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
            cmd = build.getCoreCommand()
            if cmd is None and build.rulename.name != "CUSTOM_COMMAND":
                logging.warning(f"{el} has no valid command {build.inputs}")
                logging.warning(
                    f"Didn't find a valid command in {build.getRawcommand()}"
                )
                return
            assert cmd is not None
            build.handleRuleProducedForBazelGen(ctx, el, cmd)
        elif build.rulename.name == "phony":
            logging.info(f"Handling phony {build.outputs[0]}")
            build.handlePhonyForBazelGen(ctx, el, build)
        elif el.type == TargetType.manually_generated:
            build.handleManuallyGeneratedForBazelGen(ctx, el, build)

    @classmethod
    def getVisitor(cls) -> VisitorType:
        def visitor(el: "BuildTarget", ctx: VisitorContext, _var: bool = False):
            # build is the build that produce this element that is used
            # by the ctx.producer build
            build = el.producedby
            parentBuild = ctx.producer

            assert isinstance(ctx, BazelBuildVisitorContext)
            if parentBuild is not None and parentBuild.rulename.name == "phony":
                pass
            if (
                parentBuild is not None
                and ctx.parentIsPhony
                and not parentBuild.canGenerateFinal()
            ):
                logging.info(
                    f"Skipping {el.name} used by {parentBuild.rulename.name} because it's a chain of empty targets"
                )
                return False
            if el.producedby is not None:
                build = el.producedby
                BuildVisitor.visitProduced(ctx, el, build)
                return True
            else:
                Build.handleFileForBazelGen(el, ctx)
                return True
            return True

        return visitor
