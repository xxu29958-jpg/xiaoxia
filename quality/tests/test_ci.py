"""Actual process, source-consumer and skipped-job counterexamples for cloud CI."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.quality.ci_results import job_result, qualify
from tools.quality.external_checks import execute
from tools.quality.impact import consumers, plan


class ResultTests(unittest.TestCase):
    def test_real_process_failure_missing_executable_and_timeout_are_distinct(self):
        failed = execute(Path.cwd(), [sys.executable, "-c", "raise SystemExit(7)"])
        missing = execute(Path.cwd(), ["xiaoxia-nonexistent-test-executable"])
        timeout = execute(
            Path.cwd(), [sys.executable, "-c", "import time; time.sleep(5)"], timeout=1
        )
        self.assertEqual(
            [r["status"] for r in (failed, missing, timeout)],
            ["fail", "not_executed", "timeout"],
        )
        self.assertTrue(
            all(r["elapsed_seconds"] >= 0 for r in (failed, missing, timeout))
        )

    def test_actual_unittest_skip_cannot_be_an_all_pass(self):
        result = execute(
            Path.cwd(),
            [
                sys.executable,
                "-c",
                "import unittest\nclass T(unittest.TestCase):\n @unittest.skip('missing target')\n def test_skipped(self): pass\nunittest.main()",
            ],
        )
        result["command"].append("unittest")
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(qualify([result]))
        self.assertEqual(result["tests"]["status"], "partial")

    def test_missing_or_failed_job_never_passes_even_with_zero_exit_elsewhere(self):
        jobs: dict[str, str] = dict.fromkeys(
            ("plan", "g1", "g2", "metrics", "candidate"), "success"
        )
        for state in ("failure", "skipped", "cancelled", ""):
            self.assertEqual(
                job_result({**jobs, "g2": state}, "true")["status"], "fail"
            )
        self.assertEqual(
            job_result({**jobs, "g2": "skipped"}, "false")["status"], "pass"
        )
        self.assertEqual(job_result(jobs, "true")["g3"], "not_executed")
        self.assertFalse(
            qualify(
                [{"command": ["pytest"], "exit_code": 0, "stdout": "", "stderr": ""}]
            )
        )


class ImpactTests(unittest.TestCase):
    def test_plugin_alias_and_transitive_consumers_are_resolved(self):
        state = "data/plugins/astrbot_plugin_memory_companion/core/appraisal_store.py"
        chat = "data/plugins/astrbot_plugin_ada_companion/appraisals.py"
        native = "tests/cognition_state/test_native.py"
        found = consumers(
            {
                state: b"value=1",
                chat: b"from astrbot_plugin_memory_companion.core.appraisal_store import value",
                native: b"from astrbot_plugin_ada_companion.appraisals import value",
            },
            [state],
        )
        self.assertEqual(found[native], [state, chat, native])

    def test_unmapped_new_source_expands_and_keeps_gap(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            subprocess.run(["git", "init", "-q", root], check=True)
            (repo / "known.py").write_text("value=1\n")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Quality",
                    "-c",
                    "user.email=quality@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            (repo / "unknown.py").write_text("raise RuntimeError('never import me')\n")
            report = plan(repo, "HEAD")
            self.assertIn("unknown.py", report["coverage_gaps"])
            self.assertTrue(report["expanded_checks"])
            self.assertIn("cognition_contracts", report["groups"])
            self.assertIn("reading_contracts", report["groups"])
            self.assertNotIn("never import me", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
