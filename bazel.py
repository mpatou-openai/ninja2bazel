import logging
import os
import re
from functools import total_ordering
from typing import Dict, List, Optional, Set, Union

IncludeDir = tuple[str, bool]


class BazelCCImport:
    def __init__(self, name: str):
        self.name = name
        self.system_provided = 0
        self.hdrs: list[str] = []
        self.staticLibrary: Optional[str] = None
        self.sharedLibrary: Optional[str] = None
        self.location = ""

    def setHdrs(self, hdrs: List[str]):
        self.hdrs = hdrs

    def setSystemProvided(self):
        self.system_provided = 1

    def setStaticLibrarys(self, staticLibrary: str):
        self.staticLibrary = staticLibrary

    def setSharedLibrarys(self, sharedLibrary: str):
        self.sharedLibrary = sharedLibrary

    def setLocation(self, location: str):
        self.location = location

    def __eq__(self, other: object) -> bool:
        assert isinstance(other, BazelCCImport)
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __lt__(self, other: "BazelCCImport") -> bool:
        return self.name < other.name

    def __repr__(self) -> str:
        return f"cc_import {self.name}"

    def targetName(self) -> str:
        return f":{self.name}"

    def getGlobalImport(self) -> str:
        return ""

    def getAllHeaders(self, deps_only=False):
        # cc_import have headers but we don't include them in the upper target
        return []

    def asBazel(self) -> List[str]:
        ret = []
        ret.append("cc_import(")
        ret.append(f'    name = "{self.name}",')
        if self.system_provided:
            ret.append(f'    system_provided = "{self.system_provided}",')
            if self.sharedLibrary is not None:
                ret.append(f'    interface_library = "{self.sharedLibrary}",')
        else:
            if self.sharedLibrary is not None:
                ret.append(f'    shared_library = "{self.sharedLibrary}",')
        ret.append(f'    hdrs = "{self.hdrs}",')
        if self.staticLibrary is not None:
            ret.append(f'    static_library = "{self.staticLibrary}",')
        ret.append(")")

        return ret


class BazelBuild:
    def __init__(self: "BazelBuild"):
        self.bazelTargets: Set[Union["BaseBazelTarget", "BazelCCImport"]] = set()

    def genBazelBuildContent(self) -> Dict[str, str]:
        ret: Dict[str, str] = {}
        topContent: Dict[str, Set[str]] = {}
        tmp = {'load(":helpers.bzl", "add_bazel_out_prefix")'}
        content: Dict[str, List[str]] = {}
        lastLocation = None
        for t in sorted(self.bazelTargets):
            try:
                body = content.get(t.location, [])
                body.append(f"# Location {t.location}")
                body.extend(t.asBazel())
                content[t.location] = body
                top = topContent.get(t.location, tmp)
                top.add(t.getGlobalImport())
                topContent[t.location] = tmp
                lastLocation = t.location
            except Exception as e:
                logging.error(f"While generating Bazel content for {t.name}: {e}")
                raise
            if lastLocation is not None:
                content[lastLocation].append("")
        for k, v in topContent.items():
            top = set(filter(lambda x: x != "", v))
            if len(top) > 0:
                # Force empty line
                top.add("")
            logging.info(f"Top content is {top}")
            ret[k] = "\n".join(top)
        for k, v2 in content.items():
            ret[k] += "\n".join(v2)
        return ret


@total_ordering
class BaseBazelTarget(object):
    def __init__(self, type: str, name: str, location: str):
        self.type = type
        self.name = name
        self.location = location

    def getGlobalImport(self) -> str:
        return ""

    def __hash__(self) -> int:
        return hash(self.type + self.name)

    def __eq__(self, other: object) -> bool:
        assert isinstance(other, BaseBazelTarget)
        return self.name == other.name

    def __lt__(self, other: "BaseBazelTarget") -> bool:
        return self.name < other.name

    def addDep(self, target: Union["BaseBazelTarget", BazelCCImport]):
        raise NotImplementedError(f"Class {self.__class__} doesn't implement addDep")

    def addSrc(self, target: "BaseBazelTarget"):
        raise NotImplementedError

    def asBazel(self) -> List[str]:
        raise NotImplementedError

    def targetName(self) -> str:
        return self.name


@total_ordering
class ExportedFile(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("exports_file", name, location)

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.name == other
        if isinstance(other, ExportedFile):
            return self.name == other.name
        if isinstance(other, BazelGenRuleTargetOutput):
            return self.name == other.targetName()
        return False

    def __hash__(self) -> int:
        if self.name.startswith(":"):
            return hash(self.name[1:])
        return hash(self.name)


@total_ordering
class BazelTarget(BaseBazelTarget):
    def __init__(self, type: str, name: str, location: str):
        super().__init__(type, name, location)
        self.srcs: set[BaseBazelTarget] = set()
        self.hdrs: set[BaseBazelTarget] = set()
        self.includeDirs: set[IncludeDir] = set()
        self.deps: set[Union[BaseBazelTarget, BazelCCImport]] = set()
        self.addPrefixIfRequired: bool = True
        self.copts: List[str] = []

    def targetName(self) -> str:
        return self.depName()

    def addCopt(self, opt: str):
        self.copts.append(opt)

    def depName(self):
        if self.type == "cc_library" or self.type == "cc_shared_library":
            if not self.name.startswith("lib") and self.addPrefixIfRequired:
                name = f"lib{self.name}"
            else:
                name = self.name
            name = name.replace(".a", "")
            name = name.replace(".so", "")
        else:
            name = self.name
        return name

    def getAllHeaders(self, deps_only=False):
        if not deps_only:
            for h in self.hdrs:
                yield h
        for d in self.deps:
            try:
                yield from d.getAllHeaders()
            except AttributeError:
                logging.warn(f"Can't get headers for {d.name}")
                raise

    def addDep(self, target: Union["BaseBazelTarget", BazelCCImport]):
        self.deps.add(target)

    def addHdr(self, target: BaseBazelTarget, includeDir: Optional[IncludeDir] = None):
        self.hdrs.add(target)
        if includeDir is not None:
            self.includeDirs.add(includeDir)

    def addSrc(self, target: BaseBazelTarget):
        self.srcs.add(target)

    def __repr__(self) -> str:
        base = f"{self.type}({self.name})"
        if len(self.srcs):
            srcs = f" SRCS[{' '.join([str(s) for s in self.srcs])}]"
            base += srcs
        if len(self.hdrs):
            hdrs = f" HDRS[{' '.join([str(s) for s in self.hdrs])}]"
            base += hdrs
        if len(self.deps):
            deps = f" DEPS[{' '.join([str(d.targetName()) for d in self.deps])}]"
            base += deps
        return base

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.targetName()}",')
        deps_headers = list(self.getAllHeaders(deps_only=True))
        headers = []
        data = []
        for h in self.hdrs:
            if h not in deps_headers:
                if (
                    h.name.endswith(".h")
                    or h.name.endswith(".hpp")
                    or h.name.endswith(".tcc")
                ):
                    headers.append(h)
                else:
                    data.append(h)
        sources = [f for f in self.srcs]
        hm = {"srcs": sources, "hdrs": headers, "deps": self.deps, "data": data}

        if self.type == "cc_binary":
            del hm["hdrs"]
            sources.extend(headers)
            headers = []
        for k, v in hm.items():
            if len(v) > 0:
                ret.append(f"    {k} = [")
                for d in sorted(v):
                    pathPrefix = (
                        f"//{d.location}" if d.location != self.location else ""
                    )
                    ret.append(f'        "{pathPrefix}{d.targetName()}",')
                ret.append("    ],")
        copts = self.copts
        for dir in list(self.includeDirs):
            # The second element IncludeDir is a flag to indicate if the header is generated
            # and if so we need to add the bazel-out prefix to the -I option
            if dir[1]:
                dirName = (
                    f'add_bazel_out_prefix("{self.location + os.path.sep +dir[0]}")'
                )
            else:
                dirName = f'"{dir[0]}"'
            copts.append(f'"-I{{}}".format({dirName})')
        textOptions: Dict[str, List[str]] = {"copts": self.copts}
        for k, v2 in textOptions.items():
            if len(v2) > 0:
                ret.append(f"    {k} = [")
                for to in sorted(v2):
                    ret.append(f"        {to},")
                ret.append("    ],")
        ret.append(")")

        return ret


class BazelGenRuleTarget(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("genrule", name, location)
        self.cmd = ""
        self.outs: set[BazelGenRuleTargetOutput] = set()
        self.srcs: set[BaseBazelTarget] = set()
        self.data: set[BaseBazelTarget] = set()
        self.tools: set[BaseBazelTarget] = set()
        # We most probably don't want to do remote execution as we are running things from the
        # filesystem
        self.local: bool = True

    def addSrc(self, target: BaseBazelTarget):
        self.srcs.add(target)

    def addOut(self, name: str):
        target = BazelGenRuleTargetOutput(name, self.location, self)
        self.outs.add(target)

    def addTool(self, target: BaseBazelTarget):
        self.tools.add(target)

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        hm: Dict[str, Union[Set[BaseBazelTarget], Set[BazelGenRuleTargetOutput]]] = {
            "srcs": self.srcs,
            "outs": self.outs,
            "tools": self.tools,
        }
        len(self.outs)
        for k, v in hm.items():
            if len(v) > 0:
                ret.append(f"    {k} = [")
                for d in sorted(v):
                    pathPrefix = (
                        f"//{d.location}" if d.location != self.location else ""
                    )
                    ret.append(f'        "{pathPrefix}{d.targetName()}",')
                ret.append("    ],")
        ret.append(f'    cmd = """{self.cmd}""",')
        ret.append(f"    local = {self.local},")
        ret.append(")")

        return ret

    def getOutputs(
        self, name: str, stripedPrefix: Optional[str] = None
    ) -> List["BazelGenRuleTargetOutput"]:
        if stripedPrefix:
            name = name.replace(stripedPrefix, "")
        if name not in self.outs:
            raise ValueError(f"Output {name} didn't exists on genrule {self.name}")
        regex = r".*?/?([^/]*)\.[h|cc|cpp|hpp|c]"
        match = re.match(regex, name)
        if match:
            regex2 = rf".*?{match.group(1)}\.[h|cc|cpp|hpp|c]"
            namePrefix = match.group(1)
            outs = [v for v in self.outs if re.match(regex2, v.name)]
        else:
            outs = [v for v in self.outs if v.name == namePrefix]

        return outs


class BazelCCProtoLibrary(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("cc_proto_library", name, location)
        self.deps: Set[BaseBazelTarget] = set()

    def addDep(self, dep: Union[BaseBazelTarget, BazelCCImport]):
        assert isinstance(dep, BazelProtoLibrary)
        self.deps.add(dep)

    def getAllHeaders(self, deps_only=False):
        # FIXME
        return []

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        if len(self.deps) > 0:
            ret.append("    deps = [")
            for d in sorted(self.deps):
                pathPrefix = f"//{d.location}" if d.location != self.location else ""
                ret.append(f'        "{pathPrefix}{d.targetName()}",')
            ret.append("    ],")
        ret.append(")")

        return ret


class BazelGRPCCCProtoLibrary(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("cc_grpc_library", name, location)
        self.deps: Set[BaseBazelTarget] = set()
        self.srcs: Set[BaseBazelTarget] = set()

    def addDep(self, dep: Union[BaseBazelTarget, BazelCCImport]):
        assert isinstance(dep, BazelCCProtoLibrary)
        self.deps.add(dep)

    def addSrc(self, dep: BaseBazelTarget):
        assert isinstance(dep, BazelProtoLibrary)
        self.srcs.add(dep)

    def getAllHeaders(self, deps_only=False):
        # FIXME
        return []

    def getGlobalImport(self):
        return 'load("@com_github_grpc_grpc//bazel:cc_grpc_library.bzl", "cc_grpc_library")'

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        assert len(self.deps) > 0
        hm = {"srcs": self.srcs, "deps": self.deps}
        ret.append("    grpc_only = True,")
        for k, v in hm.items():
            if len(v) > 0:
                ret.append(f"    {k} = [")
                for d in sorted(v):
                    pathPrefix = (
                        f"//{d.location}" if d.location != self.location else ""
                    )
                    ret.append(f'        "{pathPrefix}{d.targetName()}",')
                ret.append("    ],")
        ret.append(")")

        return ret


class BazelProtoLibrary(BaseBazelTarget):
    def __init__(
        self, name: str, location: str, stripImportPrefix: Optional[str] = None
    ):
        super().__init__(
            "proto_library",
            name,
            location,
        )
        self.stripImportPrefix = stripImportPrefix
        self.srcs: Set[BaseBazelTarget] = set()
        self.deps: Set[BaseBazelTarget] = set()

    def getGlobalImport(self):
        return 'load("@rules_proto//proto:defs.bzl", "proto_library")'

    def addSrc(self, target: BaseBazelTarget):
        logging.info("addSrc called for proto_library")
        self.srcs.add(target)

    def addDep(self, target: Union[BaseBazelTarget, BazelCCImport]):
        assert isinstance(target, BaseBazelTarget)
        self.deps.add(target)

    def getAllHeaders(self, deps_only=False):
        # FIXME
        return []

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        if self.stripImportPrefix is not None:
            ret.append(f'    strip_import_prefix = "{self.stripImportPrefix}",')

        hm = {"srcs": self.srcs, "deps": self.deps}
        for k, v in hm.items():
            if len(v) > 0:
                ret.append(f"    {k} = [")
                for d in sorted(v):
                    pathPrefix = (
                        f"//{d.location}" if d.location != self.location else ""
                    )
                    ret.append(f'        "{pathPrefix}{d.targetName()}",')
                ret.append("    ],")
        ret.append(")")

        return ret


@total_ordering
class BazelGenRuleTargetOutput(BaseBazelTarget):
    def __repr__(self):
        return f"genrule_output {self.name}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.name == other
        if isinstance(other, BazelGenRuleTargetOutput):
            return self.name == other.name
        if isinstance(other, BaseBazelTarget):
            return self.name == other.name
        return False

    def __hash__(self) -> int:
        return hash(self.name)

    def __init__(
        self,
        name: str,
        location: str,
        genrule: BazelGenRuleTarget,
    ):
        super().__init__("genrule_output", f"{genrule.targetName()}_{name}", location)
        self.rule = genrule
        self.name = name

    def asBazel(self) -> List[str]:
        return self.rule.asBazel()

    def targetName(self) -> str:
        return f":{self.name}"


class PyBinaryBazelTarget(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("py_binary", name, location)
        self.main = ""
        self.srcs: set[BaseBazelTarget] = set()
        self.data: set[BaseBazelTarget] = set()

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        sources = [f for f in self.srcs]
        if len(sources) > 0:
            ret.append("    srcs = [")
            for f in sorted(sources):
                ret.append(f'        "{f.targetName()}",')
            ret.append("    ],")
        ret.append(f'    main = "{self.main}",')
        ret.append(")")

        return ret

    def addSrc(self, target: BaseBazelTarget):
        self.srcs.add(target)


class ShBinaryBazelTarget(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("sh_binary", name, location)
        self.srcs: set[BaseBazelTarget] = set()
        self.data: set[BaseBazelTarget] = set()

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        sources = [f for f in self.srcs]
        if len(sources) > 0:
            ret.append("    srcs = [")
            for f in sorted(sources):
                ret.append(f'        "{f.targetName()}",')
            ret.append("    ],")
        ret.append(")")

        return ret

    def addSrc(self, target: BaseBazelTarget):
        self.srcs.add(target)
