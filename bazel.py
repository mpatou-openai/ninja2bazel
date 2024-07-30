from functools import total_ordering
from typing import List, Set


class BazelBuild:
    def __init__(self):
        self.bazelTargets: Set["BazelTarget"] = set()

    def genBazelBuildContent(self) -> str:
        content = []
        for t in self.bazelTargets:
            content.extend(t.asBazel())
            content.append("")
        return "\n".join(content)


@total_ordering
class BazelTarget(object):
    def __init__(self, type: str, name: str):
        self.type = type
        self.name = name
        self.srcs: List[str] = []
        self.hdrs: List[str] = []
        self.deps: List[BazelTarget] = []

    def __hash__(self) -> int:
        return hash(self.type + self.name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BazelTarget):
            return False
        return self.name == other.name

    def __lt__(self, other: "BazelTarget") -> bool:
        return self.name < other.name

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
            yield from d.getAllHeaders()

    def addDep(self, filename: "BazelTarget"):
        self.deps.append(filename)

    def addHdr(self, filename: str):
        self.hdrs.append(filename)

    def addSrc(self, filename: str):
        self.srcs.append(filename)

    def __repr__(self) -> str:
        base = f"{self.type}({self.name})"
        if len(self.srcs):
            srcs = f" SRCS[{' '.join(self.srcs)}]"
            base += srcs
        if len(self.hdrs):
            hdrs = f" HDRS[{' '.join(self.hdrs)}]"
            base += hdrs
        if len(self.deps):
            deps = f" DEPS[{' '.join([str(d.name) for d in self.deps])}]"
            base += deps
        return base

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.depName()}",')
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
                ret.append(f'        ":{d.depName()}",')
            ret.append("    ],")
        ret.append(")")

        return ret
