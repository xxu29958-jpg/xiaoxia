"""Evidence-backed coupling candidates, never inferred business-rule verdicts."""

from collections import defaultdict, deque
from pathlib import PurePosixPath


def components(edges: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Kosaraju: large upstream graphs cannot exhaust Python's stack."""
    visited, order = set(), []
    for origin in sorted(edges):
        stack = [(origin, False)]
        while stack:
            node, returning = stack.pop()
            if returning:
                order.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((target, False) for target in sorted(edges.get(node, ())))
    reverse: dict[str, set[str]] = defaultdict(set)
    for source, targets in edges.items():
        for target in targets:
            reverse[target].add(source)
    visited.clear()
    groups = []
    for origin in reversed(order):
        if origin in visited:
            continue
        group, stack = [], [(origin, False)]
        while stack:
            node, _ = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            group.append(node)
            stack.extend((target, False) for target in sorted(reverse[node]))
        if len(group) > 1:
            groups.append(sorted(group))
    return sorted(groups)


def scenario(records: dict[str, dict], item: dict) -> dict:
    roots = item["seeds"]
    routes = {path: [path] for path in roots if path in records}
    queue = deque(routes)
    while queue:
        path = queue.popleft()
        for consumer in records[path]["consumers"]:
            if consumer not in routes:
                routes[consumer] = [*routes[path], consumer]
                queue.append(consumer)
    production = [p for p in routes if records[p]["first_party_production"]]
    return {
        **item,
        "consumer_routes": routes,
        "missing_seeds": sorted(set(roots) - records.keys()),
        "production_consumers": production,
        "production_responsibilities": sorted(
            {records[p]["domain"] for p in production}
        ),
        "test_groups": sorted({g for p in routes for g in records[p]["test_groups"]}),
        "scope": "Observed dependency reach; review candidates, not a claim that every consumer requires editing",
    }


def duplication(records: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for record in records:
        if not record["first_party_production"]:
            continue
        for symbol in record.get("symbols", []):
            if symbol["kind"] == "function" and symbol["lines"] >= 15:
                groups[symbol["fingerprint"]].append(
                    {
                        "path": record["path"],
                        "symbol": symbol["name"],
                        "line": symbol["start"],
                    }
                )
    return [
        {"fingerprint": digest, "definitions": members, "status": "review_candidate"}
        for digest, members in sorted(groups.items())
        if len({m["path"] for m in members}) > 1
    ]


def state_writers(records: list[dict]) -> list[dict]:
    writers = defaultdict(list)
    for record in records:
        if record["role"] != "production":
            continue
        # Database identity is not inferred from a table name. Keep domains apart
        # and show the exact statement sites for review of storage scope.
        for table in record.get("state_write_candidates", []):
            writers[(record["domain"], table["name"])].append(
                {"path": record["path"], **table}
            )
    return [
        {
            "domain": domain,
            "table": table,
            "writers": sites,
            "status": "candidate; database/transaction identity requires review",
        }
        for (domain, table), sites in sorted(writers.items())
        if len({site["path"] for site in sites}) > 1
    ]


def summarize(records: list[dict], policy: dict) -> dict:
    by_path = {r["path"]: r for r in records}
    edges = {r["path"]: set(r["dependencies"]) for r in records}
    crossing = [
        {
            "source": r["path"],
            "target": target,
            "source_domain": r["domain"],
            "target_domain": by_path[target]["domain"],
            "internal_candidate": any(
                p.startswith("_") and p != "__init__.py"
                for p in PurePosixPath(target).parts
            ),
        }
        for r in records
        for target in r["dependencies"]
        if target in by_path and by_path[target]["domain"] != r["domain"]
    ]
    registrations = [
        {
            "path": r["path"],
            **site,
            "static_consumer_count": len(r["consumers"]),
            "validation": "source registration found; runtime activation not observed",
        }
        for r in records
        for site in r.get("registrations", [])
    ]
    return {
        "cycles": components(edges),
        "cross_domain_edges": crossing,
        "fan_in": sorted(
            ({"path": r["path"], "count": len(r["consumers"])} for r in records),
            key=lambda item: (-item["count"], item["path"]),
        ),
        "fan_out": sorted(
            ({"path": r["path"], "count": len(r["dependencies"])} for r in records),
            key=lambda item: (-item["count"], item["path"]),
        ),
        "duplicate_rule_candidates": duplication(records),
        "multiple_writer_candidates": state_writers(records),
        "registrations": registrations,
        "registration_without_static_consumer": [
            r for r in registrations if not r["static_consumer_count"]
        ],
        "representative_changes": [
            scenario(by_path, item) for item in policy.get("representative_changes", [])
        ],
        "scope": "Cycles and imports are source evidence; duplication, internal access and state writers are investigation candidates, not proven semantic violations",
    }
