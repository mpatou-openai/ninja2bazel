#!/usr/bin/python3
import contextlib
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ninjabuild import NinjaParser, getToplevels  # noqa: E402


class TestParser(unittest.TestCase):
    def setUp(self):
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        with open(f"{cur_dir}/data/build.ninja", "r") as f:
            raw_ninja = f.readlines()

        self.current_dir = cur_dir
        self.raw_file = raw_ninja

    def test_parse_simple_file(self):
        parser = NinjaParser()
        parser.parse(self.raw_file, f"{self.current_dir}/data")
        levels = getToplevels(parser)
        self.assertEqual(1, len(levels))
        self.assertEqual(str(levels[0]), "xarexec_fuse")

    def test_resolveName(self):
        parser = NinjaParser()
        parser.parse(self.raw_file, f"{self.current_dir}/data")
        v = parser._resolveName("foobar$cmake_ninja_workdir")
        v2 = parser._resolveName("foo${cmake_ninja_workdir}bar")
        self.assertEqual(v, "foobartmp.1STpxdK06d")
        self.assertEqual(v2, "footmp.1STpxdK06dbar")

    def test_printgraph(self):
        parser = NinjaParser()
        parser.parse(self.raw_file, f"{self.current_dir}/data")
        top_levels = getToplevels(parser)
        with contextlib.redirect_stdout(None):
            top_levels[0].print_graph()


if __name__ == "__main__":
    unittest.main()
