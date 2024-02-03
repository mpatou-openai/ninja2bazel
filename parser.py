#!/usr/bin/env python3
import logging
import os
import sys

from ninjabuild import genBazelBuildFiles, getBuildTargets


def main():
    logging.basicConfig(level=logging.DEBUG)
    if len(sys.argv) > 2:
        filename = sys.argv[1]
        rootdir = sys.argv[2]
    else:
        logging.fatal("Ninja build input file is missing")
        sys.exit(-1)
    with open(filename, "r") as f:
        raw_ninja = f.readlines()

    cur_dir = os.path.dirname(os.path.abspath(filename))
    top_levels_targets = getBuildTargets(raw_ninja, cur_dir)
    output = genBazelBuildFiles(top_levels_targets, rootdir)
    print(output)


if __name__ == "__main__":
    main()
