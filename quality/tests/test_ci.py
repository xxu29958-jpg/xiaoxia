"""Actual process, source-consumer and skipped-job counterexamples for cloud CI."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.quality.changes import Change
from tools.quality.ci_results import job_result, qualify, selection_audit
from tools.quality.external_checks import execute
from tools.quality.impact import consumers, plan
from tools.quality.test_selection import required_tests


class ResultTests(unittest.TestCase):
    def test_full_regression_identifies_failure_missed_by_incremental_selection(self):
        plan = {
            "status": "planned",
            "groups": ["cognition", "reading"],
            "incremental_groups": ["cognition"],
            "full_regression": True,
            "head": "candidate",
            "base": "baseline",
        }
        missing = selection_audit(plan, {"cognition": {"status": "pass"}})
        self.assertEqual(missing["required_but_not_executed"], ["reading"])
        failed = selection_audit(
            plan, {"cognition": {"status": "pass"}, "reading": {"status": "fail"}}
        )
        self.assertEqual(failed["failures_outside_incremental_selection"], ["reading"])
        self.assertEqual(failed["status"], "fail")
        self.assertEqual(
            selection_audit(
                plan,
                dict.fromkeys(
                    plan["groups"],
                    {
                        "status": "pass",
                        "head": "candidate",
                        "base": "baseline",
                        "stable_candidate": True,
                    },
                ),
            )["status"],
            "pass",
        )

    def test_real_process_failure_missing_executable_and_timeout_are_distinct(self):
        failed = execute(Path.cwd(), [sys.executable, "-c", "raise SystemExit(7)"])
        missing = execute(Path.cwd(), ["xiaoxia-nonexistent-test-executable"])
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "test_wait.py").write_text(
                "import sys,time,unittest\n"
                "class Wait(unittest.TestCase):\n"
                " def test_wait(self):\n"
                "  print('partial output',flush=True)\n"
                "  print('failure diagnostic',file=sys.stderr,flush=True)\n"
                "  time.sleep(5)\n",
                encoding="utf-8",
            )
            timeout = execute(
                Path(root),
                [sys.executable, "-B", "-m", "unittest", "discover", "-v"],
                timeout=1,
            )
        self.assertEqual(
            [r["status"] for r in (failed, missing, timeout)],
            ["fail", "not_executed", "timeout"],
        )
        self.assertTrue(
            all(r["elapsed_seconds"] >= 0 for r in (failed, missing, timeout))
        )
        self.assertEqual(timeout["exit_code"], 124)
        self.assertEqual(timeout["timeout_seconds"], 1)
        self.assertIn("partial output", timeout["stdout"])
        self.assertIn("failure diagnostic", timeout["stderr"])
        self.assertIn("Command timed out", timeout["stderr"])
        self.assertFalse(qualify([timeout]))
        self.assertEqual(timeout["tests"]["status"], "incomplete")
        self.assertIsNone(timeout["tests"]["executed"])
        json.dumps(timeout)

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
    def test_cognition_runner_changes_select_its_actual_consumer_group(self):
        for path in (
            "tools/quality/cognition_checks.py",
            "tools/quality/pytest_progress.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    required_tests([Change(path, None, "M")])["cognition_contracts"]
                )

    def test_overlapping_changes_keep_each_origins_real_consumer(self):
        state = "state.py"
        chat = "data/plugins/astrbot_plugin_ada_companion/appraisals.py"
        native = "tests/cognition_state/test_native.py"
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            subprocess.run(["git", "init", "-q", root], check=True)
            sources = {
                state: "value = 1\n",
                chat: "from state import value\n",
                native: "from astrbot_plugin_ada_companion.appraisals import value\n",
            }
            for path, content in sources.items():
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text(content)
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
            for path, content in sources.items():
                (repo / path).write_text(content + "changed = True\n")
            report = plan(repo, "HEAD")
            self.assertEqual(report["coverage_gaps"], [])
            self.assertIn("cognition_contracts", report["groups"])

    def test_model_identity_and_wire_protocol_select_the_real_contracts(self):
        selected = required_tests(
            [
                Change("models.manifest.json", "models.manifest.json", "M"),
                Change("services/voice/job_api.py", "services/voice/job_api.py", "M"),
            ]
        )
        self.assertTrue(selected["installation_contracts"])
        self.assertTrue(selected["media_job_contracts"])

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
