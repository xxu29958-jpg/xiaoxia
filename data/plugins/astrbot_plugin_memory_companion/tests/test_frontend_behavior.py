"""Run production browser modules in a DOM, without live user data."""

import subprocess
from pathlib import Path


def test_frontend_user_actions_and_failure_recovery():
    workspace = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        ["node", "--test", "memory-transport.test.mjs", "memory-actions.test.mjs"],
        cwd=workspace / "maintenance" / "ui", capture_output=True,
        text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
