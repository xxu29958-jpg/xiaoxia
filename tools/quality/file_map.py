"""Join automatic facts with the small set of non-inferable domain semantics."""

from collections import deque
from fnmatch import fnmatchcase

from .quality_exceptions import for_record
from .source_relations import relationships
from .test_selection import CONTRACTS


def domain(path: str, policy: dict) -> str:
    for item in policy.get("domains", []):
        if any(fnmatchcase(path, pattern) for pattern in item["patterns"]):
            return item["name"]
    return "unclassified"


def query(report: dict, path: str) -> dict:
    records = {r["path"]: r for r in report["files"]}
    if path not in records:
        raise ValueError(f"File is absent from the measured tree: {path}")
    routes = {path: [path]}
    queue = deque([path])
    while queue:
        current = queue.popleft()
        for consumer in records[current]["consumers"]:
            if consumer not in routes:
                routes[consumer] = [*routes[current], consumer]
                queue.append(consumer)
    return {
        "commit": report["commit"],
        "tree": report["tree"],
        "file": records[path],
        "consumer_routes": routes,
        "domains": sorted({records[p]["domain"] for p in routes}),
        "test_groups": sorted({g for p in routes for g in records[p]["test_groups"]}),
        "coverage_gaps": {
            p: records[p]["dynamic_gaps"]
            for p in routes
            if records[p].get("dynamic_gaps")
        },
    }


def attach(
    records: list[dict], sources: dict[str, bytes], policy: dict, as_of: str = ""
) -> dict:
    edges, facts = relationships(
        sources,
        [r["path"] for r in records],
    )
    reverse: dict[str, list[str]] = {}
    for source, targets in edges.items():
        for target in sorted(targets):
            reverse.setdefault(target, []).append(source)
    for record in records:
        path = record["path"]
        record.update(facts.get(path, {}))
        record["domain"] = domain(path, policy)
        record["dependencies"] = sorted(edges.get(path, set()))
        record["consumers"] = sorted(reverse.get(path, []))
        record["test_groups"] = [
            name
            for name, patterns in CONTRACTS.items()
            if any(fnmatchcase(path, p) for p in patterns)
        ]
        record["state_authorities"] = [
            item
            for item in policy.get("state_authorities", [])
            if any(fnmatchcase(path, p) for p in item["patterns"])
        ]
        record["risk"] = (
            "state"
            if record["state_authorities"] or record.get("state_write_candidates")
            else "integration"
            if record["consumers"] or record.get("registrations")
            else "local"
        )
        record["quality_gates"] = ["G1"] + (
            ["G2"] if record["test_groups"] or record["risk"] != "local" else []
        )
        record["exceptions"] = for_record(record, policy, as_of)
    return {
        "scope": "Python and TypeScript/Vue imports, literal dynamic imports and tracked resources; dynamic/template/runtime activation gaps remain explicit",
        "edges": sum(len(targets) for targets in edges.values()),
        "map_gaps": [
            {"path": r["path"], "gaps": r.get("dynamic_gaps", [])}
            for r in records
            if r.get("dynamic_gaps")
        ],
        "unclassified": [r["path"] for r in records if r["domain"] == "unclassified"],
        "unmapped_production": [
            r["path"]
            for r in records
            if r["role"] == "production" and not r["test_groups"]
        ],
    }
