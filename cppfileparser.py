import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, Generator, List, Optional, Set, Tuple

from bazel import BaseBazelTarget, BazelCCImport
from helpers import resolvePath


def findAllHeaderFiles(current_dir: str) -> Generator[str, None, None]:
    for dirpath, dirname, files in os.walk(current_dir):
        for f in files:
            if f.endswith(".h") or f.endswith(".hpp"):
                yield (f"{dirpath}/{f}")


def parseIncludes(includes: str) -> Set[str]:
    matches = re.findall(r"-I([^ ](?:[^ ]|(?: (?!(?:-I)|(?:-isystem)|$)))+)", includes)
    return set(matches)


cache: Dict[str, Tuple[Set[Tuple[str, str]], Set[str], Set[BazelCCImport]]] = {}
seen = set()


def _findCPPIncludeForFile(
    file: str,
    includes_dirs: Set[str],
    includes: str,
    current_dir: str,
    name: str,
    cc_imports: List[BazelCCImport],
    compilerIncludes: List[str],
):
    found = False
    neededImports = set()
    foundHeaders = set()
    notFoundHeaders = set()

    for d in includes_dirs:
        if d.startswith("/"):
            full_file_name = f"{d}/{file}"
        else:
            full_file_name = f"{current_dir}/{d}/{file}"

        if not os.path.exists(full_file_name) or os.path.isdir(full_file_name):
            continue

        logging.debug(f"Found {file} in the includes variable")
        for imp in cc_imports:
            if full_file_name in imp.hdrs:
                logging.info(f"Found {full_file_name} in {imp}")
                neededImports.add(imp)
                found = True
                break

        for cdir in compilerIncludes:
            if file.endswith("DataTypes.h"):
                logging.info(f"Looking for {file} in the compiler include: {cdir}")
            full_file_name2 = f"{cdir}/{file}"
            if not os.path.exists(full_file_name2) or os.path.isdir(full_file_name2):
                continue
            logging.info(f"Found {file} in the compiler include: {cdir}")
            found = True
            break

        if found:
            break

        foundHeaders.add((full_file_name, d))
        full_file_name = resolvePath(full_file_name)

        fndHdrs, notFndHdrs, fndImports = findCPPIncludes(
            full_file_name, includes, compilerIncludes, cc_imports, name
        )
        foundHeaders.update(fndHdrs)
        notFoundHeaders.update(notFndHdrs)
        neededImports.update(fndImports)
        found = True
        break
    return found, foundHeaders, notFoundHeaders, neededImports


def findCPPIncludes(
    name: str,
    includes: str,
    compilerIncludes: List[str],
    cc_imports: List[BazelCCImport],
    parent: Optional[str] = None,
) -> Tuple[Set[Tuple[str, str]], Set[str], Set[BazelCCImport]]:
    key = f"{name} {includes}"
    # There is sometimes loop, as we don't really implement the #pragma once
    # deal with it
    if key in cache:
        return cache[key]
    if key in seen:
        return set(), set(), set()
    seen.add(key)
    if includes is not None:
        includes_dirs = parseIncludes(includes)
    else:
        includes_dirs = []
    current_dir = os.path.dirname(os.path.abspath(name))
    logging.debug(f"Handling findCPPIncludes {name}")
    with open(name, "r") as f:
        content = f.readlines()
    neededImports = set()
    foundHeaders = []
    notFoundHeaders: List[str] = []
    for line in content:
        found = False
        match = re.match(r'#\s*include ((?:<|").*(?:>|"))', line)
        if not match:
            continue
        current_include = match.group(1)
        file = current_include[1:-1]
        if file.endswith("StringMapEntry.h"):
            logging.info(f"Found {file} in {name} with found = {found} 2")

        if current_include.startswith('"'):
            full_file_name = f"{current_dir}/{file}"
            if os.path.exists(full_file_name) and not os.path.isdir(full_file_name):
                found = True
                logging.debug(f"Found {file} in the same directory as the looked file")
                # Not sure if it's actually a good idea to use realpath
                # We need a way of dealing with path with ..
                # full_file_name = os.path.realpath(full_file_name)
                full_file_name = resolvePath(full_file_name)
                foundHeaders.append((full_file_name, current_dir))
                logging.debug(f"Checking {full_file_name} for {name}")
                fndHdrs, notFndHdrs, fndImports = findCPPIncludes(
                    full_file_name, includes, compilerIncludes, cc_imports, name
                )
                foundHeaders.extend(fndHdrs)
                notFoundHeaders.extend(notFndHdrs)
                neededImports.update(fndImports)
            else:
                if len(includes_dirs) == 0:
                    cache[key] = (
                        set(),
                        set(),
                        set(),
                    )
                    return (set(), set(), set())
                found, fndHdrs, notFndHdrs, fndImports = _findCPPIncludeForFile(
                    file,
                    includes_dirs,
                    includes,
                    current_dir,
                    name,
                    cc_imports,
                    compilerIncludes,
                )
                foundHeaders.extend(fndHdrs)
                notFoundHeaders.extend(notFndHdrs)
                neededImports.update(fndImports)
        else:
            if len(includes_dirs) == 0:
                cache[key] = (
                    set(),
                    set(),
                    set(),
                )
                return (set(), set(), set())
            found, fndHdrs, notFndHdrs, fndImports = _findCPPIncludeForFile(
                file,
                includes_dirs,
                includes,
                current_dir,
                name,
                cc_imports,
                compilerIncludes,
            )
            foundHeaders.extend(fndHdrs)
            notFoundHeaders.extend(notFndHdrs)
            neededImports.update(fndImports)
        if name.endswith("ilist_node_base.h "):
            logging.info(f"Found {file} in {name} with found = {found}")

        # We don't include compiler includes in the list of includes
        if not found:
            for d in compilerIncludes:
                full_file_name = f"{d}/{file}"
                if not os.path.exists(full_file_name) or os.path.isdir(full_file_name):
                    continue
                logging.debug(f"Found {file} in the compiler includes")
                found = True
                break

        if not found:
            logging.info(
                f"Not found {file} in the compiler includes for {name} wih includes {includes}"
            )
            notFoundHeaders.append(file)
    if len(notFoundHeaders) > 0:
        logging.debug(f"Could not find {set(notFoundHeaders)} in {name}")
    cache[key] = (set(foundHeaders), set(notFoundHeaders), neededImports)
    return (set(foundHeaders), set(notFoundHeaders), neededImports)
