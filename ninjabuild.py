import logging
import os
import re
from typing import Any, Dict, List, Optional

from bazel import BazelBuild
from build import Build, BuildTarget, Rule, TopLevelGroupingStrategy
from cppfileparser import findCPPIncludes

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


class NinjaParser:
    def __init__(self, codeRootDir: str):
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

    def getShortName(self, name):
        if name.startswith(self.codeRootDir):
            return name[len(self.codeRootDir) :]
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
                realPath = os.path.realpath(p)
                if (
                    realPath[0] != "/"
                    or realPath.startswith(
                        self.vars[self.currentContext].get(
                            "cmake_ninja_workdir", "donotexistslalala"
                        )
                    )
                    or realPath.startswith(self.codeRootDir)
                ):
                    logging.info(f"Marking {s} as an known dependency")
                    inputs.append(BuildTarget(s, self.getShortName(s)).markAsFile())
                else:
                    logging.info(f"Marking {s} as an external dependency")
                    inputs.append(BuildTarget(s, self.getShortName(s)).markAsExternal())
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
                        v = BuildTarget(s, self.getShortName(s))
                        v.markAsManual()
                    else:
                        v.markAsUnknown()
                        self.missing[s] = v
                inputs.append(v)

        depends = []
        for d in raw_depends:
            v = self.all_outputs.get(d)
            if not v:
                v = BuildTarget(d, self.getShortName(s))
                v.markAsExternal()
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

    def finalizeHeaders(self, current_dir: str):
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

        for t in self.all_outputs.values():
            if not t.producedby:
                continue
            for i in t.producedby.inputs:
                if i.is_a_file and isCPPLikeFile(i.name):
                    includes = t.producedby.vars.get("INCLUDES", "")
                    headers = findCPPIncludes(i.name, includes)
                    i.setIncludedFiles(headers)
                if i.is_a_file and isProtoLikeFile(i.name):
                    pass

    def setManuallyGeneratedTargets(self, manually_generated: Optional[List[str]]):
        self.manually_generated = manually_generated or []

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


def getBuildTargets(
    raw_ninja: List[str],
    dir: str,
    ninjaFileName: str,
    manuallyGenerated: Optional[List[str]],
    codeRootDir: str,
    directoryPrefix: str,
) -> List[BuildTarget]:
    parser = NinjaParser(codeRootDir)
    parser.setManuallyGeneratedTargets(manuallyGenerated)
    parser.setContext(ninjaFileName)
    parser.setDirectoryPrefix(directoryPrefix)
    TopLevelGroupingStrategy(directoryPrefix)
    parser.parse(raw_ninja, dir)
    parser.endContext(ninjaFileName)

    if len(parser.missing) != 0:
        logging.error(
            f"Something is wrong there is {len(parser.missing)} missing dependencies:\n {_printNiceDict(parser.missing)}"
        )
        return []

    top_levels = getToplevels(parser)
    parser.finalizeHeaders(dir)
    return top_levels


def genBazelBuildFiles(top_levels: list[BuildTarget], rootdir: str) -> str:
    bb = BazelBuild()
    for e in sorted(top_levels):
        e.genBazel(bb, rootdir)

    return bb.genBazelBuildContent()
