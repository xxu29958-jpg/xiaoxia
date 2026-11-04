"""Report executed tests without turning skips or missing execution into success."""

import re
from collections.abc import Mapping


def test_result(command: dict) -> dict:
    argv = command["command"]
    output = command.get("stdout", "") + "\n" + command.get("stderr", "")
    if "unittest" in argv:
        found = re.search(r"Ran (\d+) tests? in", output)
        total = int(found[1]) if found else None
        counts = {
            key: int(match[1]) if (match := re.search(key + r"=(\d+)", output)) else 0
            for key in (
                "skipped",
                "failures",
                "errors",
                "expected failures",
                "unexpected successes",
            )
        }
    elif "pytest" in argv:
        summary = "\n".join(output.splitlines()[-8:])
        counts = {
            key: int(match[1]) if (match := re.search(r"(\d+) " + key, summary)) else 0
            for key in ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
        }
        total = sum(counts.values()) or None
    else:
        return {
            "status": "not_applicable",
            "reason": "This command is not a test runner",
        }
    skipped = (
        counts.get("skipped", 0)
        + counts.get("xfailed", 0)
        + counts.get("expected failures", 0)
    )
    status = "fail" if command["exit_code"] else "pass"
    if command.get("status") == "timeout":
        status = "incomplete"
    elif total is None or total == 0:
        status = "not_executed"
    elif skipped and status == "pass":
        status = "partial"
    return {
        "status": status,
        "collected": total,
        "executed": None if total is None else total - counts.get("skipped", 0),
        "counts": counts,
    }


def qualify(commands: list[dict]) -> bool:
    failed = not commands
    for command in commands:
        command["tests"] = test_result(command)
        failed = (
            failed
            or bool(command["exit_code"])
            or command["tests"]["status"]
            in {"partial", "incomplete", "not_executed", "fail"}
        )
    return not failed


def job_result(jobs: Mapping[str, str], has_groups: str) -> dict:
    ok = all(
        jobs.get(key) == "success" for key in ("plan", "g1", "metrics", "candidate")
    )
    ok = ok and (
        jobs.get("g2") == "success"
        or (has_groups == "false" and jobs.get("g2") == "skipped")
    )
    return {
        "status": "pass" if ok else "fail",
        "jobs": jobs,
        "g2_applicable": has_groups != "false",
        "g3": "not_executed",
        "production_deployed": False,
    }


def selection_audit(plan: dict, reports: dict[str, dict]) -> dict:
    expected = set(plan.get("groups", []))
    selected = set(plan.get("incremental_groups", []))
    missing = sorted(expected - reports.keys())
    failed = sorted(
        group
        for group in expected & reports.keys()
        if reports[group].get("status") != "pass"
    )
    misses = sorted(set(failed) - selected)
    mismatched = sorted(
        group
        for group in expected & reports.keys()
        if not plan.get("head")
        or reports[group].get("head") != plan["head"]
        or reports[group].get("base") != plan.get("base")
        or not reports[group].get("stable_candidate")
    )
    valid = (
        plan.get("status") == "planned"
        and not missing
        and not failed
        and not mismatched
    )
    return {
        "status": "pass" if valid else "fail",
        "scope": "All registered contract groups when scheduled/full or selection-engine qualification; unregistered journeys are not claimed covered",
        "incremental_groups": sorted(selected),
        "executed_groups": sorted(reports),
        "required_but_not_executed": missing,
        "failed_groups": failed,
        "candidate_mismatch_groups": mismatched,
        "execution": {
            group: {
                "status": report.get("status"),
                "runtime": report.get("runtime"),
                "elapsed_seconds": report.get("elapsed_seconds"),
                "head": report.get("head"),
                "base": report.get("base"),
                "commands": [
                    {
                        k: command.get(k)
                        for k in (
                            "command",
                            "status",
                            "exit_code",
                            "elapsed_seconds",
                            "tests",
                        )
                    }
                    for command in report.get("commands", [])
                ],
            }
            for group, report in sorted(reports.items())
        },
        "failures_outside_incremental_selection": misses,
        "full_comparison": bool(
            plan.get("full_regression") or plan.get("selection_engine_qualification")
        ),
        "map_gaps": plan.get("coverage_gaps", []),
        "unmapped_transitive_consumers": plan.get("unmapped_transitive_consumers", []),
    }
