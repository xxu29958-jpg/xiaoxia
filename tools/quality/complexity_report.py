"""Batch Ruff's actual C901 measurements; no substitute McCabe implementation."""

import importlib.metadata
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from .external_checks import complexity_values


def measure(sources: dict[str, str]) -> dict[str, dict[str, int]]:
    if importlib.metadata.version("ruff") != "0.15.22":
        raise RuntimeError("Complexity reports require Ruff 0.15.22")
    with tempfile.TemporaryDirectory(prefix="xiaoxia-c901-") as directory:
        root = Path(directory)
        paths = {}
        for index, (path, source) in enumerate(sorted(sources.items())):
            exported = root / f"source_{index}.py"
            exported.write_text(source, encoding="utf-8", newline="\n")
            paths[exported.name] = path
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
                "lint.mccabe.max-complexity=0",
                "--output-format",
                "json",
                str(root),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError(f"Ruff measurement failed: {result.stderr}")
        diagnostics: dict[str, list[dict]] = {path: [] for path in sources}
        for item in json.loads(result.stdout):
            if item["code"] != "C901":
                raise ValueError(
                    f"Ruff could not parse {paths[Path(item['filename']).name]}"
                )
            diagnostics[paths[Path(item["filename"]).name]].append(item)
        return {
            path: complexity_values(path, sources[path], items)
            for path, items in diagnostics.items()
        }


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(values),
        "maximum": max(values, default=0),
        **{
            f"p{percent}": ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]
            if ordered
            else 0
            for percent in (50, 90, 95)
        },
        "above_10": sum(value > 10 for value in values),
        "above_15": sum(value > 15 for value in values),
    }


def summarize(records: list[dict]) -> dict:
    functions = [
        symbol
        for record in records
        for symbol in record.get("symbols", [])
        if symbol["kind"] == "function"
    ]
    classes = [
        symbol
        for record in records
        for symbol in record.get("symbols", [])
        if symbol["kind"] == "class"
    ]
    return {
        "c901": distribution([s["complexity"] for s in functions if "complexity" in s]),
        "function_length": distribution([s["lines"] for s in functions]),
        "class_length": distribution([s["lines"] for s in classes]),
        "file_length": distribution([r.get("lines", 0) for r in records]),
        "control_nesting": distribution([s["control_nesting"] for s in functions]),
        "function_count_per_file": distribution(
            [
                sum(s["kind"] == "function" for s in r.get("symbols", []))
                for r in records
            ]
        ),
        "missing_function_complexity": [
            s["name"] for s in functions if "complexity" not in s
        ],
    }
