"""Exercise native state consumers without opening a production AstrBot database."""

import os
import tempfile
from pathlib import Path

from .external_checks import execute


def cognition_checks(repo: Path, interpreter: str) -> list[dict]:
    if not interpreter or not Path(interpreter).is_file():
        return [
            {
                "command": ["cognition-tests"],
                "exit_code": 2,
                "stdout": "",
                "stderr": "Missing --bot-python interpreter.",
            }
        ]
    commands = [
        [
            interpreter,
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/cognition_state",
            "-v",
        ],
        [
            interpreter,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "maintenance/tests/test_companion_runtime.py",
            "data/plugins/astrbot_plugin_memory_companion/tests/test_core_memory.py",
            "data/plugins/astrbot_plugin_memory_companion/tests/test_manual_memory_correction.py",
            "data/plugins/astrbot_plugin_memory_companion/tests/test_source_capture.py",
        ],
    ]
    results = []
    for command in commands:
        with tempfile.TemporaryDirectory(prefix="xiaoxia-cognition-contract-") as root:
            results.append(
                execute(
                    repo,
                    command,
                    extra_env={
                        "ASTRBOT_ROOT": root,
                        "ASTRBOT_TEST_MODE": "true",
                        "TESTING": "true",
                        "PYTHONPATH": os.pathsep.join(
                            str(repo / p)
                            for p in (
                                "data/plugins",
                                "data/plugins/astrbot_plugin_memory_companion",
                                "data/plugins/astrbot_plugin_memory_companion/tests",
                            )
                        ),
                    },
                )
            )
    return results
