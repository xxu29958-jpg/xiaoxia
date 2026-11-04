"""Run pinned static tools in read-only mode and retain real exit codes."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .metrics import python_metrics


def execute(
    repo: Path, argv: list[str], *, timeout: int = 180, extra_env: dict | None = None
) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_COLOR": "1",
                **(extra_env or {}),
            },
        )
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
        status = "pass" if code == 0 else "fail"
    except subprocess.TimeoutExpired as error:
        code, status = 124, "timeout"
        stdout, stderr = error.stdout or "", error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stderr += "\nCommand timed out."
    except OSError as error:
        code, status, stdout, stderr = 127, "not_executed", "", str(error)
    return {
        "command": argv,
        "exit_code": code,
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "timeout_seconds": timeout,
        "explicit_environment": extra_env or {},
    }


def bot_checks(repo: Path, interpreter: str) -> list[dict]:
    if not interpreter or not Path(interpreter).is_file():
        return [
            {
                "command": ["bot-consumer-tests"],
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
            "tests/performance_delivery",
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
            "maintenance/tests/test_ada_local_voice.py",
            "maintenance/tests/test_ada_dialogue.py",
            "maintenance/tests/test_ada_companion.py",
            "tests/unit/test_tool_generator_lifecycle.py",
            "tests/test_tool_loop_agent_runner.py::test_tool_result_includes_all_calltoolresult_content",
        ],
    ]
    results = []
    for command in commands:
        with tempfile.TemporaryDirectory(prefix="xiaoxia-bot-contract-") as root:
            results.append(
                execute(
                    repo,
                    command,
                    extra_env={
                        "ASTRBOT_ROOT": root,
                        "ASTRBOT_TEST_MODE": "true",
                        "TESTING": "true",
                        "PYTHONPATH": str(repo / "data/plugins"),
                    },
                )
            )
    return results


def runtime_group(path: str) -> str:
    if path.startswith(
        (
            "data/plugins/astrbot_plugin_ada_companion/",
            "tests/performance_delivery/",
            "data/plugins/astrbot_plugin_memory_companion/",
            "tests/cognition_state/",
            "tests/registration/",
            "maintenance/tests/",
        )
    ) or path in {
        "astrbot/core/star/register/star_handler.py",
        "maintenance/tests/test_ada_local_voice.py",
        "maintenance/tests/test_companion_runtime.py",
    }:
        return "bot"
    if path == "services/voice/server_voice.py":
        return "gateway"
    if path in {
        "services/voice/cover_pipeline.py",
        "services/voice/cover_source.py",
        "services/voice/server_singing.py",
    } or path.startswith("tests/media_pipeline/"):
        return "singing"
    return "quality"


def pyright_checks(
    repo: Path, files: list[str], runtimes: dict[str, str]
) -> list[dict]:
    checks = []
    for group in sorted({runtime_group(path) for path in files}):
        selected = [path for path in files if runtime_group(path) == group]
        interpreter = runtimes.get(group)
        if not interpreter or not Path(interpreter).is_file():
            checks.append(
                {
                    "command": ["pyright", "--runtime", group, *selected],
                    "exit_code": 2,
                    "stdout": "",
                    "stderr": f"Missing {group} interpreter; supply --{group}-python. "
                    "No fallback to an unrelated package environment.",
                }
            )
            continue
        checks.append(
            execute(
                repo,
                [
                    interpreter,
                    "-B",
                    "-c",
                    "import sys; print(sys.version); "
                    "raise SystemExit(0 if sys.version_info[:2] == "
                    + ("(3, 12)" if group in {"quality", "bot"} else "(3, 11)")
                    + " else 2)",
                ],
            )
        )
        if checks[-1]["exit_code"]:
            continue
        checks.append(
            execute(
                repo,
                [
                    "node",
                    str(repo / "quality/node_modules/pyright/index.js"),
                    "--project",
                    "pyrightconfig.json",
                    "--pythonpath",
                    interpreter,
                    "--outputjson",
                    *selected,
                ],
            )
        )
    return checks


def python_checks(
    repo: Path, files: list[str], runtimes: dict[str, str] | None = None
) -> list[dict]:
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
    interpreters = {"quality": sys.executable, **(runtimes or {})}
    checks.extend(pyright_checks(repo, files, interpreters))
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
            "--ignore-noqa",
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
        # Windows text-mode stdin translates LF to CRLF. Passing existing CRLF
        # unchanged inserts extra blank lines and breaks AST/diagnostic binding.
        input=text.replace("\r\n", "\n").replace("\r", "\n"),
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
    definitions = {symbol["definition_line"]: symbol["key"] for symbol in symbols}
    result = {}
    for line, value in complexity_by_line(diagnostics).items():
        name = definitions.get(line)
        if name is None:
            raise ValueError(
                "Unsupported Ruff complexity output; update the pinned parser"
            )
        result[name] = value
    return result


def complexity_by_line(diagnostics: list[dict]) -> dict[int, int]:
    pattern = re.compile(r"`(.+)` is too complex \((\d+) > \d+\)")
    result = {}
    for finding in diagnostics:
        match = pattern.fullmatch(finding["message"])
        if match is None:
            raise ValueError(
                "Unsupported Ruff complexity output; update the pinned parser"
            )
        result[finding["location"]["row"]] = int(match[2])
    return result
