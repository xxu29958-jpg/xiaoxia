"""Counterexamples for semantic debt, renames and real graph differences."""

import copy
import json
import unittest
from datetime import date
from pathlib import Path

from tools.quality.architecture_report import components
from tools.quality.frontend import analyze
from tools.quality.impact import consumers, mapping_gaps
from tools.quality.metrics import python_metrics
from tools.quality.quality_exceptions import apply, for_record, validate
from tools.quality.size_gate import compare, identities
from tools.quality.source_relations import facts
from tools.quality.trends import function_changes, relation_changes

POLICY = json.loads(
    (Path(__file__).parents[1] / "policy.json").read_text(encoding="utf-8")
)


class ArchitectureTests(unittest.TestCase):
    def test_vue_typescript_and_actual_configured_alias_consumer(self):
        sources = {
            "dashboard/vite.config.ts": b"export default {resolve:{alias:[{find:'@',replacement:new URL('./src', import.meta.url)}]}}",
            "dashboard/src/state.ts": b"export const revision = 1;",
            "dashboard/src/Card.vue": b"<script setup lang='ts'>import {revision} from '@/state';</script><template>{{revision}}</template>",
            "dashboard/src/page.ts": b"import Card from './Card.vue'; export { Card };",
        }
        result = consumers(sources, ["dashboard/src/state.ts"])
        self.assertEqual(
            result["dashboard/src/page.ts"],
            [
                "dashboard/src/state.ts",
                "dashboard/src/Card.vue",
                "dashboard/src/page.ts",
            ],
        )

    def test_computed_frontend_dependency_is_an_explicit_blocking_gap(self):
        evidence = analyze(
            {
                "dashboard/src/loader.ts": b"export const load = (name: string) => import(name);"
            },
            [],
        )
        gaps = mapping_gaps(
            ["dashboard/src/loader.ts"],
            {"dashboard/src/loader.ts": ["dashboard/src/loader.ts"]},
            evidence,
        )
        self.assertEqual(gaps, ["dashboard/src/loader.ts"])

    def test_actual_cycle_members_and_acyclic_fan_in(self):
        self.assertEqual(
            components({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}}),
            [["a", "b", "c"]],
        )
        self.assertEqual(components({"a": {"b", "c"}, "b": {"c"}, "c": set()}), [])

    def test_sql_prose_is_not_a_shared_state_edge(self):
        result = facts(
            "x.py",
            '"""Move from memory into context."""\nsql = "UPDATE beliefs SET value = ?"\n',
        )
        self.assertEqual([t["name"] for t in result["tables"]], ["beliefs"])
        self.assertEqual(len(result["state_write_candidates"]), 1)

    def test_rename_preserves_complexity_and_function_identity(self):
        old = python_metrics("def old(x):\n    if x:\n        return 1\n", "a.py")
        new = python_metrics("def renamed(x):\n    if x:\n        return 1\n", "b.py")
        old["symbols"][0]["complexity"] = new["symbols"][0]["complexity"] = 2
        result = function_changes(
            {"a.py": old}, {"b.py": new}, [{"path": "b.py", "previous_path": "a.py"}]
        )
        self.assertEqual(result[0]["state"], "layout_or_rename_only")
        self.assertEqual(result[0]["complexity_delta"], 0)

    def test_rename_does_not_report_existing_cycle_as_new(self):
        def report(first):
            return {
                "architecture": {"cycles": [[first, "b.py"]]},
                "files": [
                    {
                        "path": first,
                        "dependencies": ["b.py"],
                        "state_authorities": [],
                        "exceptions": [],
                    },
                    {
                        "path": "b.py",
                        "dependencies": [first],
                        "state_authorities": [],
                        "exceptions": [],
                    },
                ],
            }

        delta = relation_changes(
            report("a.py"),
            report("renamed.py"),
            [{"status": "R100", "path": "renamed.py", "previous_path": "a.py"}],
        )
        self.assertEqual(delta["cycles"]["added"], [])
        self.assertEqual(delta["edges"]["removed"], [])


class ExceptionTests(unittest.TestCase):
    def setUp(self):
        self.old = (
            "def old():\n" + "    # context\n" * 103 + "    return 1\n"
        ).encode()
        self.new = b"def renamed():\n    return 1\n"
        self.exception = copy.deepcopy(POLICY["exceptions"][0])
        self.exception.update(
            path="old.py",
            symbol="old",
            fingerprint=identities(self.old.decode())["old"],
            maximum=105,
        )
        self.policy = {**POLICY, "exceptions": [self.exception]}

    def test_compression_and_rename_cannot_erase_review_or_exception(self):
        findings = compare("new.py", "old.py", self.old, self.new, self.policy)
        self.assertEqual(findings[0]["state"], "layout_only")
        self.assertEqual(findings[0]["previous_value"], 105)
        self.assertEqual(findings[0]["value"], 2)
        self.assertEqual(findings[0]["exception"]["id"], self.exception["id"])
        record = {"path": "new.py", **python_metrics(self.new.decode(), "new.py")}
        self.assertEqual(
            for_record(record, self.policy, "2026-11-04")[0]["measured_path"], "new.py"
        )

    def test_expiry_and_semantic_change_require_review(self):
        item = {
            "path": "new.py",
            "previous_path": "old.py",
            "symbol": "renamed",
            "previous_symbol": "old",
            "metric": "function_lines",
            "value": 105,
            "blocking": False,
        }
        digest = self.exception["fingerprint"]
        self.assertFalse(
            apply(item, self.policy, digest, date(2026, 11, 4))["blocking"]
        )
        self.assertTrue(apply(item, self.policy, digest, date(2026, 12, 5))["blocking"])
        self.assertTrue(
            apply(item, self.policy, "different", date(2026, 11, 4))["blocking"]
        )
        self.assertTrue(validate({"exceptions": []}, self.policy))


if __name__ == "__main__":
    unittest.main()
