#!/usr/bin/python3
import unittest

from bazel_test import TestBazelTests  # noqa: F401
from cppfileparser_test import FindIncludesTestCase  # noqa: F401
from cppfileparser_test import TestParseIncludesTests  # noqa: F401
from ninjabuild_test import TestGetBuildTargets, TestVisitGraph  # noqa: F401
from parser_test import TestParser  # noqa: F401

if __name__ == "__main__":
    unittest.main()
