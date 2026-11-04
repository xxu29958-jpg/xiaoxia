"""Select contracts through both old and new real import consumers."""

from collections import deque
from pathlib import Path

from . import boundaries
from .changes import Change, capture
from .external_checks import runtime_group
from .gate import dependency_sources
from .ownership import CONFIG
from .test_selection import CONTRACTS, required_tests


def consumers(sources: dict[str, bytes], paths: list[str]) -> dict[str, list[str]]:
    graph = boundaries.graph(sources)
    paths_by_module = {boundaries.module_name(path): path for path in sources}
    reverse: dict[str, set[str]] = {}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)
    routes = {boundaries.module_name(path): [path] for path in paths}
    queue = deque(routes)
    while queue:
        node = queue.popleft()
        for parent in sorted(reverse.get(node, set())):
            if parent not in routes:
                routes[parent] = [*routes[node], paths_by_module[parent]]
                queue.append(parent)
    return {
        paths_by_module[node]: route
        for node, route in routes.items()
        if node in paths_by_module
    }


def plan(repo: Path, base: str, *, full: bool = False) -> dict:
    snapshot = capture(repo, base)
    changes = snapshot["changes"]
    old, new = dependency_sources(repo, snapshot["base"], changes)
    paths = [c.path for c in changes]
    previous = [c.previous_path or c.path for c in changes]
    routes = consumers(old, previous) | consumers(new, paths)
    selected = required_tests(
        [Change(p, None, "M") for p in sorted(set(paths) | routes.keys())]
    )
    direct = required_tests(changes)
    # Existing unmapped transitive consumers are coverage gaps, not grounds to
    # silently drop them. Expand to all known contracts and retain the gap.
    unmapped = [
        p
        for p in direct["unmapped_code_changes"]
        if p not in routes
        or not any(len(route) > 1 and route[0] == p for route in routes.values())
    ]
    unknown_config = [
        p
        for p in paths
        if Path(p).suffix in CONFIG | {".sql"}
        and not any(required_tests([Change(p, None, "M")])[k] for k in CONTRACTS)
    ]
    gaps = sorted(set(unmapped + unknown_config + direct["frontend_checks_required"]))
    groups = [
        key
        for key in CONTRACTS
        if key != "quality_contracts" and (full or gaps or selected[key])
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
        "expanded_checks": bool(gaps),
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
