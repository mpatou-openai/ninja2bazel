import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from helpers import resolvePath
from build import BuildTarget
from bazel import BazelCCImport


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
    foundHeaders: Set[Tuple[str, Optional[str]]]
    notFoundHeaders: Set[str]
    neededImports: Set[BuildTarget]
    neededGeneratedFiles: Set[Tuple[str, Optional[str]]]

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
    cc_imports: List[BuildTarget],
    compilerIncludes: List[str],
    generatedFiles: Dict[str, Any],
    generatedDir: Optional[str],
) -> Tuple[bool, CPPIncludes]:
    found = False
    ret = CPPIncludes(set(), set(), set(), set())
    check = False

    logging.info(f"_findCPPIncludeForFile: {file}")

    for d in includes_dirs:
        use_generated_dir = False
        if d == "/generated":
            full_file_name = file
            use_generated_dir = True
        elif d.startswith("/generated"):
            full_file_name = f"{d.replace('/generated', '')}/{file}"
            use_generated_dir = True
        elif d.startswith("/"):
            full_file_name = f"{d}/{file}"
        else:
            full_file_name = f"{current_dir}/{d}/{file}"

        if use_generated_dir and full_file_name in generatedFiles:
            # The search header is a generated one that whose path match the includes
            # There might be something to do remove prefixes
            ret.neededGeneratedFiles.add((full_file_name, d))
            found = True
            if not full_file_name.endswith(".pb.h"):
                check = True
                generatedFileFullName = full_file_name
                tempDir = generatedFiles[full_file_name][1]
                full_file_name = f"{tempDir}/{full_file_name}"
            logging.debug(f"Found generated {file} in the includes variable")
            break

        # Search in the compiler include, depending on how things were done in the Ninja file
        # the include path might have it or not ...
        foundCCImport = False
        for cdir in compilerIncludes:
            full_file_name2 = f"{cdir}/{file}"
            if not os.path.exists(full_file_name2) or os.path.isdir(full_file_name2):
                continue
            # File might be in the standard include path of the compiler but still coming from
            # an external packate that we need to depends on
            for imp in cc_imports:
                assert isinstance(imp.opaque, BazelCCImport)
                if full_file_name2 in imp.opaque.hdrs:
                    foundCCImport = True
                    logging.debug(f"Found {full_file_name} in {imp}")
                    ret.neededImports.add(imp)
                    break
            found = True
            break

        if found and os.path.exists(full_file_name2):
            if not foundCCImport:
                logging.debug(f"Found {file} in the compiler include path: {cdir} skipping")
            break


        if not os.path.exists(full_file_name) or os.path.isdir(full_file_name):
            continue

        logging.debug(f"Found {file} in the includes variable using {d}")
        # Check if the file is part of the cc_imports as we don't want to recurse for headers there
        for imp in cc_imports:
            assert isinstance(imp.opaque, BazelCCImport)
            if full_file_name in imp.opaque.hdrs:
                logging.info(f"Found {full_file_name} in cc_import {imp}")
                ret.neededImports.add(imp)
                found = True
                break

        if found:
            break

        full_file_name = resolvePath(full_file_name)
        if not use_generated_dir:
            # If generated dir is True it means that the header was found using a generated dir include
            # we don't want to add it as is to the list of headers otherwise we will have a "/tmp" and it won't be great
            if generatedDir and d == generatedDir:
                ret.foundHeaders.add((full_file_name.replace(f"{generatedDir}/", ""), "/generated"))
            else:
                logging.info(f"Found {file}  {full_file_name} in the includes variable using {d}")
                ret.foundHeaders.add((full_file_name, d))
        else:
            ret.neededGeneratedFiles.add(("/generated" + generatedFileFullName, d))

        found = True
        check = True
        break

    if check:
        cppIncludes = findCPPIncludes(
            full_file_name,
            includes_dirs,
            compilerIncludes,
            cc_imports,
            generatedFiles,
            use_generated_dir,
            generatedDir,
        )
        if use_generated_dir:
            newfoundHeaders = set()
            for e in cppIncludes.foundHeaders:
                # The list of header might include headers with the same temporary folder used by the current file
                # the reason for that is that current file a.h might have #include "b.h" and b.h is generated
                # so we end-up with returning /tmp/tmpxxbbcc/subfolder1/subfolder2/b.h
                if e[0].startswith(tempDir):
                    cppIncludes.neededGeneratedFiles.add(
                        (e[0].replace(tempDir, "/generated"), e[1])
                    )
                else:
                    newfoundHeaders.add((e[0], e[1]))
            cppIncludes.foundHeaders = newfoundHeaders
        ret += cppIncludes
    return found, ret

def _findCPPIncludeForFileSameDir(
    name: str,
    file: str,
    includes_dirs: Set[str],
    current_dir: str,
    cc_imports: List[BuildTarget],
    compilerIncludes: List[str],
    generatedFiles: Dict[str, Any],
    generatedDir: Optional[str],
    generated: bool = False,
    ) -> Tuple[bool, CPPIncludes]:
        ret = CPPIncludes(set(), set(), set(), set())
        found = False
        full_file_name = f"{current_dir}/{file}"
        if not (os.path.exists(full_file_name) and not os.path.isdir(full_file_name)):
            return False, ret

        found = True
        logging.debug(
            f"Found {file} in the same directory as the looked file generated {generated}"
        )
        # We need a way of dealing with path with ..
        full_file_name = resolvePath(full_file_name)
        # Current file is generated so we are in some /tmp/tmpxxbbcc path and
        # in this path we find `file` so it's safe to return "/generated"
        if generated:
            # full_file_name will have the same base folder (ie. /tmp/tmpxxbbcc) as the current file
            # it's ok we cppIncludes will take care of it

            # So this is tricky we don't know the generic name here only the resolved one and removing the folder of the file where we
            # found this include won't help because it's most probably not the one used in generatedFiles dict.
            # So we will need to iterate on the dict look for the values
            foundGenerated = False
            for k, v in generatedFiles.items():
                #logging.info(f"Checking {k} against {name}")
                if name.endswith(k):
                    foundGenerated = True
                    genericGeneratedHeader = f"{name.replace(v[1]+'/','').replace(os.path.basename(k), file)}"
                    ret.neededGeneratedFiles.add(
                        (genericGeneratedHeader, "/generated")
                    )
                    break
            if not foundGenerated:
                # We can't find the relative path because we only know the filename and the current directory
                # So let's try to use the generatedDir that was passed as an argument to hopefully remove the prefix
                assert generatedDir is not None
                tmp = full_file_name.replace(f"{generatedDir}/","")
                if tmp.startswith("/"):
                    logging.error(f"Could not find the relative path for {file} in the generated files")
                ret.neededGeneratedFiles.add(
                    (tmp, "/generated")
                )
                return False, ret
        else:
            ret.foundHeaders.add((full_file_name, None))

        cppIncludes = findCPPIncludes(
            full_file_name,
            includes_dirs,
            compilerIncludes,
            cc_imports,
            generatedFiles,
            generated,
            generatedDir,
        )
        ret += cppIncludes
        return found, ret


def findCPPIncludes(
    name: str,
    includes_dirs: Set[str],
    compilerIncludes: List[str],
    cc_imports: List[BuildTarget],
    generatedFiles: Dict[str, Any],
    generated: bool = False,
    generatedDir: Optional[str] = None,
) -> CPPIncludes:
    key = f"{name}"
    seenkey = f"{name} {includes_dirs}"
    ret = CPPIncludes(set(), set(), set(), set())
    # There is sometimes loop, as we don't really implement the #pragma once
    # deal with it
    if key in cache:
        return cache[key]
    if seenkey in seen:
        return ret
    seen.add(seenkey)
    current_dir = os.path.dirname(os.path.abspath(name))
    logging.debug(f"Handling findCPPIncludes {name}")
    with open(name, "r") as f:
        content = f.readlines()
    for line in content:
        found = False
        match = re.match(r'\s*#\s*include ((?:<|").*(?:>|"))', line)
        if not match:
            continue
        current_include = match.group(1)
        file = current_include[1:-1]

        if current_include.startswith('"'):
            found, cppIncludes = _findCPPIncludeForFileSameDir(
                name,
                file,
                includes_dirs,
                current_dir,
                cc_imports,
                compilerIncludes,
                generatedFiles,
                generatedDir,
                generated,
            )
            if not found:
                if len(includes_dirs) == 0:
                    empty = CPPIncludes(set(), set(), set(), set())
                    return empty
                found, cppIncludes = _findCPPIncludeForFile(
                    file,
                    includes_dirs,
                    current_dir,
                    cc_imports,
                    compilerIncludes,
                    generatedFiles,
                    generatedDir,
                )
                ret += cppIncludes
            else:
                ret += cppIncludes
        else:
            if len(includes_dirs) == 0:
                empty = CPPIncludes(set(), set(), set(), set())
                return empty
            found, cppIncludes = _findCPPIncludeForFile(
                file,
                includes_dirs,
                current_dir,
                cc_imports,
                compilerIncludes,
                generatedFiles,
                generatedDir,
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
                (found, cppIncludes) = _findCPPIncludeForFile(
                    file,
                    includes_dirs,
                    current_dir,
                    cc_imports,
                    compilerIncludes,
                    generatedFiles,
                    generatedDir
                )
                ret += cppIncludes
                found = True

        if not found:
            logging.info(
                f"Not found {file} in the compiler includes for {name} wih includes {includes_dirs}"
            )
            ret.notFoundHeaders.add(file)
    if len(ret.notFoundHeaders) > 0:
        ret.notFoundHeaders = set(filter(lambda x: not x.endswith(".pb.h"), ret.notFoundHeaders))
        logging.debug(f"Could not find {set(ret.notFoundHeaders)} in {name}")
    cache[key] = ret
    return ret
