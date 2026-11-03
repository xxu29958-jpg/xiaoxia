"""Counterexamples for the inventory contract; no application imports required."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.quality.inventory import collect
from tools.quality.metrics import physical_lines, python_metrics, threshold
from tools.quality.ownership import file_kind, responsibility

POLICY = json.loads((Path(__file__).parents[1] / "policy.json").read_text())


class MeasurementTests(unittest.TestCase):
    def test_physical_lines_include_comments_not_phantom_terminal_line(self):
        self.assertEqual(physical_lines("# comment\r\n\r\na = 1\r\n"), 3)
        self.assertEqual(physical_lines(""), 0)

    def test_decorated_async_and_nested_spans_are_not_added(self):
        source = "@deco\nasync def outer():\n    # comment\n    def inner():\n        pass\n    return inner\n"
        found = python_metrics(source, "example.py")["symbols"]
        self.assertEqual(
            [(s["name"], s["lines"]) for s in found], [("outer", 6), ("outer.inner", 2)]
        )

    def test_nested_function_control_flow_does_not_inflate_parent(self):
        source = "def f():\n    def g():\n        if True:\n            while False:\n                pass\n    return g\n"
        found = python_metrics(source, "example.py")["symbols"]
        self.assertEqual([s["control_nesting"] for s in found], [0, 2])

    def test_parse_error_is_reported_without_importing_source(self):
        self.assertIn("parse_error", python_metrics("def broken(", "broken.py"))
        found = python_metrics("raise RuntimeError('must not execute')", "safe.py")
        self.assertEqual(found["symbols"], [])

    def test_elif_is_a_peer_branch_not_an_additional_nesting_level(self):
        source = (
            "def f(x):\n    if x == 1:\n        pass\n    elif x == 2:\n        pass\n"
        )
        self.assertEqual(
            python_metrics(source, "x.py")["symbols"][0]["control_nesting"], 1
        )

    def test_threshold_is_strictly_above_limit(self):
        limits = {"warn_above": 500, "review_above": 800}
        self.assertIsNone(threshold(500, limits))
        self.assertEqual(threshold(501, limits), "warn")
        self.assertEqual(threshold(800, limits), "warn")
        self.assertEqual(threshold(801, limits), "review")

    def test_plugins_and_modified_vendor_keep_maintenance_coverage(self):
        for name in (
            "data/plugins/astrbot_plugin_memory_companion/main.py",
            "vendor/seed_vc/inference.py",
        ):
            self.assertFalse(responsibility(name, "new", POLICY, {})["size_exempt"])
            self.assertEqual(file_kind(name), "production")
        self.assertEqual(file_kind("data/plugins/foo/tests/test_voice.py"), "test")
        self.assertEqual(file_kind("vendor/index_tts/cli_tests/test_cli.py"), "test")

    def test_only_exact_upstream_content_can_be_exempt(self):
        source = {"astrbot/core/example.py": "original"}
        self.assertTrue(
            responsibility("astrbot/core/example.py", "original", POLICY, source)[
                "size_exempt"
            ]
        )
        self.assertFalse(
            responsibility("astrbot/core/example.py", "changed", POLICY, source)[
                "size_exempt"
            ]
        )


class GitIsolationTests(unittest.TestCase):
    def test_immutable_snapshot_and_index_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args])

            git("init", "-q")
            source = root / "example.py"
            source.write_text("value = 1\n", encoding="utf-8")
            git("add", "example.py")
            git(
                "-c",
                "user.name=Quality test",
                "-c",
                "user.email=quality@example.invalid",
                "commit",
                "-qm",
                "fixture",
            )
            source.write_text("value = 2\n", encoding="utf-8")
            git("add", "example.py")
            (root / "private.db").write_bytes(b"must not be read")
            before = git("status", "--porcelain=v2")
            index_before = (root / ".git/index").read_bytes()
            report = collect(root, "HEAD", POLICY, {})
            self.assertEqual([r["path"] for r in report["files"]], ["example.py"])
            self.assertEqual(
                report["files"][0]["blob"],
                git("rev-parse", "HEAD:example.py").decode().strip(),
            )
            self.assertEqual(index_before, (root / ".git/index").read_bytes())
            self.assertEqual(before, git("status", "--porcelain=v2"))
            self.assertEqual(source.read_text(), "value = 2\n")


if __name__ == "__main__":
    unittest.main()
