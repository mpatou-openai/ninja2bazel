import glob
import logging
import re
from typing import Optional

from bazel import BazelCCImport


def parseCCImports(raw_imports: list[str], location: str) -> list[BazelCCImport]:
    imports = []
    newObj = False
    current: Optional[BazelCCImport] = None
    name = ""
    inflightAttr = None
    inflightVals = None
    inflightComplete = False
    for line in raw_imports:

        line = line.strip()
        if line.startswith("#") or not line:
            continue

        if line.startswith("cc_import("):
            if newObj:
                raise ValueError("cc_import() while a current one is open")
            newObj = True
            continue

        if line.startswith(")"):
            if not newObj:
                raise ValueError(
                    f"closing cc_import() while a current one is not open, name = {name}"
                )
            newObj = False
            assert current is not None
            imports.append(current)
            current = None
            continue

        if "=" in line:
            val = line.split("=")[1].strip()

            if line.startswith("name = "):
                name = cleanupVar(val)
                current = BazelCCImport(name)
                # Force this external repo
                current.setLocation("@cpp_ext_libs//")
                current.setPhysicalLocation(location)
            if line.startswith("interface_library = ") or line.startswith(
                "shared_library = "
            ):
                assert current is not None
                current.setSharedLibrarys(cleanupVar(val))
            if line.startswith("skip_wrapping = "):
                assert current is not None
                current.setSkipWrapping(val == "True")
            if line.startswith("static_library = "):
                assert current is not None
                current.setStaticLibrarys(cleanupVar(val))
            if line.startswith("static_libs = "):
                assert current is not None
                current.setStaticLibrarys(cleanupVar(val))
            if line.startswith("deps = "):
                assert current is not None
                regex = r"([()\[\]])"
                openParentesis = 0
                openBrackets = 0
                found = re.findall(regex, line)
                for c in found:
                    if c == "[":
                        openBrackets += 1
                    if c == "]":
                        openBrackets -= 1
                    if c == "(":
                        openParentesis += 1
                    if c == ")":
                        openParentesis -= 1
                inflightAttr = "deps"
                inflightVals = line.replace("deps = ", "").strip()
                inflightComplete = False
                if openParentesis == 0 or openBrackets == 0:
                    inflightComplete = True
            if line.startswith("includes = "):
                assert current is not None
                regex = r"([\[\]])"
                openBrackets = 0
                found = re.findall(regex, line)
                for c in found:
                    if c == "[":
                        openBrackets += 1
                    if c == "]":
                        openBrackets -= 1
                inflightAttr = "includes"
                inflightVals = line.replace("includes = ", "").strip()
                inflightComplete = False
                if openBrackets == 0:
                    inflightComplete = True
            if line.startswith("hdrs = "):
                assert current is not None
                regex = r"([()\[\]])"
                openParentesis = 0
                openBrackets = 0
                found = re.findall(regex, line)
                for c in found:
                    if c == "[":
                        openBrackets += 1
                    if c == "]":
                        openBrackets -= 1
                    if c == "(":
                        openParentesis += 1
                    if c == ")":
                        openParentesis -= 1
                inflightAttr = "hdrs"
                inflightVals = line.replace("hdrs = ", "").strip()
                inflightComplete = False
                if openParentesis == 0 or openBrackets == 0:
                    inflightComplete = True

        else:
            logging.info(line)
        if inflightComplete:
            assert inflightVals is not None
            assert inflightAttr is not None
            assert current is not None
            inflightComplete = False
            vals = []
            for v in inflightVals.split("\n"):
                val = v.strip()
                if val.endswith(","):
                    val = val[:-1]
                if val.startswith("glob("):
                    vals.extend(parse_glob(val))
                else:
                    for subVal in val.split(","):
                        if len(subVal) == 0:
                            continue
                        if subVal[0] == "[":
                            subVal = subVal[1:]
                        if val[-1] == "]":
                            subVal = subVal[:-1]
                        subVal = subVal.strip()
                        # replace quotes ...
                        subVal =subVal.replace('"', "").replace("'", "")
                        if len(subVal) > 0 and subVal[0] == ":":
                            subVal = subVal[1:]
                        vals.append(subVal)
            setattr(current, inflightAttr, vals)

    return imports


def cleanupVar(var: str) -> str:
    return var.replace('"', "").replace("'", "").replace(",", "").strip()


def parse_glob(raw_glob: str) -> list[str]:
    ret: list[str] = []

    for e in raw_glob[:-1].replace("glob(", "").split(","):
        e = e.strip()
        if e[0] == "[":
            e = e[1:]
        if e[-1] == "]":
            e = e[:-1]
        pattern = e.replace('"', "").replace("'", "")
        matching_files = glob.glob(pattern, recursive=True)

        # Print the matching files
        for file in matching_files:
            ret.append(file)

    return ret
