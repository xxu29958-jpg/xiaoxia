"""Independent counterexamples for new/worsened metrics and import contracts."""

import json
import tempfile
import unittest
from pathlib import Path

from tools.quality import boundaries
from tools.quality.changes import Change
from tools.quality.content_checks import secret_findings, verify_reference_bundle
from tools.quality.external_checks import complexity_values
from tools.quality.gate import complexity_changes, required_tests
from tools.quality.size_gate import compare

POLICY = json.loads((Path(__file__).parents[1] / "policy.json").read_text())


def long_function(name="f", lines=105):
    return (
        f"def {name}():\n" + "    # explanation\n" * (lines - 2) + "    return 1\n"
    ).encode()


class SizeRulesTests(unittest.TestCase):
    def test_property_setter_cannot_hide_worsening_getter_complexity(self):
        def source(branches):
            getter = "class State:\n    @property\n    def value(self):\n" + "".join(
                f"        if self.x == {n}:\n            return {n}\n"
                for n in range(branches)
            )
            setter = "    @value.setter\n    def value(self, x):\n" + "".join(
                f"        if x == {n}:\n            self.x = {n}\n" for n in range(20)
            )
            return (getter + setter).encode()

        changes = complexity_changes(
            Path.cwd(), "state.py", source(16), source(18), POLICY
        )
        self.assertEqual(
            [(r["symbol"], r["previous_value"], r["value"]) for r in changes],
            [("State.value#1", 17, 19)],
        )

    def test_new_review_fails_and_warning_does_not(self):
        big = compare("x.py", "x.py", None, long_function(), POLICY)
        small = compare("x.py", "x.py", None, long_function(lines=80), POLICY)
        self.assertTrue(any(f["blocking"] for f in big))
        self.assertFalse(any(f["blocking"] for f in small))

    def test_unchanged_baseline_and_reduction_are_retained(self):
        old = long_function(lines=110)
        same = compare("x.py", "x.py", old, old, POLICY)
        reduced = compare("x.py", "x.py", old, long_function(), POLICY)
        self.assertEqual(same[0]["state"], "unchanged")
        self.assertEqual(reduced[0]["state"], "layout_only")
        self.assertFalse(any(f["blocking"] for f in [*same, *reduced]))

    def test_worsening_a_renamed_file_is_not_a_new_baseline(self):
        findings = compare(
            "new/path.py",
            "old/path.py",
            long_function(),
            long_function(lines=106),
            POLICY,
        )
        self.assertEqual(findings[0]["previous_path"], "old/path.py")
        self.assertEqual(findings[0]["state"], "worsened")
        self.assertTrue(findings[0]["blocking"])

    def test_renamed_function_preserves_its_existing_metric(self):
        findings = compare(
            "new.py", "old.py", long_function("old"), long_function("renamed"), POLICY
        )
        self.assertEqual(findings[0]["previous_symbol"], "old")
        self.assertEqual(findings[0]["state"], "unchanged")

    def test_moving_production_code_into_tests_requires_explicit_review(self):
        findings = compare(
            "tests/moved.py",
            "app/original.py",
            long_function(),
            long_function(),
            POLICY,
        )
        self.assertTrue(
            any(
                f["metric"] == "maintenance_responsibility" and f["blocking"]
                for f in findings
            )
        )

    def test_complexity_does_not_mix_identically_named_methods(self):
        source = "class A:\n    def run(self):\n        pass\nclass B:\n    def run(self):\n        pass\n"
        records = [
            {"message": "`run` is too complex (22 > 10)", "location": {"row": 2}},
            {"message": "`run` is too complex (17 > 10)", "location": {"row": 5}},
        ]
        self.assertEqual(
            complexity_values("x.py", source, records), {"A.run": 22, "B.run": 17}
        )


class DependencyRulesTests(unittest.TestCase):
    def test_worker_cannot_import_channel_even_inside_function(self):
        sources = {
            "services/voice/new.py": b"def f():\n    from astrbot.api import event\n"
        }
        findings = boundaries.violations(sources, POLICY["dependency_contracts"])
        self.assertEqual(findings[0]["rule"], "ARC-01-worker-channel-boundary")

    def test_relative_and_from_package_import_cycles_fail(self):
        before = {"app/a.py": b"value=1\n", "app/b.py": b"from . import a\n"}
        after = {**before, "app/a.py": b"from .b import value\n"}
        found = boundaries.new_cycles(boundaries.graph(before), boundaries.graph(after))
        self.assertIn(["app.a", "app.b", "app.a"], found)

    def test_existing_cycle_is_reported_only_when_new_edge_added(self):
        before = {"a": {"b"}, "b": {"a"}, "c": {"a"}}
        self.assertEqual(boundaries.new_cycles(before, before), [])
        self.assertTrue(boundaries.new_cycles(before, {**before, "a": {"b", "c"}}))

    def test_typing_reference_stays_in_source_graph_but_is_not_runtime_cycle(self):
        before = {"host.py": b"import part\n", "part.py": b"value=1\n"}
        after = {
            **before,
            "part.py": b"from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import host\nelse:\n    import live\n",
            "live.py": b"value=2\n",
        }
        source = boundaries.graph(after)
        runtime = boundaries.graph(after, runtime_only=True)
        self.assertTrue(boundaries.new_cycles(boundaries.graph(before), source))
        self.assertFalse(boundaries.new_cycles(boundaries.graph(before), runtime))
        self.assertIn("host", source["part"])
        self.assertEqual(runtime["part"], {"live"})
        forbidden = [{"id": "test-boundary", "source": "part", "forbidden": ["host"]}]
        self.assertTrue(boundaries.violations(after, forbidden))
        # Moving the same import into an executable branch restores the block.
        after["part.py"] += b"import host\n"
        self.assertTrue(
            boundaries.new_cycles(
                boundaries.graph(before), boundaries.graph(after, runtime_only=True)
            )
        )

    def test_uncertain_or_rebound_typing_guards_keep_runtime_dependencies(self):
        for prefix in (
            "",
            "from somewhere import TYPE_CHECKING\n",
            "from typing import Any as TYPE_CHECKING\n",
            "from typing import TYPE_CHECKING\nTYPE_CHECKING=True\n",
            "from typing import TYPE_CHECKING\nfrom elsewhere import TYPE_CHECKING\n",
            "from typing import TYPE_CHECKING\ndef f(TYPE_CHECKING):\n    pass\n",
            "from typing import TYPE_CHECKING\ntry:\n    pass\nexcept Exception as TYPE_CHECKING:\n    pass\n",
        ):
            with self.subTest(prefix=prefix):
                imports = boundaries.imported_modules(
                    "part.py",
                    prefix + "if TYPE_CHECKING:\n    import host\n",
                    runtime_only=True,
                )
                self.assertIn("host", imports)

    def test_parent_relative_import_resolves_to_project_module(self):
        imports = boundaries.imported_modules(
            "app/sub/a.py", "from ..store import state"
        )
        self.assertIn("app.store", imports)


class SecretChecksTests(unittest.TestCase):
    def test_secret_detection_never_echoes_value(self):
        value = b"gh" + b"p_" + b"a" * 40
        findings = secret_findings("config.py", b"token='" + value + b"'\n")
        self.assertEqual(
            findings, [{"path": "config.py", "rule": "github_token", "line": 1}]
        )
        self.assertNotIn(value.decode(), json.dumps(findings))

    def test_source_cannot_hide_in_immutable_reference_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "docs/reference/v1.3"
            bundle.mkdir(parents=True)
            (bundle / "MANIFEST.sha256.json").write_text('{"files": []}')
            (bundle / "hidden.py").write_text("value = 1\n")
            self.assertEqual(
                verify_reference_bundle(root),
                [{"path": "hidden.py", "rule": "unexpected_reference_file"}],
            )

    def test_frontend_or_product_changes_cannot_silently_skip_consumer_tests(self):
        result = required_tests(
            [
                Change("dashboard/src/broken.ts", None, "A"),
                Change("services/voice/job.py", None, "A"),
            ]
        )
        self.assertEqual(
            result["frontend_checks_required"], ["dashboard/src/broken.ts"]
        )
        self.assertIn("services/voice/job.py", result["unmapped_code_changes"])


if __name__ == "__main__":
    unittest.main()
