"""Read-only EQ2 change checks; G2/G3 remain separately required by impact."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import boundaries
from .changes import capture, load_before, source_bytes
from .cognition_checks import cognition_checks
from .content_checks import asset_findings, secret_findings, verify_reference_bundle
from .external_checks import (
    bot_checks,
    complexity,
    complexity_values,
    execute,
    python_checks,
)
from .frontend import EXTENSIONS
from .git_snapshot import BlobReader, entries
from .ownership import TEXT
from .reading_checks import reading_checks
from .size_gate import compare, identities
from .source_relations import relationships
from .test_selection import required_tests


def dependency_sources(
    repo: Path, base: str, changes: list, *, include_frontend: bool = False
) -> tuple[dict, dict]:
    suffixes = {".py", ".pyi"} | (EXTENSIONS if include_frontend else set())
    before = {}
    with BlobReader(repo) as reader:
        for item in entries(repo, base):
            if Path(item.path).suffix in suffixes:
                before[item.path] = reader.read(item.oid)
    after = {
        name: data for name in before if (data := source_bytes(repo, name)) is not None
    }
    for change in changes:
        if Path(change.path).suffix in suffixes:
            data = source_bytes(repo, change.path)
            if data is not None:
                after[change.path] = data
    return before, after


def check_dependencies(repo: Path, base: str, changes: list, policy: dict) -> dict:
    before, after = dependency_sources(repo, base, changes, include_frontend=True)
    old = boundaries.violations(
        {p: b for p, b in before.items() if p.endswith((".py", ".pyi"))},
        policy["dependency_contracts"],
    )
    new = boundaries.violations(
        {p: b for p, b in after.items() if p.endswith((".py", ".pyi"))},
        policy["dependency_contracts"],
    )
    old_graph, _ = relationships(before)
    new_graph, _ = relationships(after)
    renamed = {c.previous_path: c.path for c in changes if c.status.startswith("R")}
    old_graph = {
        renamed.get(p, p): {renamed.get(t, t) for t in targets}
        for p, targets in old_graph.items()
    }
    return {
        "scope": "Resolved Python and TypeScript/Vue imports, including plugin aliases; computed registration remains a map gap",
        "existing_forbidden": old,
        "new_forbidden": [v for v in new if v not in old],
        "new_cycles": boundaries.new_cycles(old_graph, new_graph),
    }


def complexity_changes(
    repo: Path, path: str, before: bytes | None, after: bytes, policy: dict
) -> list[dict]:
    old_text, new_text = (before or b"").decode("utf-8-sig"), after.decode("utf-8-sig")
    old_values = complexity_values(path, old_text, complexity(repo, path, old_text))
    new_values = complexity_values(path, new_text, complexity(repo, path, new_text))
    previous_identities = {
        digest: name for name, digest in identities(old_text).items()
    }
    current_identities = identities(new_text)
    result = []
    for name, value in new_values.items():
        old_name = (
            name
            if name in old_values
            else previous_identities.get(current_identities[name])
        )
        previous = old_values.get(old_name, 0) if old_name is not None else 0
        if (
            value > policy["thresholds"]["mccabe_complexity"]["review_above"]
            and value > previous
        ):
            result.append(
                {
                    "path": path,
                    "symbol": name,
                    "value": value,
                    "previous_value": previous,
                    "rule": "new_or_worsened_complexity",
                }
            )
    return result


def inspect_changes(repo: Path, snapshot: dict, policy: dict) -> dict:
    result = {"size": [], "complexity": [], "secrets": [], "assets": []}
    with BlobReader(repo) as reader:
        for change in snapshot["changes"]:
            before = load_before(repo, snapshot["base"], change, reader)
            after = source_bytes(repo, change.path)
            if after is None:
                continue
            result["secrets"].extend(secret_findings(change.path, after))
            result["assets"].extend(asset_findings(change.path, after, before))
            if change.path.startswith("docs/reference/v1.3/"):
                continue  # Immutable bundle has its own manifest check, not production LOC.
            if Path(change.path).suffix in TEXT:
                result["size"].extend(
                    compare(
                        change.path,
                        change.previous_path or change.path,
                        before,
                        after,
                        policy,
                    )
                )
            if change.path.endswith(".py"):
                result["complexity"].extend(
                    complexity_changes(repo, change.path, before, after, policy)
                )
    return result


def contract_checks(repo: Path, tests: dict, runtimes: dict[str, str]) -> list[dict]:
    checks = []
    if tests["frontend_contracts"]:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        checks.extend(
            execute(repo, [npm, "run", "--prefix", "dashboard", command])
            for command in ("typecheck", "i18n:check")
        )
    for flag, folder, interpreter in (
        ("quality_contracts", "quality/tests", sys.executable),
        ("installation_contracts", "tests/installation", sys.executable),
        ("media_job_contracts", "tests/media_jobs", sys.executable),
        (
            "media_pipeline_contracts",
            "tests/media_pipeline",
            runtimes.get("singing", ""),
        ),
    ):
        if not tests[flag]:
            continue
        if not interpreter or not Path(interpreter).is_file():
            checks.append(
                {
                    "command": ["unittest", folder],
                    "exit_code": 2,
                    "stdout": "",
                    "stderr": "Missing required test interpreter.",
                }
            )
            continue
        checks.append(
            execute(
                repo,
                [interpreter, "-B", "-m", "unittest", "discover", "-s", folder, "-v"],
            )
        )
    if tests["performance_contracts"]:
        checks.extend(bot_checks(repo, runtimes.get("bot", "")))
    if tests["cognition_contracts"]:
        checks.extend(cognition_checks(repo, runtimes.get("bot", "")))
    if tests["reading_contracts"]:
        checks.extend(reading_checks(repo, runtimes.get("bot", "")))
    return checks


def run(repo: Path, base: str, runtimes: dict[str, str] | None = None) -> dict:
    started = time.monotonic()
    policy = json.loads((repo / "quality/policy.json").read_text(encoding="utf-8"))
    snapshot = capture(repo, base)
    checks = inspect_changes(repo, snapshot, policy)
    dependencies = check_dependencies(
        repo, snapshot["base"], snapshot["changes"], policy
    )
    files = [
        c.path
        for c in snapshot["changes"]
        if c.path.endswith(".py") and (repo / c.path).is_file()
    ]
    runtimes = runtimes or {}
    commands = [] if checks["secrets"] else python_checks(repo, files, runtimes)
    tests = required_tests(snapshot["changes"])
    if not checks["secrets"]:
        commands.extend(contract_checks(repo, tests, runtimes))
    reference = verify_reference_bundle(repo)
    blocked = any(f["blocking"] for f in checks["size"]) or any(
        checks[k] for k in ("complexity", "secrets", "assets")
    )
    blocked = blocked or bool(
        dependencies["new_forbidden"] or dependencies["new_cycles"] or reference
    )
    blocked = (
        blocked
        or any(c["exit_code"] for c in commands)
        or bool(tests["unmapped_code_changes"] or tests["frontend_checks_required"])
    )
    if capture(repo, base)["content_sha256"] != snapshot["content_sha256"]:
        raise RuntimeError(
            "Source changed during checks; results do not describe a stable candidate"
        )
    return {
        "base": snapshot["base"],
        "head": snapshot["head"],
        "candidate_hashes": snapshot["content_sha256"],
        "checks": checks,
        "dependencies": dependencies,
        "reference_issues": reference,
        "tests": tests,
        "commands": commands,
        "status": "fail" if blocked else "pass",
        "scope": "EQ2 static change gate; G2/G3 and unmapped consumer tests must be handled separately",
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", required=True, help="Immutable predecessor commit or branch base"
    )
    parser.add_argument("--bot-python", type=Path)
    parser.add_argument("--gateway-python", type=Path)
    parser.add_argument("--singing-python", type=Path)
    args = parser.parse_args()
    try:
        runtimes = {
            name: str(value.resolve())
            for name in ("bot", "gateway", "singing")
            if (value := getattr(args, f"{name}_python")) is not None
        }
        report = run(Path.cwd(), args.base, runtimes)
    except (
        OSError,
        ValueError,
        RuntimeError,
        SyntaxError,
        subprocess.SubprocessError,
    ) as error:
        print(json.dumps({"status": "error", "detail": str(error)}, ensure_ascii=False))
        raise SystemExit(2) from error
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
