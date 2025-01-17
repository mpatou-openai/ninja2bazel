import glob
import logging
import re
from typing import Optional

from bazel import BazelCCImport

def _processValue(inflightVals: str, inflightAttr: str, current: BazelCCImport):
    inGlob = False
    assert inflightVals is not None
    assert inflightAttr is not None
    assert current is not None
    tmp = []
    vals = []
    for v in inflightVals.split("\n"):
        val = v.strip()
        if val.startswith("glob(["):
            if match_glob(val):
                vals.extend(parse_glob(val))
            else:
                inGlob = True
                tmp.append(val)
        elif inGlob:
            tmp.append(val)
        else:
            if val.endswith(","):
                val = val[:-1]
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
                if len(subVal) > 0:
                    vals.append(subVal)
    if inGlob:
        inGlob = False
        vals.extend(parse_glob("".join(tmp)))
        tmp = []
    setattr(current, inflightAttr, vals)

def parseCCImports(raw_imports: list[str], location: str) -> list[BazelCCImport]:
    imports = []
    newObj = False
    current: Optional[BazelCCImport] = None
    name = ""
    inflightAttr = None
    inflightVals = None
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
            if inflightVals is not None:
                _processValue(inflightVals, inflightAttr, current)
                inflightVals = None
            imports.append(current)
            current = None
            continue
                

        regexAttribute = r"(.*)\s*=\s*(.*)"
        r = re.match(regexAttribute, line)
        if r:
            attribute = r.group(1).strip()
            val = r.group(2).strip()
            if inflightVals is not None:
                _processValue(inflightVals, inflightAttr, current)
                inflightVals = None

            if attribute == "name":
                name = cleanupVar(val)
                current = BazelCCImport(name)
                # Force this external repo
                current.setLocation("@cpp_ext_libs//")
                current.setPhysicalLocation(location)
            if attribute in ["interface_library", "shared_library"]:
                assert current is not None
                current.setSharedLibrarys(cleanupVar(val))
            if attribute == "skip_wrapping":
                assert current is not None
                current.setSkipWrapping(val == "True")
            if attribute == "static_library":
                assert current is not None
                current.setStaticLibrarys(cleanupVar(val))
            if attribute == "static_libs":
                assert current is not None
                current.setStaticLibrarys(cleanupVar(val))
            if attribute in ["deps", "hdrs", "includes"]:
                assert current is not None
                inflightAttr = attribute
                inflightVals = val
        else:
            if inflightVals is not None:
                inflightVals += f"\n{line}"


    return imports


def cleanupVar(var: str) -> str:
    return var.replace('"', "").replace("'", "").replace(",", "").strip()


def match_glob(raw_glob: str) -> bool:
    regex = r'glob\(\s*\[(.*)\]\s*\)'
    matches = re.search(regex, raw_glob)
    return matches is not None

def parse_glob(raw_glob: str) -> list[str]:
    ret: list[str] = []
    regex = r'glob\(\s*\[(.*)\]\s*\)'

    logging.debug(f"Processing glob: {raw_glob}")
    matches = re.search(regex, raw_glob)
    if not matches or not matches.group(1):
        logging.error(f"Error parsing glob: {raw_glob}")
        raise ValueError(f"Error parsing not matching this regex {regex}")

    for e in matches.group(1).split(","):
        e = e.strip()
        pattern = e.replace('"', "").replace("'", "")
        matching_files = glob.glob(pattern, recursive=True)

        # Print the matching files
        for file in matching_files:
            ret.append(file)
    return ret
