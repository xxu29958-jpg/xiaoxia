"""Native registries and managers, with an isolated runtime and no remote services."""

import os
import tempfile
from pathlib import Path

from .external_checks import execute


def registration_checks(repo: Path, interpreter: str) -> list[dict]:
    if not interpreter or not Path(interpreter).is_file():
        return [
            {
                "command": ["registration-tests"],
                "exit_code": 2,
                "stdout": "",
                "stderr": "Missing bot interpreter.",
            }
        ]
    with tempfile.TemporaryDirectory(prefix="xiaoxia-registration-contract-") as root:
        return [
            execute(
                repo,
                [
                    interpreter,
                    "-B",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests/registration",
                    "-v",
                ],
                extra_env={
                    "ASTRBOT_ROOT": root,
                    "ASTRBOT_TEST_MODE": "true",
                    "TESTING": "true",
                    "PYTHONPATH": os.pathsep.join(
                        [str(repo), str(repo / "data/plugins")]
                    ),
                },
            ),
            execute(
                repo / "data/plugins/astrbot_plugin_codex_oauth_plug",
                [
                    interpreter,
                    "-B",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_astrbot_registration.py",
                    "-v",
                ],
                extra_env={
                    "ASTRBOT_ROOT": root,
                    "ASTRBOT_TEST_MODE": "true",
                    "TESTING": "true",
                    "PYTHONPATH": os.pathsep.join(
                        [str(repo), str(repo / "data/plugins")]
                    ),
                },
            ),
        ]
