import logging
import os
import re
import sys
from enum import Enum
from functools import total_ordering
from typing import Any, Callable, Dict, List, Optional

from bazel import BazelBuild, BazelTarget
from cppfileparser import findAllHeaderFiles, findIncludes

IGNORED_STANZA = [
    "ninja_required_version",
    "default",
]
IGNORED_TARGETS = [
    "edit_cache",
    "rebuild_cache",
    "clean",
    "help",
    "install",
    "build.ninja",
    "list_install_components",
    "install/local",
    "install/strip",
]

TargetType = Enum(
    "TargetType", ["other", "unknown", "known", "external", "manually_generated"]
)


@total_ordering
class BuildTarget:

    def __init__(self, name: str, implicit: bool = False):
        self.name = name
        self.implicit = implicit
        self.producedby: Optional["Build"] = None
        self.usedbybuilds: List["Build"] = []
        self.is_a_file = False
        self.type = TargetType.other
        self.headers: Optional[List[str]] = None
        self.aliases: List[str] = []
        self.external = False

    def __hash__(self) -> int:
        return self.name.__hash__()

    def __eq__(self, other) -> bool:
        return self.name == other.name

    def __lt__(self, other) -> bool:
        return self.name < other.name

    def setHeadersFiles(self, files: List[str]):
        self.headers = files

    def markAsManual(self):
        self.type = TargetType.manually_generated

    def markAsExternal(self):
        self.type = TargetType.external

    def markAsUnknown(self):
        self.type = TargetType.unknown

    def markAsknown(self):
        self.type = TargetType.known

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
            if (
                d.producedby
                and d.producedby.rulename.name == "phony"
                and len(d.producedby.inputs) == 0
                and len(d.producedby.depends) == 0
            ):
                return True
            v = d.depsAreVirtual()
            if not v:
                return False
        return False

    def visitGraph(
        self,
        visitor: Callable[["BuildTarget", Dict[str, Any]], bool],
        ctx: Dict[str, Any],
    ):
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
                visitor(self, ctx)
            except Exception as e:
                logging.error(f"Error visiting {self.name}: {e}")
                raise
        if self.producedby:
            for el in sorted(self.producedby.inputs):
                newctx = ctx["setup_subcontext"](ctx)
                el.visitGraph(visitor, newctx)
            for el in sorted(self.producedby.depends):
                if not el.depsAreVirtual():
                    newctx = ctx["setup_subcontext"](ctx)
                    el.visitGraph(visitor, newctx)

    def printGraph(self, ident: int = 0, file=sys.stdout):
        def visitor(el: "BuildTarget", ctx: Dict[str, Any]):
            print(" " * ctx["ident"] + el.name)

        def setup(ctx: Dict[str, Any]):
            ctx2 = {k: v for k, v in ctx.items()}
            ctx2["ident"] = ctx2["ident"] + 1
            return ctx2

        ctx = {
            "ident": ident,
            "output": file,
            "setup_subcontext": setup,
        }

        self.visitGraph(visitor, ctx)

    def _handleManuallyGeneratedForBazelGen(
        self, el: "BuildTarget", ctx: Dict[str, Any]
    ):
        t = BazelTarget("manually_generated_fixme", el.name)
        t.addSrc(el.name.replace(ctx["rootdir"], ""))
        ctx["bazelbuild"].bazelTargets.append(t)
        if ctx["current"] is not None:
            ctx["current"].addDep(t)

    def _handleCmdForBazelGen(self, cmd: str, el: "BuildTarget", ctx: Dict[str, Any]):
        if (
            "clang" in cmd
            or "gcc" in cmd
            or "clang++" in cmd
            or "c++" in cmd
            or "g++" in cmd
        ) and "$LINK_FLAGS" in cmd:
            t = BazelTarget("cc_binary", el.name)
            ctx["bazelbuild"].bazelTargets.append(t)
            if ctx["current"] is not None:
                ctx["current"].addDep(t)
            ctx["current"] = t
            return
        if (
            "clang" in cmd
            or "gcc" in cmd
            or "clang++" in cmd
            or "c++" in cmd
            or "g++" in cmd
        ) and "-c" in cmd:
            ctx["dest"] = ctx["current"]
            # compilation of a source file to an object file, this is taken care by
            # bazel targets like cc_binary or cc_library
            return
        if "/ar " in cmd or "llvm-ar" in cmd:
            t = BazelTarget("cc_library", el.name)
            if ctx["current"] is not None:
                ctx["current"].addDep(t)
            ctx["current"] = t
            ctx["bazelbuild"].bazelTargets.append(t)
            return
        logging.debug(cmd)

    def _handleCustomCommandForBazelGen(self, el: "BuildTarget", ctx: Dict[str, Any]):
        # TODO need to specify the exec tool
        # will filter the python / other stuff from the data files
        ctx["producer"] = el.producedby
        rule = el.producedby.rulename
        c = rule.vars.get("command")
        c2 = el.producedby._resolveName(c, ["in", "out", "TARGET_FILE"])
        if c2 != c:
            c = c2
        t = BazelTarget("genrule", el.name)
        ctx["bazelbuild"].bazelTargets.append(t)
        if ctx["current"] is not None:
            ctx["current"].addDep(t)
        ctx["current"] = t

    def genBazel(self, bb: BazelBuild, rootdir: str):
        def visitor(el: "BuildTarget", ctx: Dict[str, Any]):
            if el.producedby and el.producedby.rulename.name == "CUSTOM_COMMAND":
                self._handleCustomCommandForBazelGen(el, ctx)
            elif el.producedby and el.producedby.rulename.name != "phony":
                ctx["producer"] = el.producedby
                rule = el.producedby.rulename
                c = rule.vars.get("command")
                assert c is not None
                arr = c.split("&&")
                found = False
                for cmd in arr:
                    if "$in" in cmd and ("$out" in cmd or "$TARGET_FILE" in cmd):
                        found = True
                        break
                if not found:
                    logging.warning(f"{el} has no valid command {el.producedby.inputs}")
                    logging.warning(f"Didn't find a valid command in {c}")
                else:
                    # Fixme split this to detect if it's a compiler or ar or something else
                    self._handleCmdForBazelGen(cmd, el, ctx)
            elif el.producedby and el.producedby.rulename.name == "phony":
                if ctx.get("dest") is None:
                    print(el)
                pass
            elif el.type == TargetType.manually_generated:
                self._handleManuallyGeneratedForBazelGen(el, ctx)

            elif el.producedby and el.producedby.rulename.name == "CUSTOM_COMMAND":
                print(f"Custom command {el.producedby.rulename} for {el}")
            # Note deal with C/C++ files only here
            else:
                if not ctx.get("dest"):
                    # It can happen that .o are not connected to a real library or binary but just
                    # to phony targets in this case "dest" is NotImplemented
                    # logging.warn(f"{el} is no connected to a non phony target")
                    return
                logging.debug(ctx["producer"].vars)
                if el.name.endswith(".h") or el.name.endswith(".hpp"):
                    ctx["dest"].addHdr(el.name.replace(ctx["rootdir"], ""))
                else:
                    # Not produced aka it's a file
                    # we have to parse the file and see if there is any includes
                    # if it's a "" include then we look first in the path where the file is and then
                    # in the path specified with -I
                    ctx["dest"].addSrc(el.name.replace(ctx["rootdir"], ""))
                    if not el.headers:
                        print(f"Missing headers for {el}")
                    for h in el.headers:
                        ctx["dest"].addHdr(h.replace(ctx["rootdir"], ""))

        def setup(ctx):
            ctx2 = {k: v for k, v in ctx.items()}
            return ctx2

        ctx: Dict[str, Any] = {"setup_subcontext": setup}
        ctx["bazelbuild"] = bb
        ctx["current"] = None
        if rootdir.endswith("/"):
            ctx["rootdir"] = rootdir
        else:
            ctx["rootdir"] = f"{rootdir}/"

        self.visitGraph(visitor, ctx)


class Rule:
    def __init__(self, name: str):
        self.name = name
        self.vars: Dict[str, str] = {}

    def __repr__(self):
        return self.name


class Build:
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

        for o in self.outputs:
            o.producedby = self

        for i in self.inputs:
            i.usedby(self)

        for d in self.depends:
            d.usedby(self)

        self.vars: Dict[str, str] = {}

    def __repr__(self) -> str:
        return (
            f"{' '.join([str(i) for i in self.inputs])} + "
            f"{' '.join([str(i) for i in self.depends])} => "
            f"{self.rulename.name} => {' '.join([str(i) for i in self.outputs])}"
        )


class NinjaParser:
    def __init__(self):
        self.buildEdges = []
        self.currentBuild = None
        self.currentRule = None
        self.buffer = []
        self.all_outputs = {}
        self.missing = {}
        self.vars = {}
        self.rules = {}
        self.rules["phony"] = Rule("phony")
        self.directories = []
        self.headers_files = {}

    def markDone(self):
        # What ever we had so far, we mark at finished
        self.currentBuild = None
        self.currentRule = None

    def _resolveName(self, name: str) -> str:
        regex = r"\$\{?([\w+]+)\}?"

        def replacer(match: re.Match):
            return self.vars.get(match.group(1))

        return re.sub(regex, replacer, name)

    def _handleRule(self, arr: List[str]):
        rule = Rule(arr[1])
        self.rules[rule.name] = rule
        self.currentRule = rule

    def _handleBuild(self, arr: List[str]):
        arr.pop(0)
        outputs = []
        raw_inputs: List[str] = []
        raw_depends: List[str] = []
        for i in range(len(arr)):
            e = arr[i]
            if e.endswith(":"):
                outputs.append(BuildTarget(e[:-1]))
                break
            outputs.append(BuildTarget(e))

        i += 1
        rulename = arr[i]
        i += 1
        target = raw_inputs
        for j in range(i, len(arr)):
            if arr[j] == "||":
                target = raw_depends
                continue
            if arr[j] == "|":
                target = raw_depends
                continue
            target.append(arr[j])

        if rulename == "phony":
            if len(raw_inputs) == 0:
                newraw_depends = []
                for d in raw_depends:
                    if not os.path.isdir(f"{d}"):
                        newraw_depends.append(d)

                raw_depends = newraw_depends

        inputs = []
        for s in raw_inputs:
            if not s.startswith("/"):
                p = f"{s}"
            else:
                p = s
            if os.path.exists(p):
                inputs.append(BuildTarget(s).markAsFile())
            else:
                v = self.all_outputs.get(s)
                if not v:
                    v = BuildTarget(s)
                    v.markAsUnknown()
                    self.missing[s] = v
                inputs.append(v)

        depends = []
        for d in raw_depends:
            v = self.all_outputs.get(d)
            if not v:
                v = BuildTarget(d)
                v.markAsUnknown()
                self.missing[d] = v
            depends.append(v)

        for i in range(len(outputs)):
            o = outputs[i]
            t = self.missing.get(str(o))
            if t is not None:
                # We need to reconcile t and o
                del self.missing[str(o)]
                outputs[i] = t
                t.markAsknown()
                o = t

            self.all_outputs[str(o)] = o
        rule = self.rules.get(rulename)
        if rule is None:
            logging.error(f"Coulnd't find a rule called {rulename}")
            return
        build = Build(outputs, rule, inputs, depends)

        self.currentBuild = build
        self.buildEdges.append(build)

    def handleVariable(self, name: str, value: str):
        self.vars[name] = value
        logging.debug(f"Var {name} = {self.vars[name]}")

    def handleInfclude(self, arr: List[str]):
        dir = self.directories[-1]
        filename = f"{dir}{os.path.sep}{arr[0]}"
        with open(filename, "r") as f:
            raw_ninja = f.readlines()

        cur_dir = os.path.dirname(os.path.abspath(filename))
        self.parse(raw_ninja, cur_dir)

    def finalizeHeaders(self, current_dir: str):
        for t in self.all_outputs.values():
            if not t.producedby:
                continue
            for i in t.producedby.inputs:
                if i.is_a_file:
                    includes = t.producedby.vars.get("INCLUDES")
                    headers = findIncludes(i.name, includes)
                    i.setHeadersFiles(headers)

    def setManuallyGeneratedTargets(self, manually_generated: Optional[List[str]]):
        self.manually_generated = manually_generated or []

    def inlinePhony(self):
        for o in self.all_outputs.values():
            if o.producedby.rulename.name == "phony":
                print(f"{o} produced by {o.producedby.rulename}")

    def parse(
        self,
        content: List[str],
        current_dir: str,
    ):
        self.directories.append(current_dir)
        for line in content:
            line = line.rstrip()
            if line.startswith("#"):
                continue

            if line.endswith("$"):
                self.buffer.append(line[:-1])
                continue

            if len(self.buffer) > 0:
                self.buffer.append(line)
                newline = "".join(self.buffer)
                self.buffer = []
                line = newline

            if len(line) == 0:
                self.markDone()
                continue

            arr = re.split(r" (?!\$)", line)

            if arr[0] == "rule":
                self._handleRule(arr)
                continue

            if arr[0] == "build":
                self._handleBuild(arr)
                continue

            if line.startswith(" "):
                where = None
                resolv_vars = False

                if self.currentBuild is not None:
                    where = self.currentBuild
                    resolv_vars = True

                if self.currentRule is not None:
                    where = self.currentRule

                if where is not None:
                    # TODO resolve vars with $
                    v = line.split("=")
                    key = v.pop(0)
                    key = key.strip()
                    value = "=".join(v)
                    value.strip()
                    where.vars[key] = value
                else:
                    logging.error(f'Don\'t know how to deal with this line "{line}"')
                continue

            if arr[0] in IGNORED_STANZA:
                continue

            if arr[1] == "=":
                self.handleVariable(arr[0], " ".join(arr[2:]))
                continue

            if arr[0] == "include":
                self.handleInfclude(arr[1:])
                continue

            logging.debug(f"{line} {len(line)}")
        self.directories.pop()


def getToplevels(parser: NinjaParser) -> List[BuildTarget]:
    real_top_targets = []
    for o in parser.all_outputs.values():
        if o.isOnlyUsedBy(["all"]):
            real_top_targets.append(o)
            # logging.debug(f"{o} produced by {o.producedby.rulename}")
            continue
        if str(o) in IGNORED_TARGETS or o.isOnlyUsedBy(IGNORED_TARGETS):
            continue
        if o.producedby is not None and o.producedby.rulename.name == "phony":
            # Look at all the phony build outputs
            # if all their inputs are used in another build then it's kind of an alias
            # and so it's not a top level build
            count = 0
            for i in o.producedby.inputs:
                if len(i.usedbybuilds) != 0:
                    count += 1
            if count == len(o.producedby.inputs):
                continue
        if (
            len(o.usedbybuilds) == 0
            and not o.implicit
            and not str(o).endswith("_tests.cmake")
        ):
            logging.error(f"{o} used by no one")
            # logging.debug(f"{o} produced by {o.producedby.rulename.name}")
            real_top_targets.append(o)

    return real_top_targets


def _printNiceDict(d: dict[str, Any]) -> str:
    return "".join([f"  {k}: {v}\n" for k, v in d.items()])


def getBuildTargets(
    raw_ninja: List[str],
    dir: str,
    filename: str,
    manually_generated: Optional[List[str]],
):
    parser = NinjaParser()
    parser.setManuallyGeneratedTargets(manually_generated)
    parser.parse(raw_ninja, dir)

    if len(parser.missing) != 0:
        logging.error(
            f"Something is wrong there is {len(parser.missing)} missing dependencies:\n {_printNiceDict(parser.missing)}"
        )
        return

    top_levels = getToplevels(parser)
    parser.finalizeHeaders(dir)
    return top_levels


def genBazelBuildFiles(top_levels: list[BuildTarget], rootdir: str) -> str:
    bb = BazelBuild()
    for e in sorted(top_levels):
        e.genBazel(bb, rootdir)

    return bb.genBazelBuildContent()
