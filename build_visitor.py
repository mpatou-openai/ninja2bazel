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
    ) -> bool:
        if build.rulename.name != "phony":
            if len(el.usedbybuilds) == 0:
                logging.warning(
                    f"Skipping non phony top level target that is not used by anything: {el}"
                )
                return False
            rawCmd = build.getCoreCommand()

            if rawCmd is None and build.rulename.name != "CUSTOM_COMMAND":
                logging.warning(f"{el} has no valid command {build.inputs}")
                logging.warning(
                    f"Didn't find a valid command in {build.getRawcommand()}"
                )
                return False
            assert rawCmd is not None
            # We don't care about the directory where the command should run when generating
            # bazel command, in theory it should already be baked in the command itself
            (cmd, _) = rawCmd
            return build.handleRuleProducedForBazelGen(ctx, el, cmd)
        elif build.rulename.name == "phony":
            logging.info(f"Handling phony {build.outputs[0]}")
            return build.handlePhonyForBazelGen(ctx, el, build)
        else:
            assert False


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
                parentBuild is not None and
                ctx.parentIsPhony and
                ctx.current is None
            ):
                logging.info(
                    f"Skipping {el.name} {showParentBuildDetail(parentBuild)} because it's a chain of empty targets"
                )
                return False
            if el.producedby is not None:
                build = el.producedby
                return BuildVisitor.visitProduced(ctx, el, build)
            else:
                if el.type == TargetType.manually_generated:
                    Build.handleManuallyGeneratedForBazelGen(ctx, el)
                    return True
                Build.handleFileForBazelGen(el, ctx)
                return True

        return visitor

def showParentBuildDetail(build: Build) -> str:
    output = build.outputs[0]
    parentBuild = output.usedbybuilds[0]
    if parentBuild is None:
        parentBuildRuleName = "NONE"
        parentBuildOutput = "NONE"
    else:
        parentBuildOutput = parentBuild.outputs[0]
        parentBuildRuleName = parentBuild.rulename.name

    return f"-> {build.rulename.name} -> {output} -> {parentBuildRuleName} -> {parentBuildOutput}"