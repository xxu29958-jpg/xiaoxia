"""Bound CI artifacts must not hide stale source, missing maps or test execution."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.quality.changes import capture
from tools.quality.ci_checks import check, execution_plan
from tools.quality.impact import plan


class BoundPlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xiaoxia-bound-plan-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.marker = self.root / "executed"
        self.source = self.repo / "tests/installation/test_probe.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(
            "import unittest\nfrom pathlib import Path\n"
            "class Consumer(unittest.TestCase):\n"
            " def test_consumer(self):\n"
            f"  Path({str(self.marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        (self.repo / "quality").mkdir()
        (self.repo / "quality/policy.json").write_text("{}", encoding="utf-8")
        (self.repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.base = self.git("rev-parse", "HEAD")
        self.source.write_text(
            self.source.read_text(encoding="utf-8") + "# changed consumer\n",
            encoding="utf-8",
        )
        self.plan_path = self.root / "plan.json"
        self.report = {**plan(self.repo, self.base), "status": "planned"}
        self.digest = self.write_plan(self.report)

    def git(self, *args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Quality",
                "-c",
                "user.email=quality@example.invalid",
                *args,
            ],
            cwd=self.repo,
            text=True,
        ).strip()

    def write_plan(self, report):
        data = (json.dumps(report) + "\n").encode("utf-8")
        self.plan_path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def run_cli(self, *args, stdlib_only=False):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                *(["-S"] if stdlib_only else []),
                "-m",
                "tools.quality.ci",
                *args,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "HEAD_SHA": self.git("rev-parse", "HEAD"),
                "BASE_SHA": self.base,
                "FULL_REGRESSION": "false",
                "GITHUB_OUTPUT": str(self.root / "github-output"),
            },
        )

    def check(self, digest=None, group="installation_contracts"):
        return check(
            self.repo,
            self.base,
            "g2",
            group,
            plan_path=self.plan_path,
            plan_sha256=self.digest if digest is None else digest,
        )

    def test_planner_and_source_packager_work_without_site_packages(self):
        for action in ("plan", "package"):
            with self.subTest(action=action):
                report = self.root / f"stdlib-{action}.json"
                result = self.run_cli(action, "--report", str(report), stdlib_only=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                payload = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(payload["head"], self.base)
                self.assertEqual(
                    payload["status"], "planned" if action == "plan" else "pass"
                )

    def test_cli_publishes_actual_bytes_and_runs_real_consumer_from_bound_plan(self):
        planned = self.run_cli("plan", "--report", str(self.plan_path))
        self.assertEqual(planned.returncode, 0, planned.stderr)
        outputs = dict(
            line.split("=", 1)
            for line in (self.root / "github-output").read_text().splitlines()
        )
        self.assertEqual(
            outputs["plan_sha256"],
            hashlib.sha256(self.plan_path.read_bytes()).hexdigest(),
        )
        report_path = self.root / "check.json"
        executed = self.run_cli(
            "check",
            "--tier",
            "g2",
            "--group",
            "installation_contracts",
            "--plan",
            str(self.plan_path),
            "--plan-sha256",
            outputs["plan_sha256"],
            "--report",
            str(report_path),
        )
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        result = json.loads(report_path.read_bytes())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["impact_plan"]["source"], "artifact")
        self.assertEqual(result["impact_plan"]["sha256"], outputs["plan_sha256"])
        self.assertEqual(result["commands"][0]["tests"]["executed"], 1)
        self.assertTrue(self.marker.is_file())

    def test_corrupt_artifact_and_missing_digest_cannot_execute_a_consumer(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            self.check(digest="")
        self.plan_path.write_bytes(self.plan_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "digest"):
            self.check()
        self.assertFalse(self.marker.exists())

    def test_wrong_head_baseline_and_change_identity_are_rejected(self):
        for field, value in (
            ("head", "0" * 40),
            ("base", "0" * 40),
            ("changes", []),
            ("candidate_hashes", {}),
        ):
            with self.subTest(field=field):
                digest = self.write_plan({**self.report, field: value})
                with self.assertRaisesRegex(ValueError, field):
                    self.check(digest=digest)
        self.assertFalse(self.marker.exists())

    def test_changed_bytes_new_unmapped_file_and_rename_invalidate_old_plan(self):
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"# later edit\n")
        with self.assertRaisesRegex(ValueError, "candidate_hashes"):
            self.check()
        self.source.write_bytes(original)
        unknown = self.repo / "new_unmapped.py"
        unknown.write_text("raise RuntimeError('must never import')\n")
        with self.assertRaisesRegex(ValueError, "candidate mismatch"):
            self.check()
        unknown.unlink()
        self.source.rename(self.source.with_name("test_renamed.py"))
        with self.assertRaisesRegex(ValueError, "candidate mismatch"):
            self.check()
        self.assertFalse(self.marker.exists())

    def test_changed_commit_with_same_working_bytes_is_stale(self):
        self.git("commit", "--allow-empty", "-qm", "new candidate identity")
        with self.assertRaisesRegex(ValueError, "head"):
            self.check()
        self.assertFalse(self.marker.exists())

    def test_consumer_changing_commit_cannot_leave_a_passing_candidate(self):
        command = [
            "git",
            "-c",
            "user.name=Quality",
            "-c",
            "user.email=quality@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "changed during execution",
        ]
        source = self.source.read_text(encoding="utf-8").replace(
            "  Path(", f"  subprocess.run({command!r}, check=True)\n  Path("
        )
        self.source.write_text("import subprocess\n" + source, encoding="utf-8")
        digest = self.write_plan({**plan(self.repo, self.base), "status": "planned"})
        result = self.check(digest=digest)
        self.assertEqual(result["commands"][0]["tests"]["status"], "pass")
        self.assertFalse(result["stable_candidate"])
        self.assertEqual(result["status"], "fail")

    def test_missing_group_or_malformed_plan_cannot_execute(self):
        with self.assertRaisesRegex(ValueError, "absent from the bound plan"):
            self.check(group="reading_contracts")
        for updates in (
            {"status": "error"},
            {"groups": None},
            {"coverage_gaps": None},
        ):
            with self.subTest(updates=updates):
                digest = self.write_plan({**self.report, **updates})
                with self.assertRaises(ValueError):
                    self.check(digest=digest)
        self.assertFalse(self.marker.exists())

    def test_unknown_source_gap_and_expanded_groups_survive_plan_reuse(self):
        (self.repo / "unknown.py").write_text("value=1\n", encoding="utf-8")
        report = {**plan(self.repo, self.base), "status": "planned"}
        digest = self.write_plan(report)
        reused, _ = execution_plan(
            self.repo, capture(self.repo, self.base), self.plan_path, digest
        )
        self.assertIn("unknown.py", reused["coverage_gaps"])
        self.assertTrue(reused["expanded_checks"])
        self.assertIn("cognition_contracts", reused["groups"])
        self.assertIn("reading_contracts", reused["groups"])

    def test_cli_missing_artifact_retains_failure_report_without_running_tests(self):
        report_path = self.root / "failure.json"
        executed = self.run_cli(
            "check",
            "--tier",
            "g2",
            "--group",
            "installation_contracts",
            "--plan",
            str(self.root / "missing.json"),
            "--plan-sha256",
            self.digest,
            "--report",
            str(report_path),
        )
        self.assertEqual(executed.returncode, 1, executed.stdout + executed.stderr)
        report = json.loads(report_path.read_bytes())
        self.assertEqual(report["status"], "error")
        self.assertIn("missing.json", report["error"])
        self.assertEqual(report["g3"], "not_executed")
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
