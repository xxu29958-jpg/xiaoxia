"""Read-only EQ2 change checks; G2/G3 remain separately required by impact."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import boundaries
from .changes import capture, load_before, source_bytes
from .content_checks import asset_findings, secret_findings, verify_reference_bundle
from .external_checks import complexity, complexity_values, execute, python_checks
from .git_snapshot import BlobReader, entries
from .ownership import SOURCE, TEXT
from .size_gate import compare, identities


def dependency_sources(repo: Path, base: str, changes: list) -> tuple[dict, dict]:
    before = {}
    with BlobReader(repo) as reader:
        for item in entries(repo, base):
            if item.path.endswith(".py"):
                before[item.path] = reader.read(item.oid)
    after = {
        name: data for name in before if (data := source_bytes(repo, name)) is not None
    }
    for change in changes:
        if change.path.endswith(".py"):
            data = source_bytes(repo, change.path)
            if data is not None:
                after[change.path] = data
    return before, after


def check_dependencies(repo: Path, base: str, changes: list, policy: dict) -> dict:
    before, after = dependency_sources(repo, base, changes)
    old = boundaries.violations(before, policy["dependency_contracts"])
    new = boundaries.violations(after, policy["dependency_contracts"])
    return {
        "scope": "statically resolvable Python imports, including plugins/tests/vendor; dynamic registration requires runtime validation",
        "existing_forbidden": old,
        "new_forbidden": [v for v in new if v not in old],
        "new_cycles": boundaries.new_cycles(
            boundaries.graph(before), boundaries.graph(after)
        ),
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


def required_tests(changes: list) -> dict:
    quality_changed = any(
        c.path.startswith(("tools/quality/", "quality/")) for c in changes
    )
    installation_paths = {
        "setup.ps1",
        "services/runtime.py",
        "tools/download_models.py",
        "vendor/sources.json",
    }
    installation = [
        c.path
        for c in changes
        if c.path in installation_paths
        or c.path.startswith(
            (
                "tools/installation/",
                "tests/installation/",
                "requirements/fish",
                "models-manifests/",
            )
        )
    ]
    other_code = [
        c.path
        for c in changes
        if Path(c.path).suffix in SOURCE
        and not c.path.startswith(("tools/quality/", "quality/"))
        and c.path not in installation
    ]
    frontend = [
        c.path
        for c in changes
        if c.path.startswith("dashboard/")
        and Path(c.path).suffix not in {".md", ".png", ".svg"}
    ]
    return {
        "quality_contracts": quality_changed,
        "installation_contracts": bool(installation),
        "unmapped_code_changes": other_code,
        "frontend_checks_required": frontend,
    }


def run(repo: Path, base: str) -> dict:
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
    commands = [] if checks["secrets"] else python_checks(repo, files)
    tests = required_tests(snapshot["changes"])
    if tests["quality_contracts"] and not checks["secrets"]:
        commands.append(
            execute(
                repo,
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "quality/tests",
                    "-v",
                ],
            )
        )
    if tests["installation_contracts"] and not checks["secrets"]:
        commands.append(
            execute(
                repo,
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests/installation",
                    "-v",
                ],
            )
        )
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
    args = parser.parse_args()
    try:
        report = run(Path.cwd(), args.base)
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
