import logging
from dataclasses import dataclass
from typing import Any

from build import (BazelBuildVisitorContext, Build, BuildTarget, TargetType,
                   VisitorType)
from visitor import VisitorContext


@dataclass
class PrintVisitorContext(VisitorContext):
    ident: int
    output: Any

    def setup_subcontext(self) -> "VisitorContext":
        newCtx = PrintVisitorContext(**self.__dict__)
        newCtx.ident += 1
        return newCtx


class BuildVisitor:
    @classmethod
    def visitProduced(
        cls,
        ctx: BazelBuildVisitorContext,
        el: "BuildTarget",
        build: "Build",
    ):
        if build.rulename.name != "phony":
            if len(el.usedbybuilds) == 0:
                logging.warning(
                    f"Skipping non phony top level target that is not used by anything: {el}"
                )
                return
            ctx.producer = build
            rule = build.rulename
            c = rule.vars.get("command")
            assert c is not None
            c2 = build._resolveName(c, ["in", "out", "TARGET_FILE"])
            if c2 != c:
                c = c2
            arr = c.split("&&")
            found = False

            for cmd in arr:
                if build.rulename.name == "CUSTOM_COMMAND":
                    for fin in build.inputs:
                        if fin.is_a_file:
                            if fin.name in cmd:
                                found = True
                                break
                if "$in" in cmd and ("$out" in cmd or "$TARGET_FILE" in cmd):
                    found = True
                    break
            if not found and build.rulename.name != "CUSTOM_COMMAND":
                logging.warning(f"{el} has no valid command {build.inputs}")
                logging.warning(f"Didn't find a valid command in {c}")
                return
            build.handleRuleProducedForBazelGen(ctx, el, cmd)
        elif build.rulename.name == "phony":
            build.handlePhonyForBazelGen(ctx, el, build)
        elif el.type == TargetType.manually_generated:
            build.handleManuallyGeneratedForBazelGen(ctx, el, build)

    @classmethod
    def getVisitor(cls) -> VisitorType:
        def visitor(el: "BuildTarget", ctx: VisitorContext, _var: bool = False):
            assert isinstance(ctx, BazelBuildVisitorContext)
            if el.producedby is not None:
                build = el.producedby
                return BuildVisitor.visitProduced(ctx, el, build)
            # Note deal with C/C++ files only here
            else:
                return Build.handleFileForBazelGen(el, ctx)

        return visitor