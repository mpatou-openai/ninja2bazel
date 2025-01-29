import logging
import os
import re
from typing import Dict, List, Set, Tuple

seen = set()
cache: Dict[str, Dict[str, List[Tuple[str, str]]]] = {}

def findProtoIncludes(name: str, includeDirs: List[str]) -> Dict[str, List[Tuple[str, str]]]:
    key = f"{name} {includeDirs}"
    # There is sometimes loop, as we don't really implement the #pragma once
    # deal with it
    if key in cache:
        return cache[key]
    if key in seen:
        return {}
    seen.add(key)
    logging.debug(f"Handling findProtoIncludes {name}")
    with open(name, "r") as f:
        content = f.readlines()
    ret:Dict[str, List[Tuple[str, str]]] = {}
    ret[name] = []
    for line in content:
        match = re.match(r'import "(.*)";', line)
        if match is None:
            continue
        if match.group(1).startswith("google/"):
            ret[name].append((match.group(1), "@"))
            continue
        for d in includeDirs:
            filename = f"{d}{os.path.sep}{match.group(1)}"
            if os.path.exists(filename):
                logging.info(f"Found {match.group(1)} in {d}")
                ret[name].append((filename, d))
                ret.update(findProtoIncludes(filename, includeDirs))
                break
            else:
                logging.debug(f"Did not find {filename}")
    cache[key] = ret
    return ret
