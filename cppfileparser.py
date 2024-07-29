import logging
import os
import re
from functools import wraps
from typing import List


def findAllHeaderFiles(current_dir: str) -> List[str]:
    for dirpath, dirname, files in os.walk(current_dir):
        for f in files:
            if f.endswith(".h") or f.endswith(".hpp"):
                yield (f"{dirpath}/{f}")


def parseIncludes(includes: str) -> List[str]:
    matches = re.findall(r"-I([^ ](?:[^ ]|(?: (?!(?:-I)|$)))+)", includes)
    return set(matches)


cache = {}
seen = set()


def findIncludes(name: str, includes: str, parent: str = None) -> List[str]:
    key = f"{name} {includes}"
    # There is sometimes loop, as we don't really implement the #pragma once
    # deal with it
    if key in seen:
        return []
    seen.add(key)
    if includes is not None:
        includes_dirs = parseIncludes(includes)
    else:
        includes_dirs = []
    current_dir = os.path.dirname(os.path.abspath(name))
    logging.debug(f"Handling findIncludes {name}")
    with open(name, "r") as f:
        content = f.readlines()
    ret = []
    for line in content:
        match = re.match(r'#include ((?:<|").*(?:>|"))', line)
        if not match:
            continue
        current_include = match.group(1)
        file = current_include[1:-1]
        if current_include.startswith('"'):
            full_file_name = f"{current_dir}/{file}"
            if os.path.exists(full_file_name):
                logging.debug(f"Found {file} in the same directory as the looked file")
                ret.append(full_file_name)
                ret.extend(findIncludes(full_file_name, includes))
            else:
                # file don't exists in the same directory, let's try to find one
                # elsewhere
                for d in includes_dirs:
                    full_file_name = f"{current_dir}/{d}/{file}"
                    if not os.path.exists(full_file_name):
                        continue
                    logging.debug(f"Found {file} in the includes variable")
                    ret.append(full_file_name)
                    ret.extend(findIncludes(full_file_name, includes))
                    break
        else:
            for d in includes_dirs:
                full_file_name = f"{current_dir}/{d}/{file}"
                if not os.path.exists(full_file_name):
                    continue
                logging.debug(f"Found {file} in the includes variable")
                ret.append(full_file_name)
                ret.extend(findIncludes(full_file_name, includes))
                break
    cache[key] = ret
    return ret
