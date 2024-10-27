import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bazel import BazelTarget, ExportedFile, IncludeDir


class TestBazelTarget(unittest.TestCase):
    def test_simple_target_lib(self):
        b = BazelTarget("cc_library", "foo", "cpp")
        b.addSrc(ExportedFile("foo/bar/baz.c", "cpp"))
        b.addHdr(ExportedFile("foo/bar/baz.h", "cpp"))

        hdrs = list(b.getAllHeaders(True))
        self.assertEqual(len(hdrs), 0)

        hdrs = list(b.getAllHeaders(False))
        self.assertEqual(len(hdrs), 1)

        out = b.__repr__()
        self.assertEqual(out, "cc_library(foo) SRCS[foo/bar/baz.c] HDRS[foo/bar/baz.h]")

        out_bazel = b.asBazel()
        expected_bazel = [
            "cc_library(",
            '    name = "libfoo",',
            "    srcs = [",
            '        "foo/bar/baz.c",',
            "    ],",
            "    hdrs = [",
            '        "foo/bar/baz.h",',
            "    ],",
            ")",
        ]
        self.assertEqual(out_bazel, expected_bazel)

    def test_simple_target(self):
        b = BazelTarget("cc_binary", "foo", "cpp")
        b.addSrc(ExportedFile("foo/bar/baz.c", "cpp"))
        b.addHdr(ExportedFile("foo/bar/baz.h", "cpp"))

        hdrs = list(b.getAllHeaders(True))
        self.assertEqual(len(hdrs), 0)

        hdrs = list(b.getAllHeaders(False))
        self.assertEqual(len(hdrs), 1)

        out = b.__repr__()
        self.assertEqual(out, "cc_binary(foo) SRCS[foo/bar/baz.c] HDRS[foo/bar/baz.h]")

        out_bazel = b.asBazel()
        expected_bazel = [
            "cc_binary(",
            '    name = "foo",',
            "    srcs = [",
            '        "foo/bar/baz.c",',
            '        "foo/bar/baz.h",',
            "    ],",
            ")",
        ]
        self.assertEqual(out_bazel, expected_bazel)

    def test_add_define(self):
        b = BazelTarget("cc_library", "foo", "cpp")
        b.addDefine("DEBUG=1")
        self.assertIn("DEBUG=1", b.defines)

        out_bazel = b.asBazel()
        expected_bazel = [
            "cc_library(",
            '    name = "libfoo",',
            "    defines = [",
            "        DEBUG=1,",
            "    ],",
            ")",
        ]
        self.assertEqual(out_bazel, expected_bazel)

    def test_add_include_dir(self):
        b = BazelTarget("cc_library", "foo", "cpp")
        include_dir = IncludeDir(("foo/include", False))
        b.addIncludeDir(include_dir)
        self.assertIn(include_dir, b.includeDirs)

        out_bazel = b.asBazel()
        expected_bazel = [
            "cc_library(",
            '    name = "libfoo",',
            "    copts = [",
            '        "-I{}".format("foo/include"),',
            "    ],",
            ")",
        ]
        self.assertEqual(out_bazel, expected_bazel)

    def test_add_dep(self):
        b = BazelTarget("cc_library", "foo", "cpp")
        dep = BazelTarget("cc_library", "bar", "cpp")
        b.addDep(dep)
        self.assertIn(dep, b.deps)

        out_bazel = b.asBazel()
        expected_bazel = [
            "cc_library(",
            '    name = "libfoo",',
            "    deps = [",
            '        ":libbar",',
            "    ],",
            ")",
        ]

        self.assertEqual(out_bazel, expected_bazel)

    def test_add_needed_generated_files(self):
        b = BazelTarget("cc_library", "foo", "cpp")
        b.addNeededGeneratedFiles("generated/file.h")
        self.assertIn("generated/file.h", b.neededGeneratedFiles)


if __name__ == "__main__":
    unittest.main()
