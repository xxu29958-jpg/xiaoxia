"""Select contracts through both old and new real import consumers."""

from collections import deque
from pathlib import Path

from . import http_protocol
from .changes import Change, capture
from .external_checks import runtime_group
from .gate import dependency_sources
from .git_snapshot import entries
from .ownership import CONFIG, DOCUMENT
from .shared_state import extend_impact, uncertainties
from .source_relations import relationships
from .test_selection import CONTRACTS, required_tests


def consumers(
    sources: dict[str, bytes],
    paths: list[str],
    resources: list[str] | None = None,
    evidence: dict | None = None,
    origins: dict | None = None,
) -> dict[str, list[str]]:
    graph, records = relationships(sources, resources or [])
    extend_impact(sources, graph, records)
    http_protocol.extend_impact(graph, records)
    if evidence is not None:
        evidence.update(records)
    reverse: dict[str, set[str]] = {}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)
    routes: dict[str, list[str]] = {}
    roots: dict[str, set[str]] = {}
    queue = deque([path] for path in paths)
    while queue:
        route = queue.popleft()
        root, node = route[0], route[-1]
        if root in roots.setdefault(node, set()):
            continue
        roots[node].add(root)
        routes.setdefault(node, route)
        for parent in sorted(reverse.get(node, set())):
            queue.append([*route, parent])
    if origins is not None:
        origins.update({node: sorted(values) for node, values in roots.items()})
    return routes


def mapping_gaps(
    paths: list[str], routes: dict, evidence: dict, origins: dict | None = None
) -> list[str]:
    mapped = {
        p
        for p in routes
        if any(required_tests([Change(p, None, "M")])[k] for k in CONTRACTS)
    }
    roots = origins if origins is not None else {p: [r[0]] for p, r in routes.items()}
    covered = {root for node in mapped for root in roots.get(node, [])}
    gaps = []
    for path in paths:
        reaches_test = path in mapped or path in covered
        if not reaches_test and Path(path).suffix not in DOCUMENT:
            gaps.append(path)
        record = evidence.get(path, {})
        uncertain_import = any(
            g["kind"]
            in {
                "computed_import",
                "computed_frontend_import",
                "unresolved_frontend_import",
                "shared_state_binding",
                "http_target",
                "http_binding",
                "http_listener",
            }
            for g in record.get("dynamic_gaps", [])
        )
        if record.get("parse_errors") or uncertain_import:
            gaps.append(path)
    return sorted(set(gaps))


def changed_consumers(repo: Path, snapshot: dict) -> tuple[dict, dict, dict]:
    changes = snapshot["changes"]
    old, new = dependency_sources(
        repo, snapshot["base"], changes, include_frontend=True
    )
    paths = [c.path for c in changes]
    previous = [c.previous_path or c.path for c in changes]
    resources = sorted(
        {
            e.path
            for ref in (snapshot["base"], snapshot["head"])
            for e in entries(repo, ref)
        }
        | {p for p in paths if Path(p).suffix in CONFIG | {".sql"}}
    )
    evidence = {}
    old_origins, new_origins = {}, {}
    routes = consumers(old, previous, resources, origins=old_origins) | consumers(
        new, paths, resources, evidence, new_origins
    )
    aliases = dict(zip(previous, paths, strict=True))
    origins = {
        node: sorted(
            {aliases.get(p, p) for p in old_origins.get(node, [])}
            | set(new_origins.get(node, []))
        )
        for node in routes
    }
    return routes, evidence, origins


def plan(repo: Path, base: str, *, full: bool = False) -> dict:
    snapshot = capture(repo, base)
    changes = snapshot["changes"]
    paths = [c.path for c in changes]
    routes, evidence, origins = changed_consumers(repo, snapshot)
    selected = required_tests(
        [Change(p, None, "M") for p in sorted(set(paths) | routes.keys())]
    )
    gaps = mapping_gaps(paths, routes, evidence, origins)
    unknown = http_protocol.uncertainties(routes, evidence) | uncertainties(
        routes, evidence
    )
    expanded = bool(gaps or unknown or selected["unmapped_code_changes"])
    selection_changed = any(
        p
        in {
            "tools/quality/impact.py",
            "tools/quality/source_relations.py",
            "tools/quality/shared_state.py",
            "tools/quality/http_protocol.py",
            "tools/quality/frontend.py",
            "tools/quality/frontend_ast.cjs",
            "tools/quality/test_selection.py",
            "tools/quality/ci.py",
            "tools/quality/ci_checks.py",
            "tools/quality/external_checks.py",
            "tools/quality/ci_results.py",
            ".github/workflows/quality.yml",
        }
        for p in paths
    )
    groups = [
        key
        for key in CONTRACTS
        if key != "quality_contracts"
        and (full or selection_changed or expanded or selected[key])
    ]
    runtime_groups = {
        runtime_group(p) for p in paths if p.endswith(".py") and (repo / p).is_file()
    }
    groups.extend(f"runtime_{group}" for group in sorted(runtime_groups - {"quality"}))
    incremental = [
        key for key in CONTRACTS if key != "quality_contracts" and selected[key]
    ]
    incremental.extend(
        f"runtime_{group}" for group in sorted(runtime_groups - {"quality"})
    )
    return {
        "base": snapshot["base"],
        "head": snapshot["head"],
        "candidate_hashes": snapshot["content_sha256"],
        "changes": [vars(c) for c in changes],
        "consumer_routes": routes,
        "consumer_origins": origins,
        "groups": groups,
        "incremental_groups": incremental,
        "full_regression": full,
        "selection_engine_qualification": selection_changed,
        "coverage_gaps": gaps,
        "relationship_uncertainties": unknown,
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
                "python": "3.11.15"
                if group
                in {"media_pipeline_contracts", "runtime_singing", "runtime_gateway"}
                else "3.12.13",
            }
            for group in report["groups"]
        ]
    }
