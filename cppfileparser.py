import logging
import os
import re
from typing import Dict, Generator, List, Optional, Set, Tuple


def findAllHeaderFiles(current_dir: str) -> Generator[str, None, None]:
    for dirpath, dirname, files in os.walk(current_dir):
        for f in files:
            if f.endswith(".h") or f.endswith(".hpp"):
                yield (f"{dirpath}/{f}")


def parseIncludes(includes: str) -> Set[str]:
    matches = re.findall(r"-I([^ ](?:[^ ]|(?: (?!(?:-I)|(?:-isystem)|$)))+)", includes)
    return set(matches)


cache: Dict[str, List[Tuple[str, str]]] = {}
seen = set()


def findCPPIncludes(
    name: str, includes: str, parent: Optional[str] = None
) -> List[Tuple[str, str]]:
    key = f"{name} {includes}"
    # There is sometimes loop, as we don't really implement the #pragma once
    # deal with it
    if key in cache:
        return cache[key]
    if key in seen:
        return []
    seen.add(key)
    if includes is not None:
        includes_dirs = parseIncludes(includes)
    else:
        includes_dirs = []
    current_dir = os.path.dirname(os.path.abspath(name))
    logging.debug(f"Handling findCPPIncludes {name}")
    with open(name, "r") as f:
        content = f.readlines()
    ret = []
    not_found = []
    for line in content:
        found = False
        match = re.match(r'#\s*include ((?:<|").*(?:>|"))', line)
        if not match:
            continue
        current_include = match.group(1)
        file = current_include[1:-1]
        if current_include.startswith('"'):
            full_file_name = f"{current_dir}/{file}"
            if os.path.exists(full_file_name) and not os.path.isdir(full_file_name):
                found = True
                logging.debug(f"Found {file} in the same directory as the looked file")
                full_file_name = os.path.realpath(full_file_name)
                ret.append((full_file_name, current_dir))
                ret.extend(findCPPIncludes(full_file_name, includes, name))
            else:
                # file don't exists in the same directory, let's try to find one
                # elsewhere
                for d in includes_dirs:
                    if d.startswith("/"):
                        full_file_name = f"{d}/{file}"
                    else:
                        full_file_name = f"{current_dir}/{d}/{file}"
                    if not os.path.exists(full_file_name) or os.path.isdir(
                        full_file_name
                    ):
                        continue
                    logging.debug(f"Found {file} in the includes variable")
                    ret.append((full_file_name, d))
                    full_file_name = os.path.realpath(full_file_name)
                    ret.extend(findCPPIncludes(full_file_name, includes, name))
                    found = True
                    break
        else:
            for d in includes_dirs:
                if d.startswith("/"):
                    full_file_name = f"{d}/{file}"
                else:
                    full_file_name = f"{current_dir}/{d}/{file}"
                if not os.path.exists(full_file_name) or os.path.isdir(full_file_name):
                    continue
                logging.debug(f"Found {file} in the includes variable")
                ret.append((full_file_name, d))
                full_file_name = os.path.realpath(full_file_name)
                ret.extend(findCPPIncludes(full_file_name, includes, name))
                found = True
                break
        if not found:
            not_found.append(file)
    if len(not_found) > 0:
        logging.info(f"Could not find {not_found} in {name}")
    cache[key] = ret
    return ret
