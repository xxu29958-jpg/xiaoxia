"""Select contracts through both old and new real import consumers."""

from collections import deque
from pathlib import Path

from .changes import Change, capture
from .external_checks import runtime_group
from .gate import dependency_sources
from .git_snapshot import entries
from .ownership import CONFIG, DOCUMENT
from .source_relations import facts, relationships
from .test_selection import CONTRACTS, required_tests


def consumers(
    sources: dict[str, bytes], paths: list[str], resources: list[str] | None = None
) -> dict[str, list[str]]:
    graph, _ = relationships(sources, resources or [])
    reverse: dict[str, set[str]] = {}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)
    routes = {path: [path] for path in paths}
    queue = deque(routes)
    while queue:
        node = queue.popleft()
        for parent in sorted(reverse.get(node, set())):
            if parent not in routes:
                routes[parent] = [*routes[node], parent]
                queue.append(parent)
    return routes


def mapping_gaps(paths: list[str], routes: dict, direct: dict, new: dict) -> list[str]:
    mapped = {
        p
        for p in routes
        if any(required_tests([Change(p, None, "M")])[k] for k in CONTRACTS)
    }
    gaps = []
    for path in paths:
        reaches_test = path in mapped or any(
            route[0] == path and end in mapped for end, route in routes.items()
        )
        if not reaches_test and Path(path).suffix not in DOCUMENT:
            gaps.append(path)
        if path in new and facts(path, new[path].decode("utf-8-sig"))["dynamic_gaps"]:
            gaps.append(path)
    return sorted(set(gaps + direct["frontend_checks_required"]))


def plan(repo: Path, base: str, *, full: bool = False) -> dict:
    snapshot = capture(repo, base)
    changes = snapshot["changes"]
    old, new = dependency_sources(repo, snapshot["base"], changes)
    paths = [c.path for c in changes]
    previous = [c.previous_path or c.path for c in changes]
    resources = sorted(
        {
            e.path
            for ref in (snapshot["base"], snapshot["head"])
            for e in entries(repo, ref)
            if Path(e.path).suffix in CONFIG | {".sql"}
        }
        | {p for p in paths if Path(p).suffix in CONFIG | {".sql"}}
    )
    routes = consumers(old, previous, resources) | consumers(new, paths, resources)
    selected = required_tests(
        [Change(p, None, "M") for p in sorted(set(paths) | routes.keys())]
    )
    direct = required_tests(changes)
    gaps = mapping_gaps(paths, routes, direct, new)
    expanded = bool(gaps or selected["unmapped_code_changes"])
    groups = [
        key
        for key in CONTRACTS
        if key != "quality_contracts" and (full or expanded or selected[key])
    ]
    runtime_groups = {
        runtime_group(p) for p in paths if p.endswith(".py") and (repo / p).is_file()
    }
    groups.extend(f"runtime_{group}" for group in sorted(runtime_groups - {"quality"}))
    return {
        "base": snapshot["base"],
        "head": snapshot["head"],
        "candidate_hashes": snapshot["content_sha256"],
        "changes": [vars(c) for c in changes],
        "consumer_routes": routes,
        "groups": groups,
        "full_regression": full,
        "coverage_gaps": gaps,
        "expanded_checks": expanded,
        "unmapped_transitive_consumers": selected["unmapped_code_changes"],
        "g1": ["changed-source", "quality-counterexamples"],
        "g3": "not_executed; requires release journey and target environment",
    }


def matrix(report: dict) -> dict:
    return {
        "include": [
            {
                "group": group,
                "python": "3.11"
                if group
                in {"media_pipeline_contracts", "runtime_singing", "runtime_gateway"}
                else "3.12",
            }
            for group in report["groups"]
        ]
    }
