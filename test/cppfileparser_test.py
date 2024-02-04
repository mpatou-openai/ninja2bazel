import os
import sys
import unittest
from unittest.mock import mock_open, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cppfileparser import findIncludes, parseIncludes  # noqa: E402


def mock_dirname(path: str) -> str:
    if path.endswith("/"):
        return path[:-1]
    v = path.split("/")
    return "/".join(v[:-1])


def mock_abspath(name: str) -> str:
    base_fake_dir = "/fake_dir"
    # we will not try to resolve things like ".." yet
    # the only time we will face that is when we have that in includes path
    # ie. -I..
    if not name.startswith("/"):
        return f"{base_fake_dir}/{name}"
    if name.startswith("./"):
        return f"{base_fake_dir}/{name[:-2]}"

    if name.startswith("../"):
        # FIXME
        return None

    return name


class TestParseIncludesTests(unittest.TestCase):
    def test_single_include(self):
        includes = "-I/path/to/include"
        expected_result = ["/path/to/include"]
        self.assertEqual(parseIncludes(includes), set(expected_result))

    def test_multiple_includes(self):
        includes = "-I/path/to/include1 -I/path/to/include2 -I/path/to/include3"
        expected_result = [
            "/path/to/include1",
            "/path/to/include2",
            "/path/to/include3",
        ]
        self.assertEqual(parseIncludes(includes), set(expected_result))

    def test_no_includes(self):
        includes = ""
        expected_result = []
        self.assertEqual(parseIncludes(includes), set(expected_result))

    def test_include_with_spaces(self):
        includes = "-I/path/with spaces -I/path/with\ttabs"
        expected_result = ["/path/with spaces", "/path/with\ttabs"]
        self.assertEqual(parseIncludes(includes), set(expected_result))

    def test_include_with_duplicates(self):
        includes = "-I/path/to/include -I/path/to/include -I/path/to/include"
        expected_result = ["/path/to/include"]
        self.assertEqual(parseIncludes(includes), set(expected_result))

    def test_include_with_invalid_format(self):
        includes = "-I/path/to/include -I /path/to/include -I/missing/space"
        expected_result = ["/path/to/include", "/missing/space"]
        self.assertEqual(parseIncludes(includes), set(expected_result))

    def test_include_with_trailing_space(self):
        includes = "-I/path/to/include "
        expected_result = ["/path/to/include"]
        self.assertEqual(parseIncludes(includes), set(expected_result))


class FindIncludesTestCase(unittest.TestCase):
    def test_matching_file_same_directory(self):
        name = "test_file.cpp"
        includes_txt = ['#include "test_include.h"', "#define i_love_cpp"]
        expected_result = ["/fake_dir/test_include.h"]
        m = [mock_open(read_data=c).return_value for c in includes_txt]

        @patch("cppfileparser.parseIncludes", return_value=["test_directory"])
        @patch("os.path.exists", return_value=True)
        @patch("os.path.dirname", side_effect=mock_dirname)
        @patch("os.path.abspath", side_effect=mock_abspath)
        @patch("builtins.open", new_callable=mock_open)
        def inner_func(
            mock_open, mock_abspath, mock_dirname, mock_exists, mock_parseIncludes
        ):
            mock_open.side_effect = m
            result = findIncludes(name, "does_not_matter")
            self.assertEqual(expected_result, result)

        inner_func()

    def test_matching_file_includes_dirs_double_quote(self):
        name = "test_file.cpp"
        expected_result = ["/fake_dir/other_dir2/test_include.h"]

        def mock_path_exists(path: str):
            if path == "/fake_dir/test_include.h":
                return False
            if path == "/fake_dir/other_dir/test_include.h":
                return False
            if path == "/fake_dir/other_dir2/test_include.h":
                return True
            return None

        includes_txt = ['#include "test_include.h"', "#define i_love_cpp"]
        m = [mock_open(read_data=c).return_value for c in includes_txt]
        opener = mock_open()
        opener.side_effect = m
        with patch(
            "cppfileparser.parseIncludes", return_value=["other_dir", "other_dir2"]
        ):
            with patch("os.path.exists", mock_path_exists):
                with patch("builtins.open", opener):
                    with patch("os.path.dirname", mock_dirname):
                        with patch("os.path.abspath", mock_abspath):
                            result = findIncludes(name, "does_not_matter")

        self.assertEqual(expected_result, result)

    def test_matching_file_includes_dirs(self):
        name = "test_file.cpp"
        expected_result = ["/fake_dir/other_dir2/test_include.h"]

        def mock_path_exists(path: str):
            if path == "/fake_dir/include_dir/test_include.h":
                return False
            if path == "/fake_dir/other_dir/test_include.h":
                return False
            if path == "/fake_dir/other_dir2/test_include.h":
                return True
            return False

        includes_txt = ["#include <test_include.h>", "#define i_love_cpp"]
        m = [mock_open(read_data=c).return_value for c in includes_txt]
        opener = mock_open()
        opener.side_effect = m
        with patch(
            "cppfileparser.parseIncludes", return_value=["other_dir", "other_dir2"]
        ):
            with patch("os.path.exists", mock_path_exists):
                with patch("builtins.open", opener):
                    with patch("os.path.dirname", mock_dirname):
                        with patch("os.path.abspath", mock_abspath):
                            result = findIncludes(name, "does_not_matter")

        self.assertEqual(expected_result, result)

    def test_no_matching_file_same_directory(self):
        name = "test_file.cpp"
        includes = '#include "test_include.h"'
        expected_result = []

        with patch("cppfileparser.parseIncludes", return_value=["test_directory"]):
            with patch("os.path.exists", return_value=False):
                with patch("os.path.dirname", mock_dirname):
                    with patch("os.path.abspath", mock_abspath):
                        with patch("builtins.open", mock_open(read_data=includes)):
                            result = findIncludes(name, "-Itest_directory")

        self.assertEqual(result, expected_result)

    def test_no_matching_file_includes_dirs_none(self):
        name = "test_file.cpp"
        includes = "#include <test_include.h>"
        expected_result = []

        with patch("cppfileparser.parseIncludes", return_value=["include_dir"]):
            with patch("os.path.exists", return_value=False):
                with patch("os.path.dirname", mock_dirname):
                    with patch("os.path.abspath", mock_abspath):
                        with patch("builtins.open", mock_open(read_data=includes)):
                            result = findIncludes(name, None)

        self.assertEqual(result, expected_result)

    def test_no_matching_file_includes_dirs(self):
        name = "test_file.cpp"
        includes = "#include <test_include.h>"
        expected_result = []

        with patch("cppfileparser.parseIncludes", return_value=["include_dir"]):
            with patch("os.path.exists", return_value=False):
                with patch("os.path.dirname", mock_dirname):
                    with patch("os.path.abspath", mock_abspath):
                        with patch("builtins.open", mock_open(read_data=includes)):
                            result = findIncludes(name, includes)

        self.assertEqual(result, expected_result)

    def test_multi_include_files(self):
        name = "test_file.cpp"
        includes = '\n#include "test_include3.h"'  # noqa E501
        expected_result = [
            "/fake_dir/test_include.h",
            "/fake_dir/include_dir/test_include2.h",
            "/fake_dir/include_dir/test_include3.h",
        ]

        def mock_path_exists(path: str) -> bool:
            if path == "include_dir/test_include3.h":
                return False
            return True

        includes_txt = [
            '#include "test_include.h"' + "\n#include <test_include2.h>\n",
            "#define i_love_cpp",
            '#include "test_include3.h"',
            "#define i_love_cpp_more",
        ]
        m = [mock_open(read_data=c).return_value for c in includes_txt]
        opener = mock_open()
        opener.side_effect = m
        with patch(
            "cppfileparser.parseIncludes", return_value=["include_dir", "other_dir2"]
        ):
            with patch("os.path.exists", mock_path_exists):
                with patch("builtins.open", opener):
                    with patch("os.path.dirname", mock_dirname):
                        with patch("os.path.abspath", mock_abspath):
                            result = findIncludes(name, "does_not_matter")

        self.assertEqual(expected_result, result)


#
# Add more test cases as needed...


if __name__ == "__main__":
    unittest.main()
