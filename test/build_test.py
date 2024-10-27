import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.append(sys.path[0])
sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from build import (Build, BuildFileGroupingStrategy, BuildTarget, Rule,
                   TargetType, TopLevelGroupingStrategy)

sys.modules["bazel"] = Mock()
sys.modules["visitor"] = Mock()


# Now, write the unit tests
class TestBuildTarget(unittest.TestCase):
    def test_init(self):
        bt = BuildTarget(name="target1", shortName="t1")
        self.assertEqual(bt.name, "target1")
        self.assertEqual(bt.shortName, "t1")
        self.assertFalse(bt.implicit)
        self.assertIsNone(bt.producedby)
        self.assertEqual(bt.usedbybuilds, [])
        self.assertFalse(bt.is_a_file)
        self.assertEqual(bt.type, TargetType.other)
        self.assertEqual(bt.includes, set())
        self.assertEqual(bt.depends, [])
        self.assertEqual(bt.aliases, [])
        self.assertFalse(bt.topLevel)
        self.assertIsNone(bt.opaque)

    def test_markTopLevel(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.markTopLevel()
        self.assertTrue(bt.topLevel)

    def test_hash(self):
        bt1 = BuildTarget(name="target1", shortName="t1")
        bt2 = BuildTarget(name="target1", shortName="t1")
        self.assertEqual(hash(bt1), hash(bt2))

    def test_eq(self):
        bt1 = BuildTarget(name="target1", shortName="t1")
        bt2 = BuildTarget(name="target1", shortName="t1")
        bt3 = BuildTarget(name="target2", shortName="t2")
        self.assertEqual(bt1, bt2)
        self.assertNotEqual(bt1, bt3)
        self.assertEqual(bt1, "target1")
        self.assertNotEqual(bt1, "target3")

    def test_lt(self):
        bt1 = BuildTarget(name="target1", shortName="t1")
        bt2 = BuildTarget(name="target2", shortName="t2")
        self.assertTrue(bt1 < bt2)
        self.assertFalse(bt2 < bt1)

    def test_setIncludedFiles(self):
        bt = BuildTarget(name="target1", shortName="t1")
        includes = {("file1.h", "/path/to/include")}
        bt.setIncludedFiles(includes)
        self.assertEqual(bt.includes, includes)

    def test_addIncludedFile(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.addIncludedFile(("file1.h", "/path/to/include"))
        self.assertIn(("file1.h", "/path/to/include"), bt.includes)

    def test_setDeps(self):
        bt = BuildTarget(name="target1", shortName="t1")
        dep1 = BuildTarget(name="dep1", shortName="d1")
        bt.setDeps([dep1])
        self.assertEqual(bt.depends, [dep1])

    def test_markAsManual(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.markAsManual()
        self.assertEqual(bt.type, TargetType.manually_generated)

    def test_markAsExternal(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.markAsExternal()
        self.assertEqual(bt.type, TargetType.external)

    def test_markAsUnknown(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.markAsUnknown()
        self.assertEqual(bt.type, TargetType.unknown)

    def test_markAsKnown(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.markAsknown()
        self.assertEqual(bt.type, TargetType.known)

    def test_setOpaque(self):
        bt = BuildTarget(name="target1", shortName="t1")
        obj = object()
        bt.setOpaque(obj)
        self.assertEqual(bt.opaque, obj)

    def test_usedby(self):
        bt = BuildTarget(name="target1", shortName="t1")
        build = Mock()
        bt.usedby(build)
        self.assertIn(build, bt.usedbybuilds)

    def test_markAsFile(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.markAsFile()
        self.assertTrue(bt.is_a_file)
        self.assertEqual(bt.type, TargetType.known)

    def test_isOnlyUsedBy(self):
        bt = BuildTarget(name="target1", shortName="t1")
        build1 = Mock()
        build1.outputs = [BuildTarget(name="output1", shortName="o1")]
        build2 = Mock()
        build2.outputs = [BuildTarget(name="output2", shortName="o2")]
        bt.usedbybuilds = [build1, build2]
        self.assertFalse(bt.isOnlyUsedBy(["output1"]))
        self.assertTrue(bt.isOnlyUsedBy(["output1", "output2"]))

    def test_depsAreVirtual(self):
        bt = BuildTarget(name="target1", shortName="t1")
        bt.is_a_file = False
        bt.type = TargetType.other
        bt.producedby = Mock()
        bt.producedby.depends = []
        self.assertFalse(bt.depsAreVirtual())

    def test_repr(self):
        bt = BuildTarget(name="target1", shortName="t1")
        self.assertEqual(repr(bt), "target1")

    def test_str(self):
        bt = BuildTarget(name="target1", shortName="t1")
        self.assertEqual(str(bt), "target1")


class TestBuild(unittest.TestCase):
    def setUp(self):
        # Mock the BuildTarget and Rule
        self.output1 = BuildTarget(name="output1", shortName="o1")
        self.input1 = BuildTarget(name="input1", shortName="i1")
        self.dep1 = BuildTarget(name="dep1", shortName="d1")
        self.rule = Rule(name="compile")

    def test_init(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[self.input1],
            depends=[self.dep1],
        )
        self.assertEqual(build.outputs, [self.output1])
        self.assertEqual(build.rulename, self.rule)
        self.assertEqual(build.inputs, [self.input1])
        self.assertEqual(build.depends, {self.dep1})
        self.assertIsNone(build.associatedBazelTarget)
        self.assertEqual(build.vars, {})
        self.assertEqual(build, self.output1.producedby)

    def test_setAssociatedBazelTarget(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[],
            depends=[],
        )
        bazel_target = Mock()
        build.setAssociatedBazelTarget(bazel_target)
        self.assertEqual(build.associatedBazelTarget, bazel_target)

    def test_getRawcommand(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[],
            depends=[],
        )
        build.rulename.vars = {"COMMAND": "g++ -c input1.cpp"}
        self.assertEqual(build.getRawcommand(), "g++ -c input1.cpp")

    def test_getCoreCommand(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[],
            depends=[],
        )
        build.rulename.vars = {"command": "g++ -c $in -o $out"}
        cmd = build.getCoreCommand()
        self.assertEqual(cmd, ("g++ -c $in -o $out", None))

    def test_resolveName(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[],
            depends=[],
        )
        build.vars = {"VAR1": "value1", "VAR2": "value2"}
        name = "${VAR1}/path/${VAR2}"
        resolved_name = build._resolveName(name)
        self.assertEqual(resolved_name, "value1/path/value2")

    def test_repr(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[self.input1],
            depends=[self.dep1],
        )
        expected_repr = "input1 dep1 => compile => output1"
        self.assertEqual(repr(build), expected_repr)

    def test_canGenerateFinal(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[],
            depends=[],
        )
        build.vars = {"LINK_FLAGS": "-lstdc++"}
        build.rulename.vars = {
            "command": "g++ -o output input.cpp",
        }
        with patch.object(
            Build, "getCoreCommand", return_value=("g++ -o output input.cpp", "dir1")
        ):
            self.assertTrue(build.canGenerateFinal())

    def test_canGenerateFinal_false(self):
        build = Build(
            outputs=[self.output1],
            rulename=self.rule,
            inputs=[],
            depends=[],
        )
        build.rulename.vars = {"command": 'echo "Hello World"'}
        self.assertFalse(build.canGenerateFinal())


class TestBuildFileGroupingStrategy(unittest.TestCase):
    def setUp(self):
        BuildFileGroupingStrategy._instance = None

    def test_strategyName(self):
        strategy = BuildFileGroupingStrategy()
        self.assertEqual(strategy.strategyName(), "default")

    def test_getBuildTarget_not_implemented(self):
        strategy = BuildFileGroupingStrategy()
        with self.assertRaises(NotImplementedError):
            strategy.getBuildTarget("filename", "parentTarget")

    def test_getBuildFilenamePath_not_implemented(self):
        strategy = BuildFileGroupingStrategy()
        with self.assertRaises(NotImplementedError):
            strategy.getBuildFilenamePath("filename")


class TestTopLevelGroupingStrategy(unittest.TestCase):
    def setUp(self):
        BuildFileGroupingStrategy._instance = None

    def test_strategyName(self):
        strategy = TopLevelGroupingStrategy()
        self.assertEqual(strategy.strategyName(), "TopLevelGroupingStrategy")

    def test_getBuildFilenamePath(self):
        strategy = TopLevelGroupingStrategy()
        path = strategy.getBuildFilenamePath("dir1/dir2/file.cpp")
        self.assertEqual(path, "dir1")

    def test_getBuildFilenamePath_single_level(self):
        strategy = TopLevelGroupingStrategy()
        path = strategy.getBuildFilenamePath("file.cpp")
        self.assertEqual(path, "")

    def test_getBuildTarget(self):
        strategy = TopLevelGroupingStrategy()
        target = strategy.getBuildTarget("dir1/dir2/file.cpp", "dir1")
        self.assertEqual(target, ":dir2/file.cpp")

    def test_getBuildTarget_keepPrefix(self):
        strategy = TopLevelGroupingStrategy()
        target = strategy.getBuildTarget("dir1/dir2/file.cpp", "dir1", keepPrefix=True)
        self.assertEqual(target, ":dir2/file.cpp")


# Run the tests
if __name__ == "__main__":
    unittest.main()
