"""Run structural and selected consumer gates, preserving partial outcomes."""

import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

from .changes import capture
from .ci_results import qualify
from .content_checks import verify_reference_bundle
from .external_checks import execute, pyright_checks, runtime_group
from .gate import check_dependencies, contract_checks, inspect_changes
from .git_snapshot import git
from .impact import plan
from .quality_exceptions import validate
from .test_selection import CONTRACTS


def g1(repo: Path, snapshot: dict, policy: dict) -> dict:
    changes = inspect_changes(repo, snapshot, policy)
    dependencies = check_dependencies(
        repo, snapshot["base"], snapshot["changes"], policy
    )
    files = [
        c.path
        for c in snapshot["changes"]
        if c.path.endswith(".py") and (repo / c.path).is_file()
    ]
    commands = []
    if not changes["secrets"]:
        ruff = [sys.executable, "-B", "-m", "ruff"]
        if files:
            commands.extend(
                execute(repo, [*ruff, *args, *files])
                for args in (
                    ["check", "--no-cache"],
                    ["format", "--check", "--no-cache"],
                )
            )
            commands.extend(
                pyright_checks(
                    repo,
                    [p for p in files if runtime_group(p) == "quality"],
                    {"quality": sys.executable},
                )
            )
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
    reference = verify_reference_bundle(repo)
    exception_issues = validate(
        policy, json.loads(git(repo, "show", f"{snapshot['base']}:quality/policy.json"))
    )
    blocked = any(f["blocking"] for f in changes["size"]) or any(
        changes[k] for k in ("complexity", "secrets", "assets")
    )
    blocked = blocked or bool(
        dependencies["new_cycles"]
        or dependencies["new_forbidden"]
        or reference
        or exception_issues
    )
    return {
        "findings": changes,
        "dependencies": dependencies,
        "reference_issues": reference,
        "exception_issues": exception_issues,
        "blocked": blocked,
        "commands": commands,
    }


def g2(repo: Path, snapshot: dict, group: str) -> dict:
    if group.startswith("runtime_"):
        runtime = group.removeprefix("runtime_")
        files = [
            c.path
            for c in snapshot["changes"]
            if c.path.endswith(".py")
            and (repo / c.path).is_file()
            and runtime_group(c.path) == runtime
        ]
        commands = pyright_checks(repo, files, {runtime: sys.executable})
    else:
        if group not in CONTRACTS or group == "quality_contracts":
            raise ValueError(f"Unknown integration group: {group}")
        flags = {key: key == group for key in CONTRACTS}
        commands = contract_checks(
            repo, flags, {"bot": sys.executable, "singing": sys.executable}
        )
    return {"group": group, "commands": commands, "blocked": not commands}


def execution_plan(
    repo: Path, snapshot: dict, path: Path | None, expected_sha256: str
) -> tuple[dict, dict]:
    """Reuse only an intact planner artifact bound to this exact working candidate."""
    started = time.perf_counter()
    digest = None
    if path is None:
        if expected_sha256:
            raise ValueError("An impact-plan digest requires its artifact path")
        report = plan(repo, snapshot["base"])
    else:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not expected_sha256 or digest != expected_sha256:
            raise ValueError("Impact-plan artifact digest does not match the planner")
        report = json.loads(data)
        if not isinstance(report, dict) or report.get("status") != "planned":
            raise ValueError("Impact-plan artifact is not a completed plan")
    expected = {
        "head": snapshot["head"],
        "base": snapshot["base"],
        "candidate_hashes": snapshot["content_sha256"],
        "changes": [vars(change) for change in snapshot["changes"]],
    }
    mismatches = [key for key, value in expected.items() if report.get(key) != value]
    if mismatches:
        raise ValueError("Impact-plan candidate mismatch: " + ", ".join(mismatches))
    if any(
        not isinstance(report.get(key), list)
        or not all(isinstance(value, str) for value in report[key])
        for key in ("groups", "coverage_gaps")
    ):
        raise ValueError("Impact-plan groups or coverage gaps are missing or invalid")
    return report, {
        "source": "artifact" if path is not None else "recomputed",
        "sha256": digest,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def check(
    repo: Path,
    base: str,
    tier: str,
    group: str = "",
    *,
    plan_path: Path | None = None,
    plan_sha256: str = "",
) -> dict:
    started = time.monotonic()
    snapshot = capture(repo, base)
    impact, plan_evidence = execution_plan(repo, snapshot, plan_path, plan_sha256)
    if plan_path is not None and tier == "g2" and group not in impact["groups"]:
        raise ValueError(f"Integration group is absent from the bound plan: {group}")
    policy = json.loads((repo / "quality/policy.json").read_text(encoding="utf-8"))
    result = g1(repo, snapshot, policy) if tier == "g1" else g2(repo, snapshot, group)
    stable = capture(repo, base) == snapshot
    commands_passed = qualify(result["commands"])
    failed = result["blocked"] or not stable or not commands_passed
    failed = failed or (tier == "g1" and bool(impact["coverage_gaps"]))
    return {
        **result,
        "status": "fail" if failed else "pass",
        "tier": tier,
        "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
        "runtime": {"python": platform.python_version(), "platform": platform.system()},
        "head": snapshot["head"],
        "base": snapshot["base"],
        "candidate_hashes": snapshot["content_sha256"],
        "stable_candidate": stable,
        "impact_plan": plan_evidence,
        "coverage_gaps": impact["coverage_gaps"],
        "g3": "not_executed",
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
