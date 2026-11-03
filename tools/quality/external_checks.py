"""Run pinned static tools in read-only mode and retain real exit codes."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .metrics import python_metrics


def execute(repo: Path, argv: list[str], *, timeout: int = 180) -> dict:
    result = subprocess.run(
        argv,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"},
    )
    return {
        "command": argv,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def python_checks(repo: Path, files: list[str]) -> list[dict]:
    if not files:
        return []
    # Explicit file arguments include tracked plugin/test code despite Git ignores.
    ruff = [sys.executable, "-B", "-m", "ruff"]
    checks = [execute(repo, [*ruff, "--version"])]
    if checks[-1]["exit_code"] or checks[-1]["stdout"].strip() != "ruff 0.15.22":
        checks[-1]["exit_code"] = 2
        checks[-1]["stderr"] += " Expected pinned Ruff 0.15.22."
        return checks
    checks.append(execute(repo, [*ruff, "check", "--no-cache", *files]))
    checks.append(execute(repo, [*ruff, "format", "--check", "--no-cache", *files]))
    pyright = repo / "quality/node_modules/pyright/index.js"
    if not pyright.is_file():
        checks.append(
            {
                "command": ["pyright"],
                "exit_code": 2,
                "stdout": "",
                "stderr": "Run npm ci --prefix quality --ignore-scripts first.",
            }
        )
        return checks
    checks.append(
        execute(
            repo,
            [
                "node",
                str(pyright),
                "--project",
                "pyrightconfig.json",
                "--pythonpath",
                sys.executable,
                "--outputjson",
                *files,
            ],
        )
    )
    return checks


def complexity(repo: Path, path: str, text: str) -> list[dict]:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--no-cache",
            "--select",
            "C901",
            "--config",
            "lint.mccabe.max-complexity=10",
            "--output-format",
            "json",
            "--stdin-filename",
            path,
            "-",
        ],
        cwd=repo,
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Ruff complexity measurement failed: {result.stderr}")
    diagnostics = json.loads(result.stdout)
    if any(item["code"] != "C901" for item in diagnostics):
        raise ValueError(f"Cannot measure invalid Python: {path}")
    return diagnostics


def complexity_values(path: str, text: str, diagnostics: list[dict]) -> dict[str, int]:
    """Bind a diagnostic to the qualified symbol, not just a shared method name."""
    symbols = python_metrics(text, path).get("symbols", [])
    definitions = {symbol["definition_line"]: symbol["name"] for symbol in symbols}
    pattern = re.compile(r"`(.+)` is too complex \((\d+) > \d+\)")
    result = {}
    for finding in diagnostics:
        match = pattern.fullmatch(finding["message"])
        name = definitions.get(finding["location"]["row"])
        if match is None or name is None:
            raise ValueError(
                "Unsupported Ruff complexity output; update the pinned parser"
            )
        result[name] = int(match[2])
    return result
