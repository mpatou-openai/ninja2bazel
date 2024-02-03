import os
import sys
import unittest
from unittest.mock import Mock, call

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ninjabuild import Build, BuildTarget, Rule  # noqa: E402


class TestVisitGraph(unittest.TestCase):
    def setUp(self):
        self.mock_visitor = Mock()
        self.mock_context = {"setup_subcontext": Mock(return_value="subcontext")}

    def test_visit_graph_with_file(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = True

        build_target.visit_graph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)

    def test_visit_graph_with_non_file_and_non_phony_rule(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = False
        b = Build([build_target], Rule("non-phony"), [], [])
        build_target.producedby = b

        build_target.visit_graph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)

    def test_visit_graph_with_phony_rule_and_empty_inputs_and_depends_is_file(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = True
        b = Build([build_target], Rule("phony"), [], [])
        build_target.producedby = b

        build_target.visit_graph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)

    def test_visit_graph_with_phony_rule_and_empty_inputs_and_depends(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = False
        b = Build([build_target], Rule("phony"), [], [])
        build_target.producedby = b

        build_target.visit_graph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_not_called()

    def test_visit_graph_with_phony_rule_and_non_empty_inputs_and_depends(self):
        build_target = BuildTarget("foo")
        build_target.is_a_file = False
        b = Build([build_target], Rule("phony"), [], [])
        build_target.producedby = b
        build_target.producedby.inputs = [Mock(depsAreVirtual=Mock(return_value=False))]
        build_target.producedby.depends = [
            Mock(depsAreVirtual=Mock(return_value=False))
        ]

        build_target.visit_graph(self.mock_visitor, self.mock_context)

        self.mock_visitor.assert_called_once_with(build_target, self.mock_context)
        self.assertEqual(
            build_target.producedby.inputs[0].visit_graph.call_args_list,
            [call(self.mock_visitor, "subcontext")],
        )
        self.assertEqual(
            build_target.producedby.depends[0].visit_graph.call_args_list,
            [call(self.mock_visitor, "subcontext")],
        )


if __name__ == "__main__":
    unittest.main()
