"""A service must be checked against its own interpreter; missing environments fail."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.quality.changes import Change
from tools.quality.external_checks import (
    complexity,
    complexity_values,
    pyright_checks,
    runtime_group,
)
from tools.quality.gate import required_tests


class RuntimeCheckTests(unittest.TestCase):
    def test_crlf_complexity_locations_bind_to_the_same_ast_function(self):
        source = "def many(value):\n" + "".join(
            f"    if value == {index}:\n        return {index}\n" for index in range(12)
        )
        crlf = source.replace("\n", "\r\n")
        diagnostics = complexity(Path(__file__).resolve().parents[2], "sample.py", crlf)
        self.assertEqual(
            complexity_values("sample.py", crlf, diagnostics), {"many": 13}
        )

    def test_worker_interpreter_missing_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            result = pyright_checks(
                Path(directory),
                ["services/voice/server_voice.py"],
                {"quality": sys.executable},
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["exit_code"], 2)
        self.assertIn("--gateway-python", result[0]["stderr"])

    def test_groups_choose_explicit_interpreter_and_wrong_version_prevents_pyright(
        self,
    ):
        files = [
            "services/voice/server_voice.py",
            "services/voice/cover_pipeline.py",
            "services/jobs/store.py",
            "tests/media_pipeline/test_cover_audio.py",
        ]
        self.assertEqual(
            [runtime_group(path) for path in files],
            ["gateway", "singing", "quality", "singing"],
        )
        with patch("tools.quality.external_checks.execute") as execute:
            execute.return_value = {
                "exit_code": 2,
                "stdout": "wrong version",
                "stderr": "",
            }
            result = pyright_checks(
                Path.cwd(),
                files,
                dict.fromkeys(("gateway", "singing", "quality"), sys.executable),
            )
        self.assertEqual(len(result), 3)
        self.assertTrue(
            all(call.args[1][0] == sys.executable for call in execute.call_args_list)
        )

    def test_media_changes_require_transport_and_signal_contracts(self):
        result = required_tests(
            [
                Change("services/voice/cover_pipeline.py", None, "M"),
                Change("services/voice/unmapped.py", None, "A"),
            ]
        )
        self.assertTrue(result["media_job_contracts"])
        self.assertTrue(result["media_pipeline_contracts"])
        self.assertEqual(
            result["unmapped_code_changes"], ["services/voice/unmapped.py"]
        )

    def test_chat_consumer_has_bot_checks_and_unknown_plugin_changes_still_fail(self):
        result = required_tests(
            [
                Change(
                    "data/plugins/astrbot_plugin_ada_companion/performances.py",
                    None,
                    "A",
                ),
                Change(
                    "data/plugins/astrbot_plugin_ada_companion/unmapped.py", None, "A"
                ),
            ]
        )
        self.assertTrue(result["performance_contracts"])
        self.assertEqual(
            result["unmapped_code_changes"],
            ["data/plugins/astrbot_plugin_ada_companion/unmapped.py"],
        )
        self.assertEqual(
            runtime_group("tests/performance_delivery/test_journey.py"), "bot"
        )

    def test_core_schema_and_shared_tool_require_isolated_cognition_consumers(self):
        paths = [
            "data/plugins/astrbot_plugin_memory_companion/core/sql/memory_schema.sql",
            "data/plugins/astrbot_plugin_ada_companion/shared_core.py",
            "data/plugins/astrbot_plugin_memory_companion/core/core_state_store.py",
        ]
        result = required_tests([Change(path, None, "M") for path in paths])
        self.assertTrue(result["cognition_contracts"])
        self.assertEqual(result["unmapped_code_changes"], [])
        self.assertEqual(runtime_group(paths[2]), "bot")


if __name__ == "__main__":
    unittest.main()
