import logging
import re
from functools import total_ordering
from typing import List, Set


class BazelBuild:
    def __init__(self: "BazelBuild"):
        self.bazelTargets: Set["BaseBazelTarget"] = set()

    def genBazelBuildContent(self) -> str:
        content = []
        for t in self.bazelTargets:
            try:
                content.extend(t.asBazel())
            except Exception as e:
                logging.error(f"While generating Bazel content for {t.name}: {e}")
            content.append("")
        return "\n".join(content)


@total_ordering
class BaseBazelTarget(object):
    def __init__(self, type: str, name: str):
        self.type = type
        self.name = name
        # location of the target with in the WORKSPACE
        # FIXME
        self.location = "//"

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

    def addSrc(self, filename: str):
        raise NotImplementedError

    def asBazel(self) -> List[str]:
        raise NotImplementedError

    def targetName(self) -> str:
        return self.name


@total_ordering
class BazelTarget(BaseBazelTarget):
    def __init__(self, type: str, name: str):
        super().__init__(type, name)
        self.srcs: set[str] = set()
        self.hdrs: set[str] = set()
        self.deps: set[BaseBazelTarget] = set()
        # logging.info(f"Created BazelTarget {name}/{type}")

    def targetName(self) -> str:
        return self.depName()

    def depName(self):
        if self.type == "cc_library":
            if not self.name.startswith("lib"):
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

    def addDep(self, target: "BaseBazelTarget"):
        self.deps.add(target)

    def addHdr(self, filename: str):
        self.hdrs.add(filename)

    def addSrc(self, filename: str):
        self.srcs.add(filename)

    def __repr__(self) -> str:
        base = f"{self.type}({self.name})"
        if len(self.srcs):
            srcs = f" SRCS[{' '.join(self.srcs)}]"
            base += srcs
        if len(self.hdrs):
            hdrs = f" HDRS[{' '.join(self.hdrs)}]"
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
        if self.type == "cc_binary":
            sources.extend(headers)
            headers = []
        if len(sources) > 0:
            ret.append("    srcs = [")
            for f in sorted(sources):
                ret.append(f'        "{f}",')
            ret.append("    ],")
        if len(headers) > 0:
            ret.append("    hdrs = [")
            for f in sorted(set(headers)):
                ret.append(f'        "{f}",')
            ret.append("    ],")
        if len(self.deps) > 0:
            ret.append("    deps = [")
            for d in sorted(self.deps):
                ret.append(f'        ":{d.targetName()}",')
            ret.append("    ],")
        ret.append(")")

        return ret


class BazelGenRuleTarget(BaseBazelTarget):
    def __init__(self, name: str):
        super().__init__("genrule", name)
        self.cmd = ""
        self.outs: set[str] = set()
        self.srcs: set[str] = set()
        self.data: set[str] = set()
        self.tools: set[BaseBazelTarget] = set()

    def addSrc(self, filename: str):
        self.srcs.add(filename)

    def addOut(self, target: str):
        self.outs.add(target)

    def addTool(self, target: BaseBazelTarget):
        self.tools.add(target)

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        sources = [f for f in self.srcs]
        if len(sources) > 0:
            ret.append("    srcs = [")
            for f in sorted(sources):
                ret.append(f'        "{f}",')
            ret.append("    ],")
        if len(self.outs) > 0:
            ret.append("    outs = [")
            for f in sorted(set(self.outs)):
                ret.append(f'        "{f}",')
            ret.append("    ],")
        if len(self.tools) > 0:
            ret.append("    tools= [")
            for d in sorted(self.tools):
                pathPrefix = d.location if d.location != self.location else ""
                ret.append(f'        "{pathPrefix}:{d.targetName()}",')
            ret.append("    ],")
        ret.append(f'    cmd = "{self.cmd}",')
        ret.append(")")

        return ret

    def getOutputs(self, name: str) -> List["BazelGenRuleTargetOutput"]:
        if name not in self.outs:
            raise ValueError(f"Output {name} didn't exists on genrule {self.name}")
        regex = r"(.*)\.[h|cc|cpp|hpp|c]"
        match = re.match(regex, name)
        if match:
            namePrefix = match.group(1)
            names = [v for v in self.outs if v.startswith(namePrefix)]
            return [BazelGenRuleTargetOutput(n, self) for n in names]
        else:
            return [BazelGenRuleTargetOutput(name, self)]


class BazelGenRuleTargetOutput(BaseBazelTarget):
    def __init__(self, name: str, genrule: BazelGenRuleTarget):
        super().__init__("genrule_output", f"{genrule.targetName()}_{name}")
        self.rule = genrule
        self.name = name

    def asBazel(self) -> List[str]:
        return self.rule.asBazel()

    def targetName(self) -> str:
        return f":{self.name}"


class PyBinaryBazelTarget(BaseBazelTarget):
    def __init__(self, name: str):
        super().__init__("py_binary", name)
        self.main = ""
        self.srcs: set[str] = set()
        self.data: set[str] = set()

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.name}",')
        sources = [f for f in self.srcs]
        if len(sources) > 0:
            ret.append("    srcs = [")
            for f in sorted(sources):
                ret.append(f'        "{f}",')
            ret.append("    ],")
        ret.append('    cmd = f"{self.cmd}",')
        ret.append(")")

        return ret

    def addSrc(self, filename: str):
        self.srcs.add(filename)
