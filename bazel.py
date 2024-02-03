from typing import List


class BazelBuild:
    def __init__(self):
        self.bazelTargets = []

    def genBazelBuildContent(self) -> str:
        content = []
        for t in self.bazelTargets:
            content.extend(t.asBazel())
            content.append("")
        return "\n".join(content)


class BazelTarget:
    def __init__(self, type: str, name: str):
        self.type = type
        self.name = name
        self.srcs: List[str] = []
        self.deps: List[BazelTarget] = []

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

    def addDep(self, filename: "BazelTarget"):
        self.deps.append(filename)

    def addSrc(self, filename: str):
        self.srcs.append(filename)

    def __repr__(self) -> str:
        base = f"{self.type}({self.name})"
        if len(self.srcs):
            srcs = f" SRCS[{' '.join(self.srcs)}]"
            base += srcs
        if len(self.deps):
            deps = f" DEPS[{' '.join([str(d.name) for d in self.deps])}]"
            base += deps
        return base

    def asBazel(self) -> List[str]:
        ret = []
        ret.append(f"{self.type}(")
        ret.append(f'    name = "{self.depName()}",')
        if len(self.srcs) > 0:
            ret.append("    srcs = [")
            for f in self.srcs:
                ret.append(f'        "{f}",')
            ret.append("    ],")
        if len(self.deps) > 0:
            ret.append("    deps = [")
            for d in self.deps:
                ret.append(f'        ":{d.depName()}",')
            ret.append("    ],")
        ret.append(")")

        return ret
