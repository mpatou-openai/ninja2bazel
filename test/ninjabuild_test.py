import os
import sys
import unittest
from unittest.mock import MagicMock, Mock, call, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ninjabuild import Rule  # noqa: E402
from ninjabuild import Build, BuildTarget, genBazelBuildFiles, getBuildTargets


class TestVisitGraph(unittest.TestCase):
    def setUp(self):
        self.mock_visitor = Mock()
        self.mock_context = {"setup_subcontext": Mock(return_value="subcontext")}

    def test_visit_graph_with_file(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = True

        build_target.visitGraph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)

    def test_visit_graph_with_non_file_and_non_phony_rule(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = False
        b = Build([build_target], Rule("non-phony"), [], [])
        build_target.producedby = b

        build_target.visitGraph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)

    def test_visit_graph_with_phony_rule_and_empty_inputs_and_depends_is_file(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = True
        b = Build([build_target], Rule("phony"), [], [])
        build_target.producedby = b

        build_target.visitGraph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)

    def test_visit_graph_with_phony_rule_and_empty_inputs_and_depends(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = False
        b = Build([build_target], Rule("phony"), [], [])
        build_target.producedby = b

        build_target.visitGraph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_not_called()

    def test_visit_graph_with_phony_rule_and_empty_inputs_and_depends_and_produced(
        self,
    ):
        build_target = BuildTarget("foo")
        build_target.is_a_file = False
        b = Build([build_target], Rule("phony"), [], [])
        build_target.producedby = b
        build_target.producedby.inputs = [
            Mock(name="pouet", depsAreVirtual=Mock(return_value=False))
        ]
        build_target.producedby.depends = [
            Mock(depsAreVirtual=Mock(return_value=False))
        ]

        build_target.visitGraph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)
        self.assertEqual(
            build_target.producedby.inputs[0].visitGraph.call_args_list,
            [call(self.mock_visitor, "subcontext")],
        )
        self.assertEqual(
            build_target.producedby.depends[0].visitGraph.call_args_list,
            [call(self.mock_visitor, "subcontext")],
        )

    def test_visit_graph_with_phony_rule_depends_produced_empty_inputs_and_depends(
        self,
    ):
        build_target = BuildTarget("foo")
        b = Build([build_target], Rule("phony"), [], [])
        build_target.is_a_file = False
        build_target.producedby = b

        build_target2 = BuildTarget("foo2")
        b2 = Build([build_target2], Rule("phony"), [], [build_target])
        build_target2.is_a_file = False
        build_target2.producedby = b2
        build_target2.producedby.inputs = [
            Mock(name="pouet", depsAreVirtual=Mock(return_value=False))
        ]
        b3 = Build([], Rule("phony"), [], [])
        build_target.producedby.depends = [
            Mock(
                depsAreVirtual=Mock(return_value=False),
                producedby=b3,
            )
        ]

        def foo(x):
            return x

        build_target2.visitGraph(self.mock_visitor, {"setup_subcontext": foo})

        self.mock_visitor.assert_called_with(build_target2, {"setup_subcontext": foo})
        self.assertEqual(
            build_target2.producedby.inputs[0].visitGraph.call_args_list,
            [call(self.mock_visitor, {"setup_subcontext": foo})],
        )
        self.assertEqual(
            build_target.producedby.depends[0].visitGraph.call_args_list,
            [],
        )


def mock_isdir_func(dirname: str) -> bool:
    if dirname == "CMakeFiles/Logging.dir":
        return True
    if dirname == "CMakeFiles/XarHelperLib.dir":
        return True

    return False


FAKE_BASE_DIRECTORY = "/testing/xar"


def mock_exists_func(filename: str) -> bool:
    if filename.startswith(f"{FAKE_BASE_DIRECTORY}/") and (
        filename.endswith(".cpp") or filename.endswith(".h")
    ):
        return True

    if filename.endswith(".o") or filename.endswith(".util") or filename.endswith(".a"):
        return False

    return False


orig_open = open


class MyOpen:
    def __init__(self, filename, mode):
        self.filename = filename
        self.mode = mode
        self.file = None

    def __enter__(self):
        if self.filename == f"{FAKE_BASE_DIRECTORY}/Logging.cpp":
            mock_open = MagicMock()
            mock_open.readlines.return_value = ['#include "Logging.h"']
            return mock_open
        if self.filename in [
            f"{FAKE_BASE_DIRECTORY}/XarHelpers.cpp",
            f"{FAKE_BASE_DIRECTORY}/XarLinux.cpp",
            f"{FAKE_BASE_DIRECTORY}/XarExecFuse.cpp",
        ]:
            mock_open = MagicMock()
            mock_open.readlines.return_value = [
                '#include "XarHelpers.h"',
                '#include "Logging.h"',
            ]
            return mock_open

        if self.filename in [
            f"{FAKE_BASE_DIRECTORY}/XarHelpers.h",
            f"{FAKE_BASE_DIRECTORY}/Logging.h",
        ]:
            mock_open = MagicMock()
            mock_open.readlines.return_value = [
                "#define foo",
                "int main(void) { return 1; }",
            ]
            return mock_open

        self.file = orig_open(self.filename, self.mode)
        return self.file

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file:
            self.file.close()


class MyOpenCM:
    def __call__(self, *args):
        return MyOpen(*args)


class TestGetBuildTargets(unittest.TestCase):
    def setUp(self):
        cur_dir = os.path.dirname(os.path.abspath(__file__))
        with open(f"{cur_dir}/data/build.ninja", "r") as f:
            raw_ninja = f.readlines()

        self.current_dir = cur_dir
        self.raw_content = raw_ninja

    @patch("ninjabuild.logging")
    def test_parse_simple_file_missing_deps(self, mock_logging):
        levels = getBuildTargets(self.raw_content, f"{self.current_dir}/data")
        self.assertIsNone(levels, "levels should be none")
        self.assertTrue(mock_logging.error.called)

    @patch("os.path.exists", side_effect=mock_exists_func)
    @patch("os.path.isdir", side_effect=mock_isdir_func)
    @patch("builtins.open", new_callable=MyOpenCM)
    def test_parse_simple_file(self, mock_opener, mock_isdir, mock_exists):
        with patch("ninjabuild.logging"):
            levels = getBuildTargets(self.raw_content, f"{self.current_dir}/data")
            self.assertEqual(1, len(levels))
        c = genBazelBuildFiles(levels, self.current_dir)
        expected = """cc_binary(
    name = "xarexec_fuse",
    srcs = [
        "/testing/xar/XarExecFuse.cpp",
    ],
    deps = [
        ":libLogging",
        ":libXarHelperLib",
    ],
)

cc_library(
    name = "libLogging",
    srcs = [
        "/testing/xar/Logging.cpp",
    ],
    hdrs = [
        "/testing/xar/Logging.h",
    ],
)

cc_library(
    name = "libXarHelperLib",
    srcs = [
        "/testing/xar/Logging.cpp",
        "/testing/xar/XarHelpers.cpp",
        "/testing/xar/XarLinux.cpp",
    ],
    hdrs = [
        "/testing/xar/Logging.h",
        "/testing/xar/XarHelpers.h",
    ],
)
"""
        self.maxDiff = None
        self.assertEqual(expected, c)


if __name__ == "__main__":
    unittest.main()
