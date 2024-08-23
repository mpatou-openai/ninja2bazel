#!/usr/bin/env python3
import argparse
import logging
import os
import sys

from ninjabuild import genBazelBuildFiles, getBuildTargets


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Process Ninja build input file.")
    parser.add_argument("filename", type=str, help="Ninja build input file")
    parser.add_argument("rootdir", type=str, help="Root directory")
    parser.add_argument(
        "-m",
        "--manually_generated",
        action="append",
        help="Manually generated dependencies",
    )
    parser.add_argument(
        "--remap",
        action="append",
        help="Which path  are remopped to which path",
    )
    parser.add_argument(
        "-p",
        "--prefix",
        default="",
        help="Initial directory prefix for generated Bazel BUILD files",
    )
    parser.add_argument(
        "--imports",
        default="",
        help="A file containing a list of cc_imports to be added to the BUILD files",
    )
    args = parser.parse_args()

    filename = args.filename
    rootdir = args.rootdir
    manually_generated = args.manually_generated

    if not filename or not rootdir:
        logging.fatal(
            "Ninja build input file and/or folder where the code is located is/are missing"
        )
        sys.exit(-1)
    with open(filename, "r") as f:
        raw_ninja = f.readlines()

    if args.imports != "":
        with open(args.imports, "r") as f:
            raw_imports = f.readlines()

    prefix = ""
    if args.prefix != "":
        if not os.path.exists(f"{rootdir}{os.path.sep}{args.prefix}"):
            logging.fatal(f"Prefix directory {args.prefix} does not exist in {rootdir}")
            sys.exit(-1)
        if not os.path.isdir(f"{rootdir}{os.path.sep}{args.prefix}"):
            logging.fatal(f"Prefix directory {args.prefix} is not a directory")
            sys.exit(-1)
        prefix = f"{args.prefix}{os.path.sep}"

    cur_dir = os.path.dirname(os.path.abspath(filename))
    logging.info("Parising ninja file and buildTargets")
    if not rootdir.endswith(os.path.sep):
        rootdir = f"{rootdir}{os.path.sep}"
    remap = {}
    if args.remap:
        for e in args.remap:
            (fromPath, toPath) = e.split("=")
            remap[fromPath] = toPath

    top_levels_targets = getBuildTargets(
        raw_ninja, cur_dir, filename, manually_generated, rootdir, prefix, remap
    )
    logging.info("Generating Bazel BUILD files from buildTargets")
    output = genBazelBuildFiles(top_levels_targets, rootdir)
    logging.info("Done")
    print(output)


if __name__ == "__main__":
    main()
