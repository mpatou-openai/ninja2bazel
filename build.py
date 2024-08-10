import logging
import os
import re
import sys
from enum import Enum
from functools import total_ordering
from typing import Dict, List, Optional

from bazel import (BaseBazelTarget, BazelBuild, BazelCCProtoLibrary,
                   BazelGenRuleTarget, BazelGRPCCCProtoLibrary,
                   BazelProtoLibrary, BazelTarget, ExportedFile,
                   PyBinaryBazelTarget)
from visitor import (BazelBuildVisitorContext, BuildVisitor,
                     PrintVisitorContext, VisitorContext, VisitorType)

TargetType = Enum(
    "TargetType", ["other", "unknown", "known", "external", "manually_generated"]
)


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

    def getBuildTarget(self, filename: str, parentTarget: str, produced=False) -> str:
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
        self, filename: str, parentTargetPath: str, produced=False
    ) -> str:
        if parentTargetPath == "":
            parentTargetPath = self.prefixDirectory
        pathElements = filename.split(os.path.sep)
        prefix = ""
        if produced:
            prefix = ":"
        if len(pathElements) <= 1:
            return f"{prefix}{pathElements[0]}"
        else:
            if filename.startswith(parentTargetPath) and len(parentTargetPath):
                return f"{prefix}{os.path.sep.join(pathElements[1:])}"
            else:
                # Different directory -> always return full path
                return f"//{pathElements[0]}:{os.path.sep.join(pathElements[1:])}"


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
        self.includes: Optional[List[str]] = None
        self.aliases: List[str] = []

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

    def setIncludedFiles(self, files: List[str]):
        self.includes = files

    def markAsManual(self):
        self.type = TargetType.manually_generated
        return self

    def markAsExternal(self):
        self.type = TargetType.external
        return self

    def markAsUnknown(self):
        self.type = TargetType.unknown
        return self

    def markAsknown(self):
        self.type = TargetType.known
        return self

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
            logging.debug(f"{self} is an external dependency")
            return True

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
                visitor(self, ctx, False)
            except Exception as e:
                logging.error(f"Error visiting {self.name}: {e}")
                raise
        if self.producedby:
            for el in sorted(self.producedby.inputs):
                newctx = ctx.setup_subcontext()
                el.visitGraph(visitor, newctx)
            for el in sorted(self.producedby.depends):
                if not el.depsAreVirtual():
                    newctx = ctx.setup_subcontext()
                    el.visitGraph(visitor, newctx)
        # call cleanup() to clean the context once a node has been visited
        ctx.cleanup()

    def printGraph(self, ident: int = 0, file=sys.stdout):
        def visitor(el: "BuildTarget", ctx: VisitorContext, _var: bool = False):
            assert isinstance(ctx, PrintVisitorContext)
            print(" " * ctx.ident + el.name)
            if el.producedby is None:
                return
            for d in el.producedby.depends:
                if d.producedby is None and d.type == TargetType.external:
                    print(" " * (ctx.ident + 1) + f"  {d.name} (external)")

        ctx = PrintVisitorContext(ident, file)

        self.visitGraph(visitor, ctx)

    def genBazel(self, bb: BazelBuild, rootdir: str):

        if rootdir.endswith("/"):
            dir = rootdir
        else:
            dir = f"{rootdir}/"

        ctx = BazelBuildVisitorContext(dir, bb)

        visitor = BuildVisitor.getVisitor()

        self.visitGraph(visitor, ctx)


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

    def __init__(
        self: "Build",
        outputs: List[BuildTarget],
        rulename: Rule,
        inputs: List[BuildTarget],
        depends: List[BuildTarget],
    ):
        self.outputs = outputs
        self.rulename = rulename
        self.inputs = inputs
        self.depends = set(depends)
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
    def _genExportedFile(cls, filename: str, location: str) -> ExportedFile:
        ef = cls.staticFiles.get(filename)
        if not ef:
            fileLocation = BuildFileGroupingStrategy().getBuildFilenamePath(filename)
            ef = ExportedFile(
                BuildFileGroupingStrategy().getBuildTarget(filename, location),
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
        if not ctx.dest:
            # It can happen that .o are not connected to a real library or binary but just
            # to phony targets in this case "dest" is NotImplemented
            # logging.warn(f"{el} is no connected to a non phony target")
            return
        if el.name.endswith(".h") or el.name.endswith(".hpp"):
            if isinstance(ctx.dest, BazelTarget):
                ctx.dest.addHdr(cls._genExportedFile(el.shortName, ctx.dest.location))
            else:
                logging.warn(
                    f"{el} is a header file but {ctx.dest} is not a BazelTarget that can have headers"
                )
        else:
            if el.type == TargetType.external:
                return
            # Not produced aka it's a file
            # we have to parse the file and see if there is any includes
            # if it's a "" include then we look first in the path where the file is and then
            # in the path specified with -I
            ctx.dest.addSrc(cls._genExportedFile(el.shortName, ctx.dest.location))
            if el.includes is None:
                return
            for h in el.includes:
                if isinstance(ctx.dest, BazelTarget):
                    ctx.dest.addHdr(
                        ctx.dest.addHdr(
                            cls._genExportedFile(el.shortName, ctx.dest.location)
                        )
                    )
                else:
                    logging.warn(
                        f"{el} is a header file but {ctx.dest} is not a BazelTarget that can have headers"
                    )

    @classmethod
    def handleManuallyGeneratedForBazelGen(
        cls, ctx: BazelBuildVisitorContext, el: "BuildTarget", build: "Build"
    ):
        location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
        t = BazelTarget("manually_generated_fixme", el.name, location)
        logging.info(f"handleManuallyGeneratedForBazelGen for {el.name}")
        ctx.dest = t
        ctx.bazelbuild.bazelTargets.add(t)
        if ctx.current is not None:
            ctx.current.addDep(t)

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
            for i in self.outputs:
                cmdCopy = cmdCopy.replace(i.name, "")
            arr: List[str] = list(filter(lambda x: x != "", cmdCopy.split(" ")))

            for e in arr[1:]:
                if e in self.inputs:
                    genTarget.addSrc(self._genExportedFile(e, genTarget.location))

            for elm in self.outputs:
                genTarget.addOut(
                    elm.shortName,
                )
            logging.info(
                f"Current build path for target: {TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)}"
            )
            # We don't need to handle the replacement of prefix and whatnot bazel seems to be able
            # to handle it
            cmd = cmd.replace(self.vars.get("cmake_ninja_workdir", ""), "")
            if arr[0].endswith(".py"):
                cmdTarget = PyBinaryBazelTarget(f"{name}_cmd_py", location)
                cmdTarget.main = arr[0]
                ctx.bazelbuild.bazelTargets.add(cmdTarget)
                genTarget.cmd = (
                    "./$(location ${cmdTarget.depName()}" + " " + " ".join(arr[1:])
                )
                genTarget.addTool(cmdTarget)
                for e in allInputs:
                    if e.endswith(".py"):
                        cmdTarget.addSrc(self._genExportedFile(e, genTarget.location))
            else:
                genTarget.cmd = cmd.strip()
            ctx.bazelbuild.bazelTargets.add(genTarget)
            self.setAssociatedBazelTarget(genTarget)
        else:
            tmp = self.associatedBazelTarget
            assert isinstance(tmp, BazelGenRuleTarget)
            genTarget = tmp

        outs = genTarget.getOutputs(
            el.shortName,
            TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName) + "/",
        )
        for t in outs:
            if ctx.dest is not None:
                if t.name.endswith(".h"):
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
                    tgt.addDep(t)
            ctx.next_current = t
            ctx.current = t
        else:
            ctx.current.addDep(self.associatedBazelTarget)

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
                tmp = t.deps.pop()
                assert isinstance(tmp, BazelTarget)
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
        if self.isStaticArchiveCommand(cmd):
            assert len(self.outputs) == 1
            location = TopLevelGroupingStrategy().getBuildFilenamePath(el.shortName)
            t = BazelTarget("cc_library", el.name, location)
            if ctx.current is not None:
                ctx.current.addDep(t)
            ctx.current = t
            ctx.bazelbuild.bazelTargets.add(t)
            return
        logging.warn(f"Don't know how to hande {cmd} for {el}")

    def __repr__(self) -> str:
        return (
            f"{' '.join([str(i) for i in self.inputs])} + "
            f"{' '.join([str(i) for i in self.depends])} => "
            f"{self.rulename.name} => {' '.join([str(i) for i in self.outputs])}"
        )

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
