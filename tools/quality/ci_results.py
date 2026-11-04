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
    if total is None or total == 0:
        status = "not_executed"
    elif skipped and status == "pass":
        status = "partial"
    return {"status": status, "executed": total, "counts": counts}


def qualify(commands: list[dict]) -> bool:
    failed = not commands
    for command in commands:
        command["tests"] = test_result(command)
        failed = (
            failed
            or bool(command["exit_code"])
            or command["tests"]["status"] in {"partial", "not_executed", "fail"}
        )
    return not failed


def job_result(jobs: Mapping[str, str], has_groups: str) -> dict:
    ok = all(jobs.get(key) == "success" for key in ("plan", "g1", "candidate"))
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
