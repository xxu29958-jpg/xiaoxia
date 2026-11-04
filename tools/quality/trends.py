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
        "net_lines": sum((c["added"] or 0) - (c["deleted"] or 0) for c in changes),
        "scale": scale,
        "base_absolute_totals": {
            k: before["scale"][k]
            for k in ("Repository Total", "First-party Production Total")
        },
        "complexity": {"before": before["complexity"], "after": after["complexity"]},
        "changed_functions": function_changes(old, new, changes),
        "relationships": relation_changes(before, after, changes),
        "scope": "Absolute totals and physical additions/deletions are retained; format/move effects are explicitly labeled",
    }


def function_changes(old: dict, new: dict, changes: list[dict]) -> list[dict]:
    result = []
    for change in changes:
        previous = old.get(change["previous_path"], {}).get("symbols", [])
        remaining = [s for s in previous if s["kind"] == "function"]
        for symbol in new.get(change["path"], {}).get("symbols", []):
            if symbol["kind"] != "function":
                continue
            prior = next(
                (s for s in remaining if s["fingerprint"] == symbol["fingerprint"]),
                None,
            )
            if prior is None:
                prior = next(
                    (s for s in remaining if s["name"] == symbol["name"]), None
                )
            if prior is not None:
                remaining.remove(prior)
            result.append(
                {
                    "path": change["path"],
                    "previous_path": change["previous_path"],
                    "before": prior,
                    "after": symbol,
                    "state": "added"
                    if prior is None
                    else "layout_or_rename_only"
                    if prior["fingerprint"] == symbol["fingerprint"]
                    else "changed",
                    "complexity_delta": symbol.get("complexity", 0)
                    - (prior or {}).get("complexity", 0),
                }
            )
        result.extend(
            {
                "path": change["path"],
                "previous_path": change["previous_path"],
                "before": s,
                "after": None,
                "state": "removed",
                "complexity_delta": -s.get("complexity", 0),
            }
            for s in remaining
        )
    return result


def relation_changes(before: dict, after: dict, changes: list[dict]) -> dict:
    renamed = {
        c["previous_path"]: c["path"] for c in changes if c["status"].startswith("R")
    }

    def facts(report: dict, translate: dict) -> dict:
        result: dict[str, set[tuple]] = {
            k: set()
            for k in (
                "edges",
                "business_edges",
                "protocol_edges",
                "interfaces",
                "authorities",
                "exceptions",
                "cycles",
            )
        }
        for record in report["files"]:
            path = translate.get(record["path"], record["path"])
            result["edges"].update(
                (path, translate.get(p, p)) for p in record["dependencies"]
            )
            result["business_edges"].update(
                (path, translate.get(p, p))
                for p in record.get("business_dependencies", [])
            )
            result["protocol_edges"].update(
                (path, translate.get(p, p))
                for p in record.get("protocol_dependencies", [])
            )
            result["interfaces"].update(
                (path, s["name"], s["kind"], s.get("signature", ""))
                for s in record.get("public_interfaces", [])
            )
            result["authorities"].update(
                (path, s["id"], s["owner"]) for s in record["state_authorities"]
            )
            result["exceptions"].update(
                (s["id"], path, s["fingerprint"], s["review_by"])
                for s in record["exceptions"]
            )
        result["cycles"] = {
            tuple(sorted(translate.get(p, p) for p in cycle))
            for cycle in report.get("architecture", {}).get("cycles", [])
        }
        return result

    previous, current = facts(before, renamed), facts(after, {})
    return {
        key: {
            "added": sorted(current[key] - previous[key]),
            "removed": sorted(previous[key] - current[key]),
            "before_count": len(previous[key]),
            "after_count": len(current[key]),
        }
        for key in previous
    }


def change_history(
    repo: Path, ref: str, paths: list[str], limit: int
) -> tuple[Counter, dict, list]:
    raw = git(
        repo,
        "log",
        f"-{limit}",
        "--format=COMMIT:%H%n%s",
        "--name-status",
        "--find-renames=30%",
        "-z",
        ref,
    )
    tokens = iter(raw.decode().split("\0"))
    names = {path: path for path in paths}
    counts, latest, renames = Counter(), {}, []
    touched = set()
    commit, reason = "", ""
    for token in tokens:
        token = token.lstrip("\n")
        if not token:
            continue
        if token.startswith("COMMIT:"):
            counts.update(touched)
            touched.clear()
            commit, _, reason = token.removeprefix("COMMIT:").partition("\n")
            continue
        previous = next(tokens)
        path = next(tokens) if token.startswith(("R", "C")) else previous
        current = names.get(path)
        if current:
            touched.add(current)
            latest.setdefault(
                current, {"commit": commit, "subject": reason, "historical_path": path}
            )
        if token.startswith("R") and current:
            names.pop(path)
            names[previous] = current
            renames.append(
                {
                    "commit": commit,
                    "previous_path": previous,
                    "path": path,
                    "current_path": current,
                }
            )
        if token == "A":
            names.pop(path, None)
    counts.update(touched)
    return counts, latest, renames


def history(repo: Path, ref: str, records: list[dict], limit: int = 100) -> dict:
    counts, latest, renames = change_history(
        repo, ref, [r["path"] for r in records], limit
    )
    hotspots = []
    for record in records:
        record["recent_change_count"] = counts[record["path"]]
        record["latest_change_evidence"] = latest.get(record["path"])
        if record["first_party_production"]:
            hotspots.append(
                {
                    "path": record["path"],
                    "changes": counts[record["path"]],
                    "maximum_complexity": max(
                        (s.get("complexity", 0) for s in record.get("symbols", [])),
                        default=0,
                    ),
                    "source_consumers": len(record["consumers"]),
                    "business_consumers": len(record.get("business_consumers", [])),
                    "protocol_consumers": len(record.get("protocol_consumers", [])),
                    "consumers": len(
                        set(record["consumers"])
                        | set(record.get("business_consumers", []))
                        | set(record.get("protocol_consumers", []))
                    ),
                    "state_authority": bool(record["state_authorities"]),
                    "lines": record.get("lines", 0),
                }
            )
    hotspots.sort(
        key=lambda r: (
            r["state_authority"]
            and r["maximum_complexity"] > 10
            and r["changes"] > 1
            and r["consumers"] > 1,
            r["maximum_complexity"] * r["changes"] * (1 + r["consumers"]),
        ),
        reverse=True,
    )
    return {
        "history_limit_commits": limit,
        "head": ref,
        "hotspots": hotspots,
        "renames": renames,
        "scope": "Commit touches follow detected renames within the bounded history; copies start separate responsibility. Frequency is not an authorship or quality score",
    }
