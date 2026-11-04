"""Exact-tree deltas and bounded Git history; movement never erases old totals."""

import ast
from collections import Counter
from pathlib import Path

from .git_snapshot import git


def numeric_delta(before: dict, after: dict) -> dict:
    return {
        key: after.get(key, 0) - before.get(key, 0)
        for key in sorted(before.keys() | after.keys())
    }


def changed_files(repo: Path, base: str, head: str) -> list[dict]:
    fields = iter(
        git(repo, "diff", "--name-status", "--find-renames=30%", "-z", base, head)
        .decode()
        .split("\0")
    )
    result = []
    for status in fields:
        if not status:
            continue
        original = next(fields)
        path = next(fields) if status.startswith(("R", "C")) else original
        result.append(
            {
                "path": path,
                "previous_path": None if status == "A" else original,
                "status": status,
            }
        )
    return result


def line_delta(repo: Path, base: str, head: str) -> dict:
    fields = iter(
        git(repo, "diff", "--numstat", "--find-renames=30%", "-z", base, head)
        .decode()
        .split("\0")
    )
    result = {}
    for field in fields:
        if not field:
            continue
        added, removed, path = field.split("\t", 2)
        if not path:
            next(fields)
            path = next(fields)
        result[path] = {
            "added": None if added == "-" else int(added),
            "deleted": None if removed == "-" else int(removed),
        }
    return result


def change_kind(repo: Path, base: str, head: str, change: dict, record: dict) -> str:
    if change["status"].startswith("R100"):
        return "rename_or_move_only"
    if record.get("role") == "generated_manifest":
        return "generated_manifest"
    path, previous = change["path"], change["previous_path"]
    if previous and path.endswith(".py") and change["status"] != "D":
        before = git(repo, "show", f"{base}:{previous}").decode("utf-8-sig")
        after = git(repo, "show", f"{head}:{path}").decode("utf-8-sig")
        try:
            if ast.dump(ast.parse(before)) == ast.dump(ast.parse(after)):
                return "format_or_comment_only"
        except SyntaxError:
            return "unparseable"
    return "source_or_content"


def delta(repo: Path, before: dict, after: dict) -> dict:
    old = {record["path"]: record for record in before["files"]}
    new = {record["path"]: record for record in after["files"]}
    base, head = before["commit"], after["commit"]
    changes = changed_files(repo, base, head)
    numbers = line_delta(repo, base, head)
    for change in changes:
        record = new.get(change["path"], old.get(change["previous_path"], {}))
        change.update(numbers[change["path"]])
        change["kind"] = change_kind(repo, base, head, change, record)
        change["category"] = record["category"]
    axes = ("categories", "roles", "languages", "production_domains")
    scale = {
        axis: {
            name: numeric_delta(
                before["scale"][axis].get(name, {}), after["scale"][axis].get(name, {})
            )
            for name in sorted(
                before["scale"][axis].keys() | after["scale"][axis].keys()
            )
        }
        for axis in axes
    }
    return {
        "base_commit": base,
        "base_tree": before["tree"],
        "head_commit": head,
        "head_tree": after["tree"],
        "files": changes,
        "file_status_counts": dict(Counter(c["status"][0] for c in changes)),
        "added_lines": sum(c["added"] or 0 for c in changes),
        "deleted_lines": sum(c["deleted"] or 0 for c in changes),
        "scale": scale,
        "base_absolute_totals": {
            k: before["scale"][k]
            for k in ("Repository Total", "First-party Production Total")
        },
        "complexity": {"before": before["complexity"], "after": after["complexity"]},
        "scope": "Absolute totals and physical additions/deletions are retained; format/move effects are explicitly labeled",
    }


def history(repo: Path, ref: str, records: list[dict], limit: int = 100) -> dict:
    raw = git(repo, "log", f"-{limit}", "--format=", "--name-only", "-z", ref)
    counts = Counter(
        name.strip("\n") for name in raw.decode().split("\0") if name.strip("\n")
    )
    hotspots = []
    for record in records:
        record["recent_change_count"] = counts[record["path"]]
        if record["first_party_production"]:
            hotspots.append(
                {
                    "path": record["path"],
                    "changes": counts[record["path"]],
                    "maximum_complexity": max(
                        (s.get("complexity", 0) for s in record.get("symbols", [])),
                        default=0,
                    ),
                    "consumers": len(record["consumers"]),
                    "state_authority": bool(record["state_authorities"]),
                    "lines": record.get("lines", 0),
                }
            )
    hotspots.sort(
        key=lambda r: (
            r["state_authority"],
            r["maximum_complexity"] * r["changes"] * (1 + r["consumers"]),
        ),
        reverse=True,
    )
    return {
        "history_limit_commits": limit,
        "head": ref,
        "hotspots": hotspots,
        "scope": "Commit touches per path, not an authorship or quality score; renamed history is reported separately in the delta",
    }
