import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bazel import BazelBuild, BazelTarget  # noqa: E402


class TestBazelTests(unittest.TestCase):
    def testBazelBuild(self):
        b = BazelTarget("cc_library", "foo")
        b.addSrc("foo/bar/baz.c")
        b.addHdr("foo/bar/baz.h")

        b2 = BazelTarget("cc_binary", "foo")
        b2.addSrc("foo/bar/foo.c")
        # Add the same header to the binary and to the library
        # it should be ignored
        b2.addHdr("foo/bar/baz.h")
        b2.addDep(b)

        hdrs = list(b2.getAllHeaders(True))
        self.assertEqual(len(hdrs), 1)

        hdrs = list(b2.getAllHeaders(False))
        self.assertEqual(len(hdrs), 2)

        out = b2.__repr__()
        self.assertEqual(
            out, "cc_binary(foo) SRCS[foo/bar/foo.c] HDRS[foo/bar/baz.h] DEPS[foo]"
        )

        bz = BazelBuild()
        bz.bazelTargets.append(b2)
        expected = [
            "cc_binary(",
            '    name = "foo",',
            "    srcs = [",
            '        "foo/bar/foo.c",',
            "    ],",
            "    deps = [",
            '        ":libfoo",',
            "    ],",
            ")",
            "",
        ]
        out = bz.genBazelBuildContent()
        self.assertEqual(out, "\n".join(expected))

    def testBinaryWithDep2(self):
        b = BazelTarget("cc_library", "libfoo2")
        b.addSrc("foo/bar/baz.c")
        b.addHdr("foo/bar/baz.h")

        b2 = BazelTarget("cc_binary", "foo")
        b2.addSrc("foo/bar/foo.c")
        # Add the same header to the binary and to the library
        # it should be ignored
        b2.addHdr("foo/bar/baz.h")
        b2.addDep(b)

        hdrs = list(b2.getAllHeaders(True))
        self.assertEqual(len(hdrs), 1)

        hdrs = list(b2.getAllHeaders(False))
        self.assertEqual(len(hdrs), 2)

        out = b2.__repr__()
        self.assertEqual(
            out, "cc_binary(foo) SRCS[foo/bar/foo.c] HDRS[foo/bar/baz.h] DEPS[libfoo2]"
        )
        out_bazel = b2.asBazel()
        self.assertEqual(
            out_bazel,
            [
                "cc_binary(",
                '    name = "foo",',
                "    srcs = [",
                '        "foo/bar/foo.c",',
                "    ],",
                "    deps = [",
                '        ":libfoo2",',
                "    ],",
                ")",
            ],
        )

    def testBinaryWithDep(self):
        b = BazelTarget("cc_library", "foo")
        b.addSrc("foo/bar/baz.c")
        b.addHdr("foo/bar/baz.h")

        b2 = BazelTarget("cc_binary", "foo")
        b2.addSrc("foo/bar/foo.c")
        # Add the same header to the binary and to the library
        # it should be ignored
        b2.addHdr("foo/bar/baz.h")
        b2.addDep(b)

        hdrs = list(b2.getAllHeaders(True))
        self.assertEqual(len(hdrs), 1)

        hdrs = list(b2.getAllHeaders(False))
        self.assertEqual(len(hdrs), 2)

        out = b2.__repr__()
        self.assertEqual(
            out, "cc_binary(foo) SRCS[foo/bar/foo.c] HDRS[foo/bar/baz.h] DEPS[foo]"
        )
        out_bazel = b2.asBazel()
        self.assertEqual(
            out_bazel,
            [
                "cc_binary(",
                '    name = "foo",',
                "    srcs = [",
                '        "foo/bar/foo.c",',
                "    ],",
                "    deps = [",
                '        ":libfoo",',
                "    ],",
                ")",
            ],
        )

    def testSimpleTargetLib(self):
        b = BazelTarget("cc_library", "foo")
        b.addSrc("foo/bar/baz.c")
        b.addHdr("foo/bar/baz.h")

        hdrs = list(b.getAllHeaders(True))
        self.assertEqual(len(hdrs), 0)

        hdrs = list(b.getAllHeaders(False))
        self.assertEqual(len(hdrs), 1)

        out = b.__repr__()
        self.assertEqual(out, "cc_library(foo) SRCS[foo/bar/baz.c] HDRS[foo/bar/baz.h]")

        out_bazel = b.asBazel()
        self.assertEqual(
            out_bazel,
            [
                "cc_library(",
                '    name = "libfoo",',
                "    srcs = [",
                '        "foo/bar/baz.c",',
                "    ],",
                "    hdrs = [",
                '        "foo/bar/baz.h",',
                "    ],",
                ")",
            ],
        )

    def testSimpleTarget(self):
        b = BazelTarget("cc_binary", "foo")
        b.addSrc("foo/bar/baz.c")
        b.addHdr("foo/bar/baz.h")

        hdrs = list(b.getAllHeaders(True))
        self.assertEqual(len(hdrs), 0)

        hdrs = list(b.getAllHeaders(False))
        self.assertEqual(len(hdrs), 1)

        out = b.__repr__()
        self.assertEqual(out, "cc_binary(foo) SRCS[foo/bar/baz.c] HDRS[foo/bar/baz.h]")

        out_bazel = b.asBazel()
        self.assertEqual(
            out_bazel,
            [
                "cc_binary(",
                '    name = "foo",',
                "    srcs = [",
                '        "foo/bar/baz.c",',
                '        "foo/bar/baz.h",',
                "    ],",
                ")",
            ],
        )


if __name__ == "__main__":
    unittest.main()
