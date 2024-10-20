import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from bazel import BazelCCImport
from helpers import resolvePath


def findAllHeaderFiles(current_dir: str) -> Generator[str, None, None]:
    for dirpath, dirname, files in os.walk(current_dir):
        for f in files:
            if f.endswith(".h") or f.endswith(".hpp"):
                yield (f"{dirpath}/{f}")


def parseIncludes(includes: str) -> Set[str]:
    matches = re.findall(r"-I([^ ](?:[^ ]|(?: (?!(?:-I)|(?:-isystem)|$)))+)", includes)
    return set(matches)


seen = set()


@dataclass
class CPPIncludes:
    foundHeaders: Set[Tuple[str, str]]
    notFoundHeaders: Set[str]
    neededImports: Set[BazelCCImport]
    neededGeneratedFiles: Set[Tuple[str, str]]

    def __add__(self, other: "CPPIncludes") -> "CPPIncludes":
        return CPPIncludes(
            self.foundHeaders.union(other.foundHeaders),
            self.notFoundHeaders.union(other.notFoundHeaders),
            self.neededImports.union(other.neededImports),
            self.neededGeneratedFiles.union(other.neededGeneratedFiles),
        )


cache: Dict[str, CPPIncludes] = {}


def _findCPPIncludeForFile(
    file: str,
    includes_dirs: Set[str],
    current_dir: str,
    name: str,
    cc_imports: List[BazelCCImport],
    compilerIncludes: List[str],
    generatedFiles: Dict[str, Any],
) -> Tuple[bool, CPPIncludes]:
    found = False
    ret = CPPIncludes(set(), set(), set(), set())

    for d in includes_dirs:
        generated_dir = False
        if d.startswith("/generated"):
            full_file_name = f"{d.replace('/generated', '')}/{file}"
            generated_dir = True
        elif d.startswith("/"):
            full_file_name = f"{d}/{file}"
        else:
            full_file_name = f"{current_dir}/{d}/{file}"

        if generated_dir and full_file_name in generatedFiles:
            # The search header is a generated one that whose path match the includes
            # There might be something to do remove prefixes
            if full_file_name.endswith(".pb.h"):
                # Skip protobuf files
                continue
            ret.neededGeneratedFiles.add((full_file_name, d))
            found = True
            break

        if not os.path.exists(full_file_name) or os.path.isdir(full_file_name):
            continue

        logging.debug(f"Found {file} in the includes variable")
        # Check if the file is part of the cc_imports as we don't want to recurse for headers there
        for imp in cc_imports:
            if full_file_name in imp.hdrs:
                logging.info(f"Found {full_file_name} in {imp}")
                ret.neededImports.add(imp)
                found = True
                break

        # Check if the file is part of the compiler include as we don't want to recurse for headers
        # there too
        for cdir in compilerIncludes:
            full_file_name2 = f"{cdir}/{file}"
            if not os.path.exists(full_file_name2) or os.path.isdir(full_file_name2):
                continue
            logging.info(f"Found {file} in the compiler include: {cdir}")
            found = True
            break

        if found:
            break

        full_file_name = resolvePath(full_file_name)
        ret.foundHeaders.add((full_file_name, d))

        cppIncludes = findCPPIncludes(
            full_file_name,
            includes_dirs,
            compilerIncludes,
            cc_imports,
            generatedFiles,
            name,
        )
        ret += cppIncludes
        found = True
        break
    return found, ret


def findCPPIncludes(
    name: str,
    includes_dirs: Set[str],
    compilerIncludes: List[str],
    cc_imports: List[BazelCCImport],
    generatedFiles: Dict[str, Any],
    parent: Optional[str] = None,
) -> CPPIncludes:
    key = f"{name} {includes_dirs}"
    ret = CPPIncludes(set(), set(), set(), set())
    # There is sometimes loop, as we don't really implement the #pragma once
    # deal with it
    if key in cache:
        return cache[key]
    if key in seen:
        return ret
    seen.add(key)
    current_dir = os.path.dirname(os.path.abspath(name))
    logging.debug(f"Handling findCPPIncludes {name}")
    with open(name, "r") as f:
        content = f.readlines()
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
                # Not sure if it's actually a good idea to use realpath
                # We need a way of dealing with path with ..
                # full_file_name = os.path.realpath(full_file_name)
                full_file_name = resolvePath(full_file_name)
                ret.foundHeaders.add((full_file_name, current_dir))
                logging.debug(f"Checking {full_file_name} for {name}")
                cppIncludes = findCPPIncludes(
                    full_file_name,
                    includes_dirs,
                    compilerIncludes,
                    cc_imports,
                    generatedFiles,
                    name,
                )
                ret += cppIncludes
            else:
                if len(includes_dirs) == 0:
                    empty = CPPIncludes(set(), set(), set(), set())
                    cache[key] = empty
                    return empty
                found, cppIncludes = _findCPPIncludeForFile(
                    file,
                    includes_dirs,
                    current_dir,
                    name,
                    cc_imports,
                    compilerIncludes,
                    generatedFiles,
                )
                ret += cppIncludes
        else:
            if len(includes_dirs) == 0:
                empty = CPPIncludes(set(), set(), set(), set())
                cache[key] = empty
                return empty
            found, cppIncludes = _findCPPIncludeForFile(
                file,
                includes_dirs,
                current_dir,
                name,
                cc_imports,
                compilerIncludes,
                generatedFiles,
            )
            ret += cppIncludes

        # We don't include compiler includes in the list of includes
        if not found:
            for d in compilerIncludes:
                full_file_name = f"{d}/{file}"
                if not os.path.exists(full_file_name) or os.path.isdir(full_file_name):
                    continue
                logging.debug(f"Found {file} in the compiler includes")
                found = True
                break

            if file in generatedFiles:
                logging.info(f"Found missing header {file} in the generated files")
                found = True

        if not found:
            logging.info(
                f"Not found {file} in the compiler includes for {name} wih includes {includes_dirs}"
            )
            ret.notFoundHeaders.add(file)
    if len(ret.notFoundHeaders) > 0:
        logging.debug(f"Could not find {set(ret.notFoundHeaders)} in {name}")
    cache[key] = ret
    return ret
