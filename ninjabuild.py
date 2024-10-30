import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

from bazel import BazelBuild, BazelCCImport
from build import (Build, BuildTarget, Rule, TargetType,
                   TopLevelGroupingStrategy)
from build_visitor import (BazelBuildVisitorContext, BuildVisitor,
                           PrintVisitorContext)
from cppfileparser import CPPIncludes, findCPPIncludes, parseIncludes
from helpers import resolvePath
from protoparser import findProtoIncludes
from visitor import VisitorContext

IGNORED_STANZA = [
    "ninja_required_version",
    "default",
]
IGNORED_TARGETS = [
    "edit_cache",
    "rebuild_cache",
    "clean",
    "help",
    "install",
    "build.ninja",
    "list_install_components",
    "install/local",
    "install/strip",
]


def _copyFilesBackNForth(sourceDir, destDir):
    # Ensure the destination directory exists, create it if it doesn't
    if not os.path.exists(destDir):
        os.makedirs(destDir, exist_ok=True)

    # Iterate over all the files and directories in the cache directory
    for item in os.listdir(sourceDir):
        source_path = os.path.join(sourceDir, item)
        destination_path = os.path.join(destDir, item)

        # Check if it is a file or directory and copy accordingly
        if os.path.isdir(source_path):
            # Copy the directory and its contents
            shutil.copytree(source_path, destination_path, dirs_exist_ok=True)
        else:
            # Copy the file
            shutil.copy2(source_path, destination_path)


def isCPPLikeFile(name: str) -> bool:
    for e in [".cc", ".cpp", ".h", ".hpp"]:
        if name.endswith(e):
            return True

    return False


def isProtoLikeFile(name: str) -> bool:
    for e in [".proto"]:
        if name.endswith(e):
            return True
    return False


class NinjaParser:
    def __init__(self, codeRootDir: str):
        self.generatedFiles: Dict[str, Any] = {}
        self.missingFiles: Dict[str, List[Build]] = {}
        self.codeRootDir = codeRootDir
        self.buildEdges: List[Build] = []
        self.currentBuild: Optional[List[str]] = None
        self.currentVars: Optional[Dict[str, str]] = None
        self.currentRule: Optional[List[str]] = None
        self.buffer: List[str] = []
        self.all_outputs: Dict[str, BuildTarget] = {}
        self.missing: Dict[str, Any] = {}
        self.vars: Dict[str, Dict[str, str]] = {}
        self.rules = {}
        self.rules["phony"] = Rule("phony")
        self.directories: List[str] = []
        self.headers_files: Dict[str, Any] = {}
        self.contexts: List[str] = []
        self.currentContext: str = ""
        self.initialDirectory = ""
        self.ran: Set[Tuple[str, str]] = set()
        self.externals: Dict[str, BuildTarget] = {}
        self.cacheHeaders: Dict[str, CPPIncludes] = {}
        self.generatedFilesLogged: Set[Tuple[str, str]] = set()

    def getShortName(self, name, workDir=None):
        if name.startswith(self.codeRootDir):
            return name[len(self.codeRootDir) :]
        if workDir is not None:
            s = workDir
            if not s.endswith(os.path.sep):
                s += os.path.sep
        else:
            s = self.vars[self.currentContext].get("cmake_ninja_workdir", "")

        # TODO find a way to for generated files to figure out the best prefix
        if len(s) > 0 and name.startswith(s):
            offset = len(s)
            if not s.endswith(os.path.sep):
                offset += 1
            ret = f"{self.initialDirectory}{name[offset:]}"
            return ret
        if self.initialDirectory != "" and name[0] != os.path.sep:
            ret = f"{self.initialDirectory}{name}"
            return ret
        return name

    def setDirectoryPrefix(self, initialDirectoryPrefix: str):
        self.initialDirectory = initialDirectoryPrefix

    def setContext(self, contextName: str):
        self.contexts.append(contextName)
        self.currentContext = contextName
        self.vars[contextName] = {}

    def setRemapPath(self, remapPaths: Dict[str, str]):
        self.remapPaths = remapPaths
        Build.setRemapPaths(remapPaths)

    def endContext(self, contextName: str):
        assert self.contexts[-1] == contextName
        self.contexts.pop()
        if len(self.contexts):
            self.currentContext = self.contexts[-1]
        else:
            self.currentContext = ""

    def markDone(self):
        # What ever we had so far, we mark at finished
        if self.currentBuild is not None:
            self._handleBuild(self.currentBuild, self.currentVars or {})
            self.currentVars = {}
            self.currentBuild = None

        if self.currentRule is not None:
            self._handleRule(self.currentRule, self.currentVars or {})
            self.currentVars = {}
            self.currentRule = None

    def _resolveName(
        self, name: str, additionalVars: Optional[Dict[str, str]] = None
    ) -> str:
        regex = r"\$\{?([\w+]+)\}?"

        def replacer(match: re.Match):
            if additionalVars is not None:
                v = additionalVars.get(match.group(1))
            if v is None:
                v = self.vars[self.currentContext].get(match.group(1))
            if v is None:
                v = match.group(1)

            return v

        return re.sub(regex, replacer, name)

    def _handleRule(self, arr: List[str], vars: Dict[str, str]):
        rule = Rule(arr[1])
        self.rules[rule.name] = rule
        rule.vars = vars

    def _handleBuild(self, arr: List[str], vars: Dict[str, str]):
        """
        Handle a build line materialized in the @arr list:
        * First argument is 'build'
        * the target the command to build it ie. phony or
        CUSTOM_COMMAND
        * dependencies until the end of the array or the element ||
        * ordering only dependencies
        """
        arr.pop(0)
        outputs: List[BuildTarget] = []
        implicit = False
        shouldbreak = False
        # raw input is what is after the first : and before | or || (if any)
        # raw_depends is what is after the first : and |
        raw_inputs: List[str] = []
        raw_depends: List[str] = []
        raw_non_built_depends: List[str] = []
        for i in range(len(arr)):
            e = arr[i]
            if e == "|":
                implicit = True
                continue
            val = e
            if e.endswith(":"):
                shouldbreak = True
                val = e[:-1]
            tmp = self._resolveName(val, vars)
            # logging.info(f"Adding {val} as an output, resolved as {tmp}")
            val = tmp

            outputs.append(
                BuildTarget(
                    val,
                    self.getShortName(val),
                    implicit,
                )
            )
            if shouldbreak:
                break

        # Would be a better idea to associate generated target with a prefix that is based of the
        # prefix of its inputs
        i += 1
        rulename = arr[i]
        i += 1
        target = raw_inputs
        for j in range(i, len(arr)):
            if arr[j] == "||":
                target = raw_non_built_depends
                continue
            if arr[j] == "|":
                target = raw_depends
                continue
            target.append(arr[j])

        # TODO we need to do something with the order only deps
        # More often than not they provide headers
        if rulename == "phony":
            if len(raw_inputs) == 0:
                newraw_depends = []
                for d in raw_depends:
                    if not os.path.isdir(f"{d}"):
                        newraw_depends.append(d)

                raw_depends = newraw_depends

        inputs = []
        for s in raw_inputs:
            if not s.startswith("/"):
                p = f"{s}"
            else:
                p = s
            if os.path.exists(p):
                # Using realPath leads to issues when the file is symlinked to outside of the
                # build environment
                # realPath = os.path.realpath(p)
                realPath = resolvePath(p)
                if (
                    realPath[0] != "/"
                    or realPath.startswith(
                        self.vars[self.currentContext].get(
                            "cmake_ninja_workdir", "donotexistslalala"
                        )
                    )
                    or realPath.startswith(self.codeRootDir)
                ):
                    logging.debug(f"Marking {s} as an known dependency")
                    inputs.append(BuildTarget(s, self.getShortName(s)).markAsFile())
                else:
                    ext = self.externals.get(s)
                    if s in self.manually_generated:
                        logging.info(f"Marking {s} as a manually generated target")
                        m = self.manually_generated[s]
                        mv = BuildTarget(m, self.getShortName(m))
                        mv.markAsManual()
                        inputs.append(mv)
                        continue
                    elif ext is None:
                        logging.debug(
                            f"Marking {s} as an external dependency {realPath}"
                        )
                        quiet = False
                        if s.endswith("CMakeLists.txt"):
                            quiet = True
                        ext = BuildTarget(s, self.getShortName(s)).markAsExternal(quiet)
                    self.externals[s] = ext
                    inputs.append(ext)
            # Massive hack: assume that files ending with .c/cc with a folder name third-party
            # exists
            elif (p.endswith(".c") or p.endswith(".cc") or p.endswith(".cpp")) and (
                "third-party" in p
            ):
                inputs.append(BuildTarget(s, self.getShortName(s)).markAsFile())
            else:
                v = self.all_outputs.get(s)
                if not v:
                    v = BuildTarget(s, self.getShortName(s))
                    if s in self.manually_generated:
                        logging.info(f"Marking {s} as a manually generated target")
                        m = self.manually_generated[s]
                        v = BuildTarget(m, self.getShortName(m))
                        v.markAsManual()
                    else:
                        v.markAsUnknown()
                        self.missing[s] = v
                inputs.append(v)

        depends = []
        for d in raw_depends:
            v = self.all_outputs.get(d)
            if not v:
                try:
                    v = BuildTarget(d, self.getShortName(d))
                except Exception as _:
                    logging.error("Couldn't find a target for {d}")
                    raise
                imp = self.getCCImportForExternalDep(v)
                v.setOpaque(imp)
                quiet = imp is not None
                if v.name.endswith("CMakeLists.txt") or v.name.endswith(".cmake"):
                    quiet = True
                v.markAsExternal(quiet)
            depends.append(v)

        # Reconsile targets generated by outputs with missing dependencies
        for i in range(len(outputs)):
            o = outputs[i]
            t = self.missing.get(str(o))
            if t is not None:
                # We need to reconcile t and o
                del self.missing[str(o)]
                outputs[i] = t
                t.markAsknown()
                o = t
            self.all_outputs[str(o)] = o
        stillMissing = list(self.missing.keys())
        # Hack: ignore dirs
        for m in stillMissing:
            if m.endswith(".dir"):
                del self.missing[m]

        rule = self.rules.get(rulename)
        if rule is None:
            logging.error(f"Coulnd't find a rule called {rulename}")
            return
        build = Build(outputs, rule, inputs, depends)
        for k2, v2 in self.vars[self.currentContext].items():
            build.vars[k2] = v2

        build.vars.update(build.rulename.vars)
        build.vars.update(vars)

        self.buildEdges.append(build)

    def handleVariable(self, name: str, value: str):
        self.vars[self.currentContext][name] = value
        logging.debug(f"Var {name} = {self.vars[self.currentContext][name]}")

    def handleInclude(self, arr: List[str]):
        dir = self.directories[-1]
        filename = f"{dir}{os.path.sep}{arr[0]}"
        with open(filename, "r") as f:
            raw_ninja = f.readlines()

        cur_dir = os.path.dirname(os.path.abspath(filename))
        self.parse(raw_ninja, cur_dir)

    def executeGenerator(self, build: Build, target: BuildTarget):
        tempDir = tempfile.mkdtemp()
        subDir = self.codeRootDir.replace("/", "_")
        cacheDirBase = f"{os.environ['HOME']}/.cache/ninja2bazel/{subDir}"
        os.makedirs(cacheDirBase, exist_ok=True)

        coreRet = build.getCoreCommand()
        outputs = set()
        workDir = build.vars.get("cmake_ninja_workdir", "")
        for o in build.outputs:
            outputs.add(o.name.replace(workDir, ""))

        if coreRet is None:
            if " cp " in build.vars.get(
                "COMMAND", ""
            ) or "/bin/cmake" in build.vars.get("COMMAND", ""):
                logging.debug(
                    f"Command for {target.name}: {build.vars.get('COMMAND')} is not a \"core\" one"
                )
            return
        cmd, runDir = coreRet
        cmd = cmd.strip()
        if cmd.startswith("cp "):
            return

        cmd = cmd.strip()
        os.environ["PYTHONPATH"] = (
            os.environ.get("PYTHONPATH", "") + ":" + self.codeRootDir
        )
        exe = cmd.split(" ")
        if exe[0].endswith("/protoc"):
            for f in outputs:
                self.generatedFiles[f] = build
                pass
            # Should generate empty files
            # skip protoc
            return
        if exe[0].endswith(".py"):
            cmd = f"python3 {cmd}"

        if (cmd, workDir) in self.ran:
            return
        else:
            self.ran.add((cmd, workDir))
        cwd = os.getcwd()
        os.chdir(tempDir)

        if runDir is not None:
            cmd = f"mkdir -p {runDir} && cd {runDir} && {cmd}"

        sha1cmd = hashlib.sha1()
        sha1cmd.update(cmd.encode())
        sha1 = sha1cmd.hexdigest()

        # We want to hash first before replacing workdir by tempdir
        cmd = re.sub(rf"{workDir}", f"{tempDir}/", cmd)

        cacheDir = f"{cacheDirBase}/{sha1}"
        if os.path.exists(cacheDir):
            logging.info(f"Using cache for {cmd} SHA1:{sha1}")
            _copyFilesBackNForth(cacheDir, tempDir)
        else:
            logging.info(f"Running in {tempDir} {cmd} SHA1:{sha1}")
            res = subprocess.run(cmd, shell=True)
            if res.returncode != 0:
                logging.warn(f"Got an exception when trying to run {cmd} in {tempDir}")
                return

            _copyFilesBackNForth(tempDir, cacheDir)

        os.chdir(cwd)
        return tempDir

    def getCCImportForExternalDep(self, target: BuildTarget) -> Optional[BazelCCImport]:
        logging.debug(f"Checking {target.name} as part of CCimport")
        for imp in self.cc_imports:
            # logging.info(f"Dealing with {imp} {imp.staticLibrary}")
            if target.name.endswith(".a") and imp.staticLibrary is not None:
                # logging.info(f"Looking for {target.name} in {imp}")
                if target.name in imp.staticLibrary:
                    return imp
            if target.name.endswith(".so") and imp.sharedLibrary is not None:
                if target.name in imp.sharedLibrary:
                    # logging.info(f"Looking for {target.name} in {imp}")
                    return imp
        return None

    def finiliazeHeadersForFile(
        self,
        target: BuildTarget,
        f: str,
        fileFolder: str,
        tempTopFolder: str,
        debug: bool = False,
    ):
        build = target.producedby
        if not build:
            return
        workDir = build.vars.get("cmake_ninja_workdir", "")
        if workDir.endswith(os.path.sep):
            workDir = workDir[:-1]
        # logging.info(f"Found {f} generated file")
        if isCPPLikeFile(f):
            if self.cacheHeaders.get(f):
                logging.debug(f"Already processed {f}")
                return
            else:
                logging.debug(f"Processing {f}")

            includes_dirs: Set[str] = set()
            for b in target.usedbybuilds:
                includes = b.vars.get("INCLUDES", "")
                if includes != "":
                    # Find the first build where we have includes and
                    # for those include replace workDir by the tempTopFolder
                    # so that we have a chance of finding our generated headers

                    includes_dirs = parseIncludes(includes)
                    updated_include_dirs = []
                    for dir in includes_dirs:
                        if dir.startswith(workDir):
                            updated_include_dirs.append(
                                dir.replace(workDir, tempTopFolder)
                            )
                            # We want to clobber the "/" at the end so that
                            # the constructed path looks nothing like a real
                            # path and more something like /generatedinclude
                            updated_include_dirs.append(
                                dir.replace(workDir + "/", "/generated")
                            )
                        else:
                            updated_include_dirs.append(dir)

                    includes_dirs = set(updated_include_dirs)

                    break
            if includes is None:
                logging.warn(
                    f"No includes for {target.name} using cmd {build.getCoreCommand()}"
                )
                includes = ""
            cppIncludes = findCPPIncludes(
                os.path.sep.join([fileFolder, f]),
                includes_dirs,
                self.compilerIncludes,
                self.cc_imports,
                self.generatedFiles,
            )
            if len(cppIncludes.notFoundHeaders) > 0 and includes != "":
                logging.warning(
                    f"Couldn't find {cppIncludes.notFoundHeaders} headers for generated file {f}"
                )
            if debug:
                logging.info(f"For file {f} found headers {cppIncludes.foundHeaders}")
            for i in build.outputs:
                if not (i.name.endswith(f) and len(cppIncludes.foundHeaders) > 0):
                    # Why ?
                    continue
                logging.debug(
                    f"Setting headers for {i.name} {len(cppIncludes.foundHeaders)}"
                )
                allIncludes = []
                for h in list(cppIncludes.foundHeaders):
                    name = self.getShortName(
                        h[0].replace(tempTopFolder, workDir), workDir
                    )
                    includeDir = h[1].replace(tempTopFolder, workDir)
                    allIncludes.append((name, includeDir))
                # We make the decision to not deal with generated files that are needed by other
                # generated files
                for h in list(cppIncludes.neededGeneratedFiles):
                    logging.info(
                        f"Not adding {h[0]} to the list of includes because we are dealing with a generated file"
                    )

                i.setIncludedFiles(allIncludes)
                i.setDeps(list(cppIncludes.neededImports))
            self.cacheHeaders[f] = cppIncludes

    def _finalizeHeadersForNonGeneratedFiles(self, current_dir: str):
        for t in self.all_outputs.values():
            build = t.producedby
            if not build:
                continue
            workDir = build.vars.get("cmake_ninja_workdir", "")
            generatedOutputsNeeded = set()

            for i in build.inputs:
                if i.is_a_file and isCPPLikeFile(i.name):
                    includes_dirs = parseIncludes(build.vars.get("INCLUDES", ""))
                    updated_include_dirs = []
                    for dir in includes_dirs:
                        if dir.startswith(workDir):
                            updated_include_dirs.append(
                                dir.replace(workDir, "/generated")
                            )
                        else:
                            updated_include_dirs.append(dir)

                    includes_dirs = set(updated_include_dirs)
                    cppIncludes = findCPPIncludes(
                        i.name,
                        includes_dirs,
                        self.compilerIncludes,
                        self.cc_imports,
                        self.generatedFiles,
                    )
                    if len(cppIncludes.notFoundHeaders) > 0:
                        for h in cppIncludes.notFoundHeaders:
                            if h in self.generatedFiles:
                                if h in self.generatedFilesLogged:
                                    logging.info(
                                        f"Found missing header {h} in the generated files"
                                    )
                            else:
                                if h not in self.missingFiles:
                                    self.missingFiles[h] = []
                                self.missingFiles[h].append(build)
                    allIncludes = [
                        (self.getShortName(h[0], workDir), h[1])
                        for h in list(cppIncludes.foundHeaders)
                    ]
                    i.setIncludedFiles(allIncludes)
                    i.setDeps(list(cppIncludes.neededImports))
                    # Add the builds that produce generated files to the current build
                    # for the current build

                    for h2 in list(cppIncludes.neededGeneratedFiles):
                        if h2 not in self.generatedFilesLogged:
                            logging.info(f"Needed generated include file {h2}")
                            self.generatedFilesLogged.add(h2)
                        for bld in self.generatedFiles[h2[0]].outputs:
                            includeDir = h2[1]
                            if bld.name == h2[0]:
                                # It's a bit weird that we "self" include ourselve
                                # but that's the only simple way to keep track of the needed
                                # includeDir
                                # bld.includes.add((h2[0], includeDir))
                                generatedOutputsNeeded.add(bld)
                                i.addIncludedFile((h2[0], includeDir))
                if i.is_a_file and isProtoLikeFile(i.name):
                    includes_dirs = set()
                    for part in build.vars.get("COMMAND", "").split("&&"):
                        if "/protoc" in part:
                            regex = r"-I ([^ ]+)"
                            matches = re.findall(regex, part)
                            includes_dirs.update(matches)

                    protos = findProtoIncludes(i.name, includes_dirs)
                    includesFiles = []
                    for p in protos:
                        f = self.getShortName(p[0], workDir)
                        d = self.getShortName(p[1], workDir)
                        f = f.replace(d + os.path.sep, "")
                        includesFiles.append((f, d))
                    i.setIncludedFiles(includesFiles)
            # TODO revisit if we need to extend the inputs o: the dependencies of the build
            # dependencies might have a side effect that is not desirable for generated files
            build.inputs.extend(list(generatedOutputsNeeded))

    def _finalizeHeadersForGeneratedFiles(self, current_dir: str):
        for t in self.all_outputs.values():
            build = t.producedby
            if not build:
                continue
            if build.rulename.name == "CUSTOM_COMMAND":
                ret = self.executeGenerator(build, t)
                if ret is None:
                    continue
                for dirpath, dirname, files in os.walk(ret):
                    for f in files:
                        relative_file = f"{dirpath}/{f}".replace(f"{ret}/", "")
                        # store the filename to build association
                        self.generatedFiles[relative_file] = build
                        self.finiliazeHeadersForFile(t, f, dirpath, ret, False)
                try:
                    shutil.rmtree(ret)
                except Exception as _:
                    logging.warn(f"Couldn't remove {ret}")
                    pass

    def finalizeHeaders(self, current_dir: str):
        # We might want to iterate twice on the values,
        # the first time we might want to get the builds that are custom commands because they are
        # supposed to generate files that are used by other builds
        self._finalizeHeadersForGeneratedFiles(current_dir)
        self._finalizeHeadersForNonGeneratedFiles(current_dir)

    def setManuallyGeneratedTargets(self, manually_generated: Dict[str, str]):
        self.manually_generated = manually_generated

    def parse(
        self,
        content: List[str],
        current_dir: str,
    ):
        self.directories.append(current_dir)
        for line in content:
            line = line.rstrip()
            if line.startswith("#"):
                continue

            if line.endswith("$"):
                self.buffer.append(line[:-1])
                continue

            if len(self.buffer) > 0:
                self.buffer.append(line)
                newline = "".join(self.buffer)
                self.buffer = []
                line = newline

            if len(line) == 0:
                self.markDone()
                continue

            arr = re.split(r" (?!$)", line)

            if arr[0] == "rule":
                self.currentRule = arr
                continue

            if arr[0] == "build":
                self.currentBuild = arr
                continue

            if line.startswith(" "):
                line = line.strip()
                for i in range(len(line)):
                    if (
                        line[i] == "="
                        and i > 1
                        and line[i - 1] == " "
                        and line[i - 2] != "$"
                    ):
                        key = line[:i].strip()
                        value = line[i + 1 :].strip()
                        if self.currentVars is None:
                            self.currentVars = {}
                        self.currentVars[key] = value
                        break
                continue

            if arr[0] in IGNORED_STANZA:
                continue

            if arr[1] == "=":
                self.handleVariable(arr[0], " ".join(arr[2:]))
                continue

            if arr[0] == "include":
                self.handleInclude(arr[1:])
                continue

            logging.debug(f"{line} {len(line)}")
        self.directories.pop()

    def setCCImports(self, cc_imports: List[BazelCCImport]):
        self.cc_imports = cc_imports

    def setCompilerIncludes(self, compilerIncludes: List[str]):
        self.compilerIncludes = compilerIncludes


def getToplevels(parser: NinjaParser) -> List[BuildTarget]:
    real_top_targets = []
    for o in parser.all_outputs.values():
        if o.isOnlyUsedBy(["all"]):
            real_top_targets.append(o)
            # logging.debug(f"{o} produced by {o.producedby.rulename}")
            continue
        if str(o) in IGNORED_TARGETS or o.isOnlyUsedBy(IGNORED_TARGETS):
            continue
        if o.producedby is not None and o.producedby.rulename.name == "phony":
            # Look at all the phony build outputs
            # if all their inputs are used in another build then it's kind of an alias
            # and so it's not a top level build
            count = 0
            for i in o.producedby.inputs:
                if len(i.usedbybuilds) != 0:
                    count += 1
            if count == len(o.producedby.inputs):
                continue
        if (
            len(o.usedbybuilds) == 0
            and not o.implicit
            and not str(o).endswith("_tests.cmake")
        ):
            logging.warning(f"{o} used by no one")
            # logging.debug(f"{o} produced by {o.producedby.rulename.name}")
            real_top_targets.append(o)

    return real_top_targets


def _printNiceDict(d: dict[str, Any]) -> str:
    return "".join([f"  {k}: {v}\n" for k, v in d.items()])


def genBazel(buildTarget: BuildTarget, bb: BazelBuild, rootdir: str):

    if rootdir.endswith("/"):
        dir = rootdir
    else:
        dir = f"{rootdir}/"

    ctx = BazelBuildVisitorContext(False, dir, bb)

    visitor = BuildVisitor.getVisitor()

    buildTarget.visitGraph(visitor, ctx)


def getBuildTargets(
    raw_ninja: List[str],
    dir: str,
    ninjaFileName: str,
    manuallyGenerated: Dict[str, str],
    codeRootDir: str,
    directoryPrefix: str,
    remap: Dict[str, str],
    cc_imports: List[BazelCCImport],
    compilerIncludes: List[str],
) -> List[BuildTarget]:
    TopLevelGroupingStrategy(directoryPrefix)

    parser = NinjaParser(codeRootDir)
    parser.setManuallyGeneratedTargets(manuallyGenerated)
    parser.setContext(ninjaFileName)
    parser.setRemapPath(remap)
    parser.setDirectoryPrefix(directoryPrefix)
    parser.setCompilerIncludes(compilerIncludes)
    parser.setCCImports(cc_imports)
    parser.parse(raw_ninja, dir)
    logging.info("Parsing done")
    parser.endContext(ninjaFileName)

    if len(parser.missing) != 0:
        logging.error(
            f"Something is wrong there is {len(parser.missing)} missing dependencies:\n {_printNiceDict(parser.missing)}"
        )
        return []

    top_levels = getToplevels(parser)
    parser.finalizeHeaders(dir)
    return top_levels


def printGraph(element: BuildTarget, ident: int = 0, file=sys.stdout):
    def visitor(el: "BuildTarget", ctx: VisitorContext, _var: bool = False):
        assert isinstance(ctx, PrintVisitorContext)
        print(" " * ctx.ident + el.name)
        if el.producedby is None:
            return
        for d in el.producedby.depends:
            if d.producedby is None and d.type == TargetType.external:
                print(" " * (ctx.ident + 1) + f"  {d.name} (external)")


    ctx = PrintVisitorContext(ident, file) # type: ignore

    element.visitGraph(visitor, ctx)


def genBazelBuildFiles(
    top_levels: list[BuildTarget], rootdir: str, prefix: str
) -> Dict[str, str]:
    bb = BazelBuild(prefix)
    for e in sorted(top_levels):
        e.markTopLevel()
        genBazel(e, bb, rootdir)

    return bb.genBazelBuildContent()
