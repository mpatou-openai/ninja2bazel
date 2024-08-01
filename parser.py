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

    cur_dir = os.path.dirname(os.path.abspath(filename))
    logging.info("Parising ninja file and buildTargets")
    top_levels_targets = getBuildTargets(
        raw_ninja, cur_dir, filename, manually_generated, rootdir
    )
    logging.info("Generating Bazel BUILD files from buildTargets")
    output = genBazelBuildFiles(top_levels_targets, rootdir)
    logging.info("Done")
    print(output)


if __name__ == "__main__":
    main()
