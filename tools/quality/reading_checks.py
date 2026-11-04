"""Exercise actual notebook, browser and discovery consumers in a temporary root."""

import os
import tempfile
from pathlib import Path

from .external_checks import execute

TESTS = (
    "test_reading_evidence.py",
    "test_reading_tools.py",
    "test_interest_discovery.py",
    "test_initiative_materials.py",
    "test_ada_web_backpack.py",
    "test_bili_reader.py",
    "test_bili_discovery.py",
    "test_browser_cache.py",
    "test_browser_gateway.py",
    "test_browser_url_messages.py",
    "test_sticker_request_routing.py",
)


def reading_checks(repo: Path, interpreter: str) -> list[dict]:
    if not interpreter or not Path(interpreter).is_file():
        return [
            {
                "command": ["reading-tests"],
                "exit_code": 2,
                "stdout": "",
                "stderr": "Missing --bot-python interpreter.",
            }
        ]
    with tempfile.TemporaryDirectory(prefix="xiaoxia-reading-contract-") as root:
        return [
            execute(
                repo,
                [
                    interpreter,
                    "-B",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    *("maintenance/tests/" + name for name in TESTS),
                ],
                extra_env={
                    "ASTRBOT_ROOT": root,
                    "ASTRBOT_TEST_MODE": "true",
                    "TESTING": "true",
                    "PYTHONPATH": os.pathsep.join(
                        [str(repo), str(repo / "data/plugins")]
                    ),
                },
            )
        ]
