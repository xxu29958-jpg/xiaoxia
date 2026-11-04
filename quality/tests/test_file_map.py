"""Recompute exclusive totals, real C901 distributions and resource consumers."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.quality import complexity_report, file_map, line_counts, trends
from tools.quality.git_snapshot import Entry
from tools.quality.inventory import collect, file_record
from tools.quality.source_relations import relationships

POLICY = json.loads(
    (Path(__file__).parents[1] / "policy.json").read_text(encoding="utf-8")
)


class LineTests(unittest.TestCase):
    def test_verified_modified_vendor_keeps_production_responsibility(self):
        entry = Entry("vendor/engine/inference.py", "local", "blob", 10)
        modified, _ = file_record(entry, b"x = 1\n", POLICY, {entry.path: "upstream"})
        self.assertTrue(modified["first_party_production"])
        self.assertEqual(modified["category"], "vendor_modified/production")
        unchanged, _ = file_record(entry, b"x = 1\n", POLICY, {entry.path: "local"})
        self.assertFalse(unchanged["first_party_production"])
        self.assertEqual(unchanged["category"], "vendor_unmodified/production")

    def test_strings_and_mixed_comments_have_exclusive_line_counts(self):
        source = (
            '"""module documentation"""\n\nx = "# text"  # comment\n# only comment\n'
        )
        result = line_counts.measure("x.py", source, "production")
        self.assertEqual(
            [
                result[k]
                for k in (
                    "physical_lines",
                    "code_lines",
                    "comment_lines",
                    "blank_lines",
                )
            ],
            [4, 1, 2, 1],
        )
        js = line_counts.measure(
            "x.js",
            'const url = "https://example.invalid"; // note\n/* two\nlines */\n',
            "production",
        )
        self.assertEqual(
            [js[k] for k in ("physical_lines", "code_lines", "comment_lines")],
            [3, 1, 2],
        )

    def test_scripts_sql_generated_and_binary_do_not_leak_into_production(self):
        self.assertEqual(line_counts.role("tools/build.py"), "tool_script_build")
        self.assertEqual(
            line_counts.role("plugins/memory/scripts/benchmark.py"),
            "tool_script_build",
        )
        self.assertEqual(line_counts.role("plugins/memory/scripts/test_cli.py"), "test")
        self.assertEqual(
            line_counts.role("plugins/memory/core/scripts.py"), "production"
        )
        self.assertEqual(
            line_counts.role("plugins/memory/core/tools/query.py"), "production"
        )
        self.assertEqual(line_counts.role("core/sql/state.sql"), "schema_migration")
        self.assertEqual(line_counts.role("core/store_schema.py"), "production")
        self.assertEqual(line_counts.role("package-lock.json"), "generated_manifest")
        self.assertEqual(
            line_counts.decode("两行\n内容".encode("utf-16")),
            ("两行\n内容", "utf-16-bom"),
        )
        self.assertEqual(line_counts.decode(b"\x00\x01")[1], "binary")

    def test_nested_script_keeps_local_upstream_responsibility_and_source_metrics(self):
        path = "data/plugins/astrbot_plugin_memory_companion/scripts/benchmark.py"
        source = b"def run():\n    return 1\n"
        entry = Entry(path, "local", "blob", len(source))
        record, parsed = file_record(entry, source, POLICY, {path: "upstream"})
        self.assertEqual(record["category"], "upstream_modified/tool_script_build")
        self.assertEqual(record["maintenance"], "self_maintained")
        self.assertFalse(record["first_party_production"])
        self.assertFalse(record["size_exempt"])
        self.assertEqual(record["physical_lines"], 2)
        self.assertEqual(record["code_lines"], 2)
        self.assertEqual(parsed, source)

    def test_actual_ruff_measures_below_ten_and_above_fifteen_without_custom_formula(
        self,
    ):
        complex_source = "def choose(value):  # noqa: C901\n" + "".join(
            f"    if value == {n}:\n        return {n}\n" for n in range(16)
        )
        values = complexity_report.measure(
            {"simple.py": "def simple():\n    return 1\n", "complex.py": complex_source}
        )
        self.assertEqual(values, {"simple.py": {1: 1}, "complex.py": {1: 17}})
        distribution = complexity_report.distribution([1, 17])
        self.assertEqual(distribution["above_10"], 1)
        self.assertEqual(distribution["above_15"], 1)
        self.assertEqual(distribution["p95"], 17)

    def test_property_getter_and_setter_do_not_overwrite_each_other(self):
        source = "class State:\n    @property\n    def value(self):\n        return 1\n    @value.setter\n    def value(self, new):\n        if new:\n            self._value = new\n"
        self.assertEqual(
            complexity_report.measure({"state.py": source}), {"state.py": {3: 1, 6: 2}}
        )

    def test_sql_resource_reaches_actual_reading_consumer(self):
        graph, evidence = relationships(
            {
                "store.py": b"from pathlib import Path\nschema = Path('state.sql').read_text()"
            },
            ["sql/state.sql"],
        )
        self.assertEqual(graph["store.py"], {"sql/state.sql"})
        self.assertEqual(evidence["store.py"]["dynamic_gaps"], [])

    def test_same_basename_runtime_state_is_not_a_quality_policy_consumer(self):
        graph, evidence = relationships(
            {
                "plugins/chat/store.py": b"from pathlib import Path\nstate = Path('policy.json').read_text()"
            },
            ["quality/policy.json"],
        )
        self.assertEqual(graph["plugins/chat/store.py"], set())
        self.assertEqual(
            evidence["plugins/chat/store.py"]["dynamic_gaps"][0]["kind"],
            "resource_resolution",
        )


class ExactTreeTests(unittest.TestCase):
    def test_totals_recompute_and_rename_keeps_complexity_and_absolute_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args])

            def commit():
                git("add", ".")
                git(
                    "-c",
                    "user.name=Quality",
                    "-c",
                    "user.email=quality@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                )
                return git("rev-parse", "HEAD").decode().strip()

            git("init", "-q")
            (root / "module.py").write_text(
                "def work():\n    return 1\n", encoding="utf-8"
            )
            (root / "README").write_text("extensionless text\n", encoding="utf-8")
            (root / "image.bin").write_bytes(b"\x00\x01\x02")
            base = commit()
            before = collect(root, base, POLICY, {})
            (root / "module.py").rename(root / "renamed.py")
            head = commit()
            after = collect(root, head, POLICY, {}, previous=before)
            self.assertEqual(after["incremental"]["reused_file_metrics"], 2)
            measured = next(r for r in after["files"] if r["path"] == "renamed.py")
            self.assertEqual(measured["recent_change_count"], 2)
            self.assertEqual(len(after["history"]["renames"]), 1)
            report = trends.delta(root, before, after)
            total = after["scale"]["Repository Total"]
            for key in total:
                self.assertEqual(
                    total[key],
                    sum(
                        bucket[key] for bucket in after["scale"]["categories"].values()
                    ),
                )
            self.assertEqual(total["files"], 3)
            self.assertEqual(total["physical_lines"], 3)
            self.assertEqual(
                total["physical_lines"],
                sum(
                    total[k]
                    for k in (
                        "code_lines",
                        "comment_lines",
                        "blank_lines",
                        "content_lines",
                    )
                ),
            )
            self.assertEqual(
                after["scale"]["First-party Production Total"]["code_lines"], 2
            )
            self.assertEqual(report["files"][0]["kind"], "rename_or_move_only")
            self.assertEqual(
                report["complexity"]["before"], report["complexity"]["after"]
            )
            self.assertEqual(report["added_lines"], 0)
            self.assertEqual(file_map.query(after, "renamed.py")["tree"], after["tree"])


if __name__ == "__main__":
    unittest.main()
