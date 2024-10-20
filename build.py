import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from functools import total_ordering
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from bazel import (BaseBazelTarget, BazelBuild, BazelCCImport,
                   BazelCCProtoLibrary, BazelGenRuleTarget,
                   BazelGRPCCCProtoLibrary, BazelProtoLibrary, BazelTarget,
                   ExportedFile, ShBinaryBazelTarget)
from visitor import VisitorContext

VisitorType = Callable[["BuildTarget", "VisitorContext", bool], bool]
TargetType = Enum(
    "TargetType", ["other", "unknown", "known", "external", "manually_generated"]
)


@dataclass
class BazelBuildVisitorContext(VisitorContext):
    rootdir: str
    bazelbuild: BazelBuild
    current: Optional[BaseBazelTarget] = None
    dest: Optional[BaseBazelTarget] = None
    producer: Optional["Build"] = None
    next_dest: Optional[BaseBazelTarget] = None
    next_current: Optional[BaseBazelTarget] = None
    currentBuild: Optional["Build"] = None

    def __post_init__(self):
        self.parentIsPhony = False

    def setup_subcontext(self) -> "VisitorContext":
        newCtx = BazelBuildVisitorContext(**self.__dict__)
        # Never copy the next desitnation from the parent context
        newCtx.next_dest = None
        if self.next_dest is not None:
            newCtx.dest = self.next_dest
        return newCtx

    def cleanup(self):
        self.next_dest = None


class BuildFileGroupingStrategy:
    _instance = None

    def __new__(cls, *args, **kwargs):
        base = cls.__bases__[0]
        if base == object:
            base = cls

        if base._instance is None:
            base._instance = super(BuildFileGroupingStrategy, cls).__new__(cls)
        return base._instance

    def __init__(self, prefixDirectory: str = ""):
        if not hasattr(self, "initialized"):  # To prevent reinitialization
            self.prefixDirectory = prefixDirectory
            self.initialized = True

    def strategyName(self):
        return "default"

    def getBuildTarget(self, filename: str, parentTarget: str, keepPrefix=False) -> str:
        raise NotImplementedError

    def getBuildFilenamePath(self, filename: str) -> str:
        raise NotImplementedError


class TopLevelGroupingStrategy(BuildFileGroupingStrategy):
    def __init__(self, prefixDirectory: str = ""):
        super().__init__(prefixDirectory)

    def strategyName(self):
        return "TopLevelGroupingStrategy"

    def getBuildFilenamePath(self, filename: str) -> str:
        pathElements = filename.split(os.path.sep)
        if len(pathElements) <= 1:
            return ""
        else:
            return pathElements[0]

    def getBuildTarget(
        self, filename: str, parentTargetPath: str, keepPrefix=False
    ) -> str:
        if parentTargetPath == "":
            parentTargetPath = self.prefixDirectory
        pathElements = filename.split(os.path.sep)
        prefix = ":"
        if len(pathElements) <= 1:
            return f"{prefix}{pathElements[0]}"
        else:
            if filename.startswith(parentTargetPath) and len(parentTargetPath):
                return f"{prefix}{os.path.sep.join(pathElements[1:])}"
            else:
                if keepPrefix:
                    idx = 0
                else:
                    idx = 1
                # Different directory -> always return full path
                v = f":{os.path.sep.join(pathElements[idx:])}"
                return v


@total_ordering
class BuildTarget:

    def __init__(
        self,
        name: str,
        shortName: str,
        implicit: bool = False,
    ):
        self.name = name
        self.shortName = shortName
        self.implicit = implicit
        self.producedby: Optional["Build"] = None
        self.usedbybuilds: List["Build"] = []
        self.is_a_file = False
        self.type = TargetType.other
        self.includes: Optional[List[Tuple[str, str]]] = None
        self.depends: List[Union[BazelCCImport, "BuildTarget"]] = []
        self.aliases: List[str] = []
        # Is this target the first level (ie. one of the final output of the build) ?
        self.topLevel = False
        self.opaque = Optional[object]

    def markTopLevel(self):
        self.topLevel = True

    def __hash__(self) -> int:
        return self.name.__hash__()

    def __eq__(self, other) -> bool:
        if isinstance(other, BuildTarget):
            return self.name == other.name
        if isinstance(other, str):
            return self.name == other
        return False

    def __lt__(self, other) -> bool:
        return self.name < other.name

    def setIncludedFiles(self, files: List[Tuple[str, str]]):
        self.includes = files

    def setDeps(self, deps: List[Union["BuildTarget", "BazelCCImport"]]):
        self.depends = deps

    def markAsManual(self):
        self.type = TargetType.manually_generated
        return self

    def markAsExternal(self, quiet=False):
        if not quiet:
            logging.info(f"Marking {self.name} as external")
        self.type = TargetType.external
        return self

    def markAsUnknown(self):
        self.type = TargetType.unknown
        return self

    def markAsknown(self):
        self.type = TargetType.known
        return self

    def setOpaque(self, o: object):
        self.opaque = o

    def __repr__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name

    def __cmp__(self, other) -> bool:

        if self.name == other.name:
            return True
        else:
            for a in self.aliases:
                if a == other.name:
                    return True
            for a in other.aliases:
                if a == self.name:
                    return True

        return False

    def usedby(self, build: "Build") -> None:
        self.usedbybuilds.append(build)

    def markAsFile(self) -> "BuildTarget":
        self.type = TargetType.known
        self.is_a_file = True
        return self

    def isOnlyUsedBy(self, targetsName: list[str]) -> bool:
        if len(self.usedbybuilds) == 0:
            return False
        count = 0
        for e in self.usedbybuilds:
            for b in e.outputs:
                if str(b) in targetsName:
                    count += 1
        return count == len(self.usedbybuilds)

    def depsAreVirtual(self) -> bool:
        if self.is_a_file:
            logging.debug(f"{self} is a file")
            return False

        if self.producedby is None and self.type == TargetType.external:
            # logging.debug(f"{self} is an external dependency")
            return False

        if self.producedby is None and not self.is_a_file:
            logging.warning(
                f"{self.name} is a dependency for something else, is not a file"
                f" and has nothing producing it, assuming it's a virtual dependency"
            )
            return True

        for d in self.producedby.depends:
            if d.producedby and d.producedby.rulename.name == "phony":
                if len(d.producedby.inputs) == 0 and len(d.producedby.depends) == 0:
                    return True
                # Treat the case where the phony target has a ctest command as virtual
                if "/ctest " in d.producedby.vars.get("COMMAND", ""):
                    return True
                if "/ccmake " in d.producedby.vars.get("COMMAND", ""):
                    return True
                if "/cmake " in d.producedby.vars.get("COMMAND", ""):
                    return True
            v = d.depsAreVirtual()
            if not v:
                return False
        return False

    def visitGraph(self, visitor: VisitorType, ctx: VisitorContext):
        # If we are visiting a target that is a file ord
        # a target that is produced by something that is either not phony
        # of is phony but has real inputs / deps
        if self.is_a_file or not (
            self.producedby
            and self.producedby.rulename.name == "phony"
            and len(self.producedby.inputs) == 0
            and len(self.producedby.depends) == 0
        ):
            try:
                if not visitor(self, ctx, False):
                    ctx.cleanup()
                    return
            except Exception as e:
                # it might sounds wrong but producer is set a level above,
                # when looking at files producer is actually the Build that uses those files
                if ctx.producer is not None:
                    usedBy = ctx.producer.rulename.name
                else:
                    usedBy = "unknown"

                logging.error(f"Error visiting {self.name} used by {usedBy}: {e} ")
                raise
        if self.producedby:
            for el in sorted(self.producedby.inputs):
                newctx = ctx.setup_subcontext()
                newctx.producer = self.producedby
                newctx.parentIsPhony = False
                if ctx.producer is not None and ctx.producer.rulename.name == "phony":
                    newctx.parentIsPhony = True
                elif self.producedby.rulename.name == "phony" and ctx.producer is None:
                    # We don't have a producer set, this means that self is the output of topLevel
                    # build and this build is phony (ie. all)
                    builds = [b.outputs[0] for b in self.usedbybuilds]
                    logging.info(
                        f"{self.name} is phony ctx.producer = {ctx.producer}, parent build(s): {builds}"
                    )
                el.visitGraph(visitor, newctx)
            for el in sorted(self.producedby.depends):
                if not el.depsAreVirtual():
                    newctx = ctx.setup_subcontext()
                    newctx.producer = self.producedby
                    if (
                        ctx.producer is not None
                        and ctx.producer.rulename.name == "phony"
                    ):
                        newctx.parentIsPhony = True
                    else:
                        newctx.parentIsPhony = False
                    el.visitGraph(visitor, newctx)
        # call cleanup() to clean the context once a node has been visited
        ctx.cleanup()


class GeneratedBuildTarget(BuildTarget):
    pass


class Rule:
    def __init__(self, name: str):
        self.name = name
        self.vars: Dict[str, str] = {}

    def __repr__(self):
        return self.name


class Build:
    staticFiles: Dict[str, ExportedFile] = {}
    remapPaths: Dict[str, str] = {}

    def __init__(
        self: "Build",
        outputs: List[BuildTarget],
        rulename: Rule,
        inputs: List[BuildTarget],
        depends: List[BuildTarget],
    ):
        self.outputs: List[BuildTarget] = outputs
        self.rulename: Rule = rulename
        self.inputs: List[BuildTarget] = inputs
        self.depends: Set[BuildTarget] = set(depends)
        self.associatedBazelTarget: Optional[BaseBazelTarget] = None

        for o in self.outputs:
            o.producedby = self

        for i in self.inputs:
            i.usedby(self)

        for d in self.depends:
            d.usedby(self)

        self.vars: Dict[str, str] = {}

    def setAssociatedBazelTarget(self, t: BaseBazelTarget):
        self.associatedBazelTarget = t

    @classmethod
    def setRemapPaths(cls, remapPaths: Dict[str, str]):
        cls.remapPaths = remapPaths

    @classmethod
    def _genExportedFile(
        cls,
        filename: str,
        locationCaller: str,
        fileLocation: Optional[str] = None,
    ) -> ExportedFile:
        ef = cls.staticFiles.get(filename)
        if not ef:
            keepPrefix = False
            if fileLocation is None:
                fileLocation = BuildFileGroupingStrategy().getBuildFilenamePath(
                    filename
                )
            else:
                keepPrefix = True
            for k, v in cls.remapPaths.items():
                if fileLocation.startswith(k):
                    fileLocation = fileLocation.replace(k, v)
            ef = ExportedFile(
                BuildFileGroupingStrategy().getBuildTarget(
                    filename, locationCaller, keepPrefix
                ),
                fileLocation,
            )
            cls.staticFiles[filename] = ef
        return ef

    @classmethod
    def handleFileForBazelGen(
        cls,
        el: "BuildTarget",
        ctx: BazelBuildVisitorContext,
    ):
        if el.type == TargetType.external and el.opaque is None:
            logging.info(
                f"Dealing with external dep {el.name} that doesn't have an opaque"
            )
            return

        if len(el.depends) > 0:
            logging.info(f"Visiting deps for {el.name}")
            for dep in el.depends:
                logging.info(f"Visiting dep {dep}")
                if ctx.dest is not None:
                    assert isinstance(dep, BazelCCImport)
                    ctx.dest.addDep(dep)
                    ctx.bazelbuild.bazelTargets.add(dep)
        if (
            el.type == TargetType.external
            and el.opaque is not None
            and ctx.current is not None
        ):
            maybe_cc_import = el.opaque
            if isinstance(maybe_cc_import, BazelCCImport):
                ctx.current.addDep(maybe_cc_import)
                ctx.bazelbuild.bazelTargets.add(maybe_cc_import)
                return
        # logging.info(f"handleFileForBazelGen {el.name}")
        if not ctx.dest:
            # It can happen that .o are not connected to a real library or binary but just
            # to phony targets in this case "dest" is NotImplemented
            # logging.warn(f"{el} is no connected to a non phony target")
            logging.error(
                f"{el.name} is a file that is connected to a phony target {ctx.producer}, it should be filtered upstream, ignoring still"
            )
            return
        if el.name.endswith(".h") or el.name.endswith(".hpp"):
            # TODO I have the feeling that we could extrapolate the location of the header here
            # FIXME Not clear if this is ever visted
            logging.info(f"Handling generated file header {el.name}")
            if isinstance(ctx.dest, BazelTarget):
                ctx.dest.addHdr(cls._genExportedFile(el.shortName, ctx.dest.location))
            else:
                logging.warn(
                    f"{el} is a header file but {ctx.dest} is not a BazelTarget that can have headers"
                )
        elif el.name.endswith(".proto") and el.includes is None:
            logging.warn(f"{el.name} is a protobuf and includes is none")
        elif el.name.endswith(".proto") and el.includes is not None:
            ctx.dest.addSrc(cls._genExportedFile(el.shortName, ctx.dest.location))
            # Protobuf shouldn't have additional dependencies, so let's skip parsing el.deps

            # I don't remember what are the includes in the case of a protobuf
            logging.info(f"{el.name} is a protobuf and includes are {el.includes}")
            if len(el.includes) == 0:
                return
            t = BazelProtoLibrary(f"sub_{ctx.dest.name}", ctx.dest.location)
            ctx.bazelbuild.bazelTargets.add(t)
            ctx.dest.addDep(t)
            for i, d in el.includes:
                t.addSrc(
                    cls._genExportedFile(f"{d}{os.path.sep}{i}", ctx.dest.location)
                )
                stripPrefix = d.replace(ctx.dest.location + os.path.sep, "")
                t.stripImportPrefix = stripPrefix
        else:
            if el.type == TargetType.external:
                logging.info(f"Dealing with external dep {el.name} {ctx.dest}")
                return
            # Not produced aka it's a file
            # we have to parse the file and see if there is any includes
            # if it's a "" include then we look first in the path where the file is and then
            # in the path specified with -I
            ctx.dest.addSrc(cls._genExportedFile(el.shortName, ctx.dest.location))

            if el.includes is None:
                return
            incDirs = set()
            workDir = None
            if el.producedby is not None:
                workDir = el.producedby.vars.get("cmake_ninja_workdir", "")
            for i, d in el.includes:
                incDirs.add(d)
                generated = False
                if d.startswith(ctx.rootdir):
                    includeDir = d.replace(ctx.rootdir, "")
                elif d.startswith("/") and workDir is not None:
                    includeDir = d.replace(workDir, "")
                    generated = True
                elif d.startswith("/generated"):
                    generated = True
                    includeDir = d.replace("/generated", "")
                else:
                    # Maybe we should look for system headers
                    # FIXME deal with cc_imports here too
                    logging.error(f"{el.name} depends on {i} in {d}")
                    includeDir = "This is wrong"

                if isinstance(ctx.dest, BazelTarget):
                    ctx.dest.addHdr(
                        cls._genExportedFile(i, ctx.dest.location),
                        (includeDir, generated),
                    )
                else:
                    logging.warn(
                        f"{i} is a header file but {ctx.dest} is not a BazelTarget that can have headers"
                    )
            if not isinstance(ctx.dest, BazelTarget):
                return

    @classmethod
    def handleManuallyGeneratedForBazelGen(
        cls,
        ctx: BazelBuildVisitorContext,
        el: "BuildTarget",
    ):
        # We expect the manually generated target to have a : in the name
        assert ":" in el.name
        (location, target) = el.name.split(":")
        t = BazelTarget("manually_generated", target, location)
        logging.info(f"handleManuallyGeneratedForBazelGen for {el.name}")
        # We don't add the manually generated target to the list of target to generate because we
        # expect it to be well generated manually by the user
        if ctx.dest is not None:
            if el.name.endswith(".cc"):
                ctx.dest.addSrc(t)
            if el.name.endswith(".h"):
                assert isinstance(ctx.dest, BazelTarget)
                ctx.dest.addHdr(t)

    @classmethod
    def handlePhonyForBazelGen(
        cls, ctx: BazelBuildVisitorContext, el: "BuildTarget", build: "Build"
    ):
        if ctx.dest is None:
            logging.debug(f"{el} is a phony target")

    @classmethod
    def isCPPCommand(cls, cmd: str) -> bool:
        if (
            "clang" in cmd
            or "gcc" in cmd
            or "clang++" in cmd
            or "c++" in cmd
            or "g++" in cmd
        ):
            return True
        else:
            return False

    @classmethod
    def isStaticArchiveCommand(cls, cmd: str) -> bool:
        if "/ar " in cmd or "llvm-ar" in cmd:
            return True
        else:
            return False

    def _handleProtobufForBazelGen(
        self, ctx: BazelBuildVisitorContext, el: "BuildTarget", cmd: str
    ):
        assert ctx.current is not None or ctx.dest is not None
        if self.associatedBazelTarget is None:
            arr = el.name.split(os.path.sep)
            filename = arr[-1]
            # TODO use negative forward looking
            regex = r"([^.]*)(?:\.grpc)?\.pb\.(?:cc|h)"
            match = re.match(regex, filename)
            if not match:
                logging.info("not a match")
                return
            proto = match.group(1)

            location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
            t = BazelProtoLibrary(f"{proto}_proto", location)
            ctx.bazelbuild.bazelTargets.add(t)
            self.setAssociatedBazelTarget(t)

            assert el.producedby is not None

            for e in el.producedby.inputs:
                if e.name.endswith(".proto"):
                    logging.info(
                        f"Proto input for {ctx.rootdir} {el.name.replace(ctx.rootdir, '')}: {e.name.replace(ctx.rootdir, '')}"
                    )
                    path = e.name.replace(ctx.rootdir, "")
                    # .replace(location, "")
                    # if path.startswith("/"):
                    #     path = path[1:]
                    t.addSrc(self._genExportedFile(path, location))
            if ctx.dest is not None:
                ctx.dest.addSrc(t)
            elif ctx.current is not None:
                ctx.current.addDep(t)
            ctx.dest = t
            ctx.next_dest = t

        else:
            tmp = self.associatedBazelTarget
            if ctx.dest is not None:
                ctx.dest.addSrc(tmp)
            elif ctx.current is not None:
                ctx.current.addDep(tmp)
            ctx.dest = tmp
            ctx.next_dest = tmp

    def canGenerateFinal(self) -> bool:
        cmd = self.getCoreCommand()
        if cmd is None:
            return False
        if self.isCPPCommand(cmd) and self.vars.get("LINK_FLAGS") is not None:
            return True
        if self.isCPPCommand(cmd) and "-c" not in cmd:
            return True

        return False

    def _handleCustomCommandForBazelGen(
        self, ctx: BazelBuildVisitorContext, el: "BuildTarget", cmd: str
    ):
        if self.associatedBazelTarget is None:
            name = el.shortName.replace("/", "_").replace(".", "_")

            location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
            genTarget = BazelGenRuleTarget(f"{name}_command", location)

            allInputs: List[str] = []
            regex = f"^{ctx.rootdir}/?"
            for i in self.inputs:
                allInputs.append(re.sub(regex, "", i.name))
            regex = f"{ctx.rootdir}/?"
            cmd = re.sub(regex, "", cmd)
            logging.info(f"Handling custom command {cmd}")
            cmdCopy = cmd

            # This seems wrong to be doing that ... it leads to some arguments being striped of
            # their final filename
            # for i in self.outputs:
            # FIXME comment why we are doing that
            # cmdCopy = cmdCopy.replace(i.name, "")
            arr: List[str] = list(filter(lambda x: x != "", cmdCopy.split(" ")))

            for e in arr[1:]:
                if e in self.inputs:
                    genTarget.addSrc(self._genExportedFile(e, genTarget.location))
            outDirs = set()
            outFiles = set()
            workDir = self.vars.get("cmake_ninja_workdir", "")
            shortNames = [elm.shortName for elm in self.outputs]
            for elm in self.outputs:
                altName = elm.name.replace(workDir, "")

                name = elm.shortName

                if name.startswith(location + "/"):
                    name = name.replace(location + "/", "")

                if altName != name and location + "/" + altName in shortNames:
                    logging.info(f"Creating alias {name} for {altName}")
                    genTarget.addOut(altName, name)
                else:
                    outDirs.add(os.path.dirname(name))
                    outFiles.add(name)
                    genTarget.addOut(name)

            logging.info(
                f"Current build path for target: {TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)}"
            )
            # We don't need to handle the replacement of prefix and whatnot bazel seems to be able
            # to handle it
            cmd = cmd.replace(workDir, "")
            countRewrote = 0
            countInput = 0
            countOptions = 0

            alteredArgs = []
            command = arr[0]
            if command.endswith(".py"):
                lastArgIsOption = False
                for arg in arr[1:]:
                    if arg.startswith("-"):
                        # There will be an issue with options that take multiple values ie --foo bar
                        # baz biz
                        lastArgIsOption = True
                        alteredArgs.append(arg)
                        countOptions += 1
                        continue

                    found = False
                    for outFile in outFiles:
                        if outFile == os.path.basename(arg) or outFile.endswith(
                            os.path.sep + os.path.basename(arg)
                        ):
                            logging.info(f"Mapping arg: {arg} to ouput: {outFile}")
                            prefix = ":" if not outFile.startswith(":") else ""
                            alteredArgs.append(f"$(location {prefix}{outFile})")
                            countRewrote += 1
                            found = True
                            break

                    if lastArgIsOption and not found:
                        # assume that this argument is an option for the last option
                        alteredArgs.append(arg)
                        countOptions += 1
                        continue

                    lastArgIsOption = False
                    if not found:
                        if os.path.exists(f"{ctx.rootdir}/{arg}"):
                            countInput += 1
                        else:
                            logging.info(f"{arg} not found in the output hope it's ok")
                        alteredArgs.append(arg)
                # Generate a shell script to run the custom command
                buildTarget = BazelGenRuleTarget(f"{name}_cmd_build", location)
                buildTarget.addOut(f"{name}_cmd.sh")
                # Add the sha1 of all inputs to force rebuild if intput file changes

                if (countInput + countRewrote + countOptions) == len(arr[1:]):

                    def genShBinaryScript(rootdir: str, pycommand: str) -> str:
                        return f"""
echo -ne '#!/bin/bash \\n\\
#set -x\\n\\
cur=$$(pwd)\\n\\
cd {rootdir}\\n\\
# Create the symlink to the bazel-out directory\\n\\
if [ ! -e bazel-out ]; then\\n\\
    ln -s $$cur/bazel-out bazel-out\\n\\
fi\\n\\
export PYTHONPATH={rootdir}:$$PYTHONPATH\\n\\
python3 {pycommand} $$@ \\n\\
' > $@
chmod a+x $@
                """

                else:
                    logging.warn(
                        f"Need to write the function for dealing with non fully rewritten arguments for {el.name}"
                        f", {countInput}, {countRewrote}, {countOptions} {len(arr[1:])}"
                    )

                buildTarget.cmd = genShBinaryScript(ctx.rootdir, command)
                # Make a sh_binary target out of iter
                shBinary = ShBinaryBazelTarget(f"{name}_cmd", location)
                shBinary.addSrc(buildTarget)
                genTarget.cmd = (
                    f"./$(location {shBinary.targetName()})"
                    + " "
                    + " ".join(alteredArgs)
                )
                genTarget.addTool(shBinary)
                for e in allInputs:
                    if e.endswith(".py"):
                        buildTarget.addSrc(self._genExportedFile(e, genTarget.location))
                ctx.bazelbuild.bazelTargets.add(buildTarget)
                ctx.bazelbuild.bazelTargets.add(shBinary)

            else:
                genTarget.cmd = cmd.strip()
            ctx.bazelbuild.bazelTargets.add(genTarget)
            self.setAssociatedBazelTarget(genTarget)
        else:
            tmp = self.associatedBazelTarget
            assert isinstance(tmp, BazelGenRuleTarget)
            genTarget = tmp

        location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName) + "/"
        outs = genTarget.getOutputs(el.shortName, location)
        for t in outs:
            if ctx.dest is not None:
                if t.name.endswith(".h"):
                    # Figure out if we need some strip_include_prefix by matching the file
                    # with the diffrent -I flags from the command line
                    assert isinstance(ctx.dest, BazelTarget)
                    ctx.dest.addHdr(t)
                if (
                    t.name.endswith(".c")
                    or t.name.endswith(".cc")
                    or t.name.endswith(".cpp")
                ):
                    ctx.dest.addSrc(t)
            elif ctx.current is not None:
                logging.warn(f"No dest for custom command: {el}")
                [ctx.current.addDep(o) for o in outs]
        ctx.next_dest = genTarget
        ctx.current = genTarget

    def _handleGRPCCCProtobuf(self, ctx: BazelBuildVisitorContext, el: BuildTarget):
        assert ctx.current is not None
        if self.associatedBazelTarget is None:
            arr = el.name.split(os.path.sep)
            filename = arr[-1]
            proto = filename.replace(".grpc.pb.cc.o", "")

            location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
            t = BazelGRPCCCProtoLibrary(f"{proto}_cc_grpc", location)
            ctx.current.addDep(t)
            ctx.bazelbuild.bazelTargets.add(t)
            self.setAssociatedBazelTarget(t)
            for tgt in ctx.bazelbuild.bazelTargets:
                if tgt.name == f"{proto}_cc_proto":
                    t.addDep(tgt)
            ctx.next_dest = t
            ctx.dest = t
        else:
            ctx.current.addDep(self.associatedBazelTarget)
            ctx.next_dest = self.associatedBazelTarget
            ctx.dest = self.associatedBazelTarget

    def _handleCCProtobuf(self, ctx: BazelBuildVisitorContext, el: BuildTarget):
        assert ctx.current is not None
        if self.associatedBazelTarget is None:
            arr = el.name.split(os.path.sep)
            filename = arr[-1]
            proto = filename.replace(".pb.cc.o", "")

            location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
            t = BazelCCProtoLibrary(f"{proto}_cc_proto", location)
            ctx.current.addDep(t)
            ctx.bazelbuild.bazelTargets.add(t)
            self.setAssociatedBazelTarget(t)
            for tgt in ctx.bazelbuild.bazelTargets:
                if tgt.name == f"{proto}_cc_grpc":
                    assert isinstance(tgt, BaseBazelTarget)
                    tgt.addDep(t)
            ctx.next_current = t
            ctx.current = t
        else:
            ctx.current.addDep(self.associatedBazelTarget)

    def _handleCPPLinkExecutableCommand(
        self, el: BuildTarget, cmd: str, ctx: BazelBuildVisitorContext
    ):
        location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
        if self.associatedBazelTarget is None:
            t = BazelTarget("cc_binary", el.name, location)
            nextCurrent = t
            ctx.bazelbuild.bazelTargets.add(t)
            self.setAssociatedBazelTarget(t)
        else:
            tmp = self.associatedBazelTarget
            assert isinstance(tmp, BazelTarget)
            t = tmp
            nextCurrent = tmp

        if ctx.current is not None:
            ctx.current.addDep(t)
        ctx.current = nextCurrent
        return

    def _handleCPPLinkCommand(
        self, el: BuildTarget, cmd: str, ctx: BazelBuildVisitorContext
    ):
        location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
        if self.associatedBazelTarget is None:
            if self.vars.get("SONAME") is not None:
                staticLibTarget = BazelTarget(
                    "cc_library", "inner_" + el.shortName.replace("/", "_"), location
                )
                staticLibTarget.addPrefixIfRequired = False
                t = BazelTarget(
                    "cc_shared_library", el.shortName.replace("/", "_"), location
                )

                t.addPrefixIfRequired = False
                t.addDep(staticLibTarget)
                ctx.bazelbuild.bazelTargets.add(staticLibTarget)
                nextCurrent = staticLibTarget
            else:
                t = BazelTarget("cc_binary", el.name, location)
                nextCurrent = t
            ctx.bazelbuild.bazelTargets.add(t)
            self.setAssociatedBazelTarget(t)
        else:
            tmp = self.associatedBazelTarget
            assert isinstance(tmp, BazelTarget)
            t = tmp
            if t.type == "cc_shared_library":
                # First (and only ?) dep of a cc_shared_library should be a cc_library (so
                # a BaseBazelTarget
                tmp2 = t.deps.pop()
                assert isinstance(tmp2, BazelTarget)
                tmp = tmp2
            nextCurrent = tmp

        if ctx.current is not None:
            ctx.current.addDep(t)
        ctx.current = nextCurrent
        return

    def handleRuleProducedForBazelGen(
        self,
        ctx: BazelBuildVisitorContext,
        el: "BuildTarget",
        cmd: str,
    ):

        if self.rulename.name == "CUSTOM_COMMAND" and "bin/protoc" in self.vars.get(
            "COMMAND", ""
        ):
            return self._handleProtobufForBazelGen(ctx, el, cmd)
        if self.rulename.name == "CUSTOM_COMMAND":
            return self._handleCustomCommandForBazelGen(ctx, el, cmd)
        if self.isCPPCommand(cmd) and self.vars.get("LINK_FLAGS") is not None:
            self._handleCPPLinkCommand(el, cmd, ctx)
            return
        if self.isCPPCommand(cmd) and "-c" in cmd:
            if ctx.current is None:
                # Usually when it's none it's because we have pseudo targets
                return
            assert ctx.current is not None
            assert isinstance(ctx.current, BazelTarget)
            build = el.producedby
            assert build is not None
            workDir = self.vars.get("cmake_ninja_workdir", "")

            for define in self.vars.get("DEFINES", "").split(" "):
                ctx.current.addDefine(f'"{define[2:]}"')

            for flag in self.vars.get("FLAGS", "").split(" "):
                keep = True
                if flag.startswith("-D"):
                    ctx.current.addDefine(f'"{flag[2:]}"')
                    keep = False
                    continue
                if flag.startswith("-std="):
                    # Let's not keep the c++ standard flag
                    keep = False
                if flag == "-g":
                    # Let's not keep the debug flag
                    keep = False
                if flag.startswith("-O"):
                    # Let's not keep the optimization flag
                    keep = False
                if flag.startswith("-march"):
                    # Let's not keep the architecture flag
                    keep = False
                if flag.startswith("-mtune"):
                    # Let's not keep the architecture tunning flag
                    keep = False
                if flag.startswith("-fPIC"):
                    # Let's not keep the optimization flag
                    keep = False
                # Maybe some flags like -fdebug-info-for-profiling
                if keep:
                    logging.info(f"Adding flag {flag} to copt into {ctx.current.name}")
                    ctx.current.addCopt(f'"{flag}"')

            # FLAGS = -fno-semantic-interposition -fno-omit-frame-pointer -fsized-deallocation -gline-tables-only -pthread -fno-omit-frame-pointer -momit-leaf-frame-pointer -fcoroutines -gdwarf-aranges -fdebug-info-for-profiling -fno-semantic-interposition
            for i in build.inputs:
                for j in i.includes or []:
                    includeFile = j[0]
                    includeDirFull = j[1]
                    generated = False
                    if includeDirFull.startswith(workDir):
                        includeDir = includeDirFull.replace(workDir, "")
                        generated = True
                    else:
                        includeDir = includeDirFull.replace(ctx.rootdir, "")
                    logging.debug(
                        f"Adding include {includeFile} from {includeDir} generated {generated} full dir {j[1]}"
                    )
                    ctx.current.addHdr(
                        build._genExportedFile(includeFile, ctx.current.location),
                        (includeDir, generated),
                    )

            assert len(self.outputs) == 1
            if ".grpc.pb.cc.o" in self.outputs[0].name:
                self._handleGRPCCCProtobuf(ctx, el)
            elif ".pb.cc.o" in self.outputs[0].name:
                # protobuf
                self._handleCCProtobuf(ctx, el)
            else:
                ctx.dest = ctx.current
                # compilation of a source file to an object file, this is taken care by
                # bazel targets like cc_binary or cc_library
            return
        if self.isCPPCommand(cmd):
            self._handleCPPLinkExecutableCommand(el, cmd, ctx)
            return
        if self.isStaticArchiveCommand(cmd):
            assert len(self.outputs) == 1
            location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
            t = BazelTarget("cc_library", el.name, location)
            if ctx.current is not None:
                ctx.current.addDep(t)
            ctx.current = t
            ctx.bazelbuild.bazelTargets.add(t)
            return
        logging.warn(f"Don't know how to handle {cmd} for {el}")

    def __repr__(self) -> str:
        return (
            f"{' '.join([str(i) for i in self.inputs])} "
            + f"{' '.join([str(i) for i in self.depends])} => "
            f"{self.rulename.name} => {' '.join([str(i) for i in self.outputs])}"
        )

    def getRawcommand(self) -> str:
        return self.rulename.vars.get("COMMAND", "")

    def getCoreCommand(self) -> Optional[str]:
        command = self.rulename.vars.get("command")
        if command is None:
            return None
        c2 = self._resolveName(command, ["in", "out", "TARGET_FILE"])
        if c2 != command:
            command = c2
        arr = command.split("&&")
        found = False

        for cmd in arr:
            if self.rulename.name == "CUSTOM_COMMAND":
                for fin in self.inputs:
                    if fin.name.endswith("atlas_tuples.yml"):
                        logging.info(
                            f"Found atlas_tuples.yml in {cmd} {fin.name in cmd} {fin.is_a_file} pouet"
                        )
                    if fin.is_a_file:
                        if fin.name in cmd:
                            found = True
                            break
            if "$in" in cmd and ("$out" in cmd or "$TARGET_FILE" in cmd):
                found = True
                break

        if found:
            return cmd
        return None

    def _resolveName(self, name: str, exceptVars: Optional[List[str]] = None) -> str:
        regex = r"\$\{?([\w+]+)\}?"

        def replacer(match: re.Match):
            if exceptVars is not None and match.group(1) in exceptVars:
                return f"${match.group(1)}"
            # if match.group(1) == "COMMAND":
            # print(f"{self.rulename.name} {name} {self.vars}")
            v = self.vars.get(match.group(1))
            if v is None:
                v = f"${match.group(1)}"

            return v

        return re.sub(regex, replacer, name)
