import logging
import re
from functools import total_ordering
from typing import Dict, List, Optional, Set, Union


class BazelBuild:
    def __init__(self: "BazelBuild"):
        self.bazelTargets: Set["BaseBazelTarget"] = set()

    def genBazelBuildContent(self) -> str:
        topContent = []
        content = []
        for t in self.bazelTargets:
            try:
                content.extend(t.asBazel())
                topContent.append(t.getGlobalImport())
            except Exception as e:
                logging.error(f"While generating Bazel content for {t.name}: {e}")
            content.append("")
        topContent = list(filter(lambda x: x != "", topContent))
        if len(topContent) > 0:
            # Force empty line
            topContent.append("")
        return "\n".join(topContent) + "\n" + "\n".join(content)


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
        if not isinstance(other, BazelTarget):
            return False
        return self.name == other.name

    def __lt__(self, other: "BaseBazelTarget") -> bool:
        return self.name < other.name

    def addDep(self, target: "BaseBazelTarget"):
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


@total_ordering
class BazelTarget(BaseBazelTarget):
    def __init__(self, type: str, name: str, location: str):
        super().__init__(type, name, location)
        self.srcs: set[BaseBazelTarget] = set()
        self.hdrs: set[BaseBazelTarget] = set()
        self.deps: set[BaseBazelTarget] = set()
        self.addPrefixIfRequired: bool = True
        # logging.info(f"Created BazelTarget {name}/{type}")

    def targetName(self) -> str:
        return self.depName()

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

    def addDep(self, target: BaseBazelTarget):
        self.deps.add(target)

    def addHdr(self, target: BaseBazelTarget):
        self.hdrs.add(target)

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
        deps_headers = self.getAllHeaders(deps_only=True)
        headers = []
        for h in self.hdrs:
            if h not in deps_headers:
                headers.append(h)
        sources = [f for f in self.srcs]
        hm = {"srcs": sources, "hdrs": headers, "deps": self.deps}
        if self.type == "cc_binary":
            sources.extend(headers)
            headers = []
        for k, v in hm.items():
            if len(v) > 0:
                ret.append(f"    {k} = [")
                for d in sorted(v):
                    pathPrefix = (
                        f"//{d.location}" if d.location != self.location else ""
                    )
                    ret.append(f'        "{pathPrefix}:{d.targetName()}",')
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
                    ret.append(f'        "{pathPrefix}:{d.targetName()}",')
                ret.append("    ],")
        ret.append(f'    cmd = "{self.cmd}",')
        ret.append(")")

        return ret

    def getOutputs(
        self, name: str, stripedPrefix: Optional[str] = None
    ) -> List["BazelGenRuleTargetOutput"]:
        if name not in self.outs:
            raise ValueError(f"Output {name} didn't exists on genrule {self.name}")
        regex = r"(.*)\.[h|cc|cpp|hpp|c]"
        match = re.match(regex, name)
        if match:
            namePrefix = match.group(1)
            outs = [v for v in self.outs if v.name.startswith(namePrefix)]
        else:
            outs = [v for v in self.outs if v.name == namePrefix]

        return outs


class BazelCCProtoLibrary(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("cc_proto_library", name, location)
        self.deps: Set[BaseBazelTarget] = set()

    def addDep(self, dep: BaseBazelTarget):
        assert isinstance(dep, BazelProtoLibrary)
        self.deps.add(dep)

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        if len(self.deps) > 0:
            ret.append("    deps = [")
            for d in sorted(self.deps):
                pathPrefix = f"//{d.location}" if d.location != self.location else ""
                ret.append(f'        "{pathPrefix}:{d.targetName()}",')
            ret.append("    ],")
        ret.append(")")

        return ret


class BazelGRPCCCProtoLibrary(BaseBazelTarget):
    def __init__(self, name: str, location: str):
        super().__init__("cc_grpc_library", name, location)
        self.deps: Set[BaseBazelTarget] = set()
        self.srcs: Set[BaseBazelTarget] = set()

    def addDep(self, dep: BaseBazelTarget):
        assert isinstance(dep, BazelCCProtoLibrary)
        self.deps.add(dep)

    def addSrc(self, dep: BaseBazelTarget):
        assert isinstance(dep, BazelProtoLibrary)
        self.srcs.add(dep)

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
                    ret.append(f'        "{pathPrefix}:{d.targetName()}",')
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
        self.srcs.add(target)

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
                    ret.append(f'        "{pathPrefix}:{d.targetName()}",')
                ret.append("    ],")
        ret.append(")")

        return ret


@total_ordering
class BazelGenRuleTargetOutput(BaseBazelTarget):
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
        ret.append('    cmd = f"{self.cmd}",')
        ret.append(")")

        return ret

    def addSrc(self, target: BaseBazelTarget):
        self.srcs.add(target)
