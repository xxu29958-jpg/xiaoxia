"""Conservative source evidence for readers and writers of shared containers.

These are possible data-flow consumers, not import edges or proof of activation.
No product module is imported. Rebound names and escaped aliases stay explicit.
"""

import ast
from collections import defaultdict
from pathlib import PurePosixPath

from .boundaries import module_name

MUTATORS = {
    "append",
    "extend",
    "insert",
    "add",
    "update",
    "clear",
    "pop",
    "remove",
    "discard",
    "setdefault",
}
WRITE_ONLY = MUTATORS - {"pop", "setdefault"}


def expression(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = expression(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def import_bindings(path: str, tree: ast.AST) -> dict[str, str | None]:
    package = module_name(path).split(".")
    if PurePosixPath(path).stem != "__init__":
        package.pop()
    choices = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                choices[alias.asname or alias.name.split(".")[0]].add(
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            base = ".".join([*prefix, *([node.module] if node.module else [])])
            for alias in node.names:
                choices[alias.asname or alias.name].add(f"{base}.{alias.name}")
    return {
        name: next(iter(values)) if len(values) == 1 else None
        for name, values in choices.items()
    }


def definitions(nodes: list[ast.stmt], prefix: str):
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{prefix}.{node.name}"
            yield name, node
            yield from definitions(node.body, name)


def containers(tree: ast.Module) -> dict[str, int]:
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
            node.value, (ast.Dict, ast.List, ast.Set)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    result[target.id] = node.lineno
    return result


def rebound(tree: ast.Module, declared: dict[str, int]) -> set[str]:
    # A file-wide exclusion is deliberately conservative when scopes disagree.
    # Do not treat a parameter or replacement object as the imported singleton.
    return {
        node.arg if isinstance(node, ast.arg) else expression(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.arg)
        or (
            isinstance(node, (ast.Name, ast.Attribute))
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and declared.get(expression(node)) != node.lineno
        )
    }


def index(sources: dict[str, bytes]) -> dict:
    records = {}
    for path, data in sources.items():
        if not path.endswith((".py", ".pyi")):
            continue
        try:
            tree = ast.parse(data.decode("utf-8-sig"), filename=path)
        except (SyntaxError, UnicodeError):
            continue  # source_relations retains parse errors; no invented edges.
        module = module_name(path)
        declared = containers(tree)
        bindings = import_bindings(path, tree)
        bindings.update(
            {
                node.name: f"{module}.{node.name}"
                for node in tree.body
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                )
            }
        )
        bindings.update({name: f"{module}.{name}" for name in declared})
        records[module] = {
            "path": path,
            "tree": tree,
            "bindings": bindings,
            "rebound": rebound(tree, declared),
            "containers": declared,
            "functions": dict(definitions(tree.body, module)),
        }
    return records


def canonical(name: str, records: dict) -> str:
    visited = set()
    while name and name not in visited:
        visited.add(name)
        owner, _, symbol = name.rpartition(".")
        if owner not in records and "data.plugins." + owner in records:
            owner = "data.plugins." + owner
            name = owner + "." + symbol
        record = records.get(owner, {})
        target = record.get("bindings", {}).get(symbol, name)
        if target is None or symbol in record.get("rebound", set()):
            return ""
        if target == name:
            return name
        name = target
    return ""


def resolve(record: dict, node: ast.AST, records: dict) -> str:
    name = expression(node)
    first, separator, tail = name.partition(".")
    parts = name.split(".")
    if any(".".join(parts[:i]) in record["rebound"] for i in range(1, len(parts) + 1)):
        return ""
    base = record["bindings"].get(first)
    return (
        canonical(base + (separator + tail if separator else ""), records)
        if base
        else ""
    )


def access_sites(record: dict, records: dict, states: dict) -> list[dict]:
    tree = record["tree"]
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
            node.ctx, ast.Load
        ):
            continue
        state = resolve(record, node, records)
        if state not in states:
            continue
        parent = parents.get(node)
        mutation = isinstance(parent, ast.Subscript) and isinstance(
            parent.ctx, (ast.Store, ast.Del)
        )
        method = (
            parent.attr
            if isinstance(parent, ast.Attribute)
            and isinstance(parents.get(parent), ast.Call)
            else ""
        )
        if mutation or method in MUTATORS:
            sites.append({"state": state, "role": "write", "line": node.lineno})
        if not mutation and method not in WRITE_ONLY:
            sites.append({"state": state, "role": "read", "line": node.lineno})
    return sites


def calls(record: dict, node: ast.AST, records: dict) -> list[tuple[str, int]]:
    return [
        (resolve(record, item.func, records), item.lineno)
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
    ]


def effects(records: dict, sites: dict) -> dict[str, set[str]]:
    writes, references = {}, {}
    for record in records.values():
        for name, node in record["functions"].items():
            writes[name] = {
                site["state"]
                for site in sites[record["path"]]
                if site["role"] == "write"
                and node.lineno <= site["line"] <= node.end_lineno
            }
            references[name] = calls(record, node, records)
    changed = True
    while changed:
        changed = False
        for name, targets in references.items():
            inherited = set().union(
                *(writes.get(target, set()) for target, _ in targets)
            )
            if inherited - writes[name]:
                writes[name].update(inherited)
                changed = True
    return writes


def binding_gaps(
    record: dict, records: dict, states: dict, written: dict
) -> list[dict]:
    gaps = []
    for name in sorted(record["rebound"]):
        first, separator, tail = name.partition(".")
        target = record["bindings"].get(first)
        if not target:
            continue
        target += separator + tail
        resolved = canonical(target, records) or target
        affected = ({resolved} & states.keys()) | written.get(resolved, set())
        for state in sorted(affected):
            gaps.append(
                {
                    "kind": "shared_state_binding",
                    "symbol": name,
                    "state": state,
                    "reason": "rebound or shadowed; identity not assumed",
                }
            )
    for node in ast.walk(record["tree"]):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value:
            state = resolve(record, node.value, records)
            if state in states:
                gaps.append(
                    {
                        "kind": "shared_state_binding",
                        "line": node.lineno,
                        "state": state,
                        "reason": "container alias escapes this direct-binding analysis",
                    }
                )
    return gaps


def analyze(sources: dict[str, bytes]) -> dict:
    records = index(sources)
    states = {
        f"{module}.{name}": {"path": record["path"], "symbol": name, "line": line}
        for module, record in records.items()
        for name, line in record["containers"].items()
    }
    sites = {
        record["path"]: access_sites(record, records, states)
        for record in records.values()
    }
    written = effects(records, sites)
    for record in records.values():
        for target, line in calls(record, record["tree"], records):
            sites[record["path"]].extend(
                {"state": state, "role": "write", "line": line, "via": target}
                for state in sorted(written.get(target, set()))
            )
    writers = defaultdict(set)
    for path, accesses in sites.items():
        for site in accesses:
            if site["role"] == "write":
                writers[site["state"]].add(path)
    result = {}
    for record in records.values():
        path = record["path"]
        dependencies = {
            writer
            for site in sites[path]
            if site["role"] == "read"
            for writer in writers[site["state"]]
            if writer != path
        }
        result[path] = {
            "shared_state_accesses": [
                {**site, "authority": states[site["state"]]} for site in sites[path]
            ],
            "business_dependencies": sorted(dependencies),
            "shared_state_gaps": binding_gaps(record, records, states, written),
        }
    return result


def extend_impact(sources: dict[str, bytes], graph: dict, facts: dict) -> None:
    """Add business evidence to an impact graph, never a cycle-check graph."""
    shared = analyze(sources)
    for path, record in shared.items():
        target = facts.setdefault(path, {})
        target.update({k: v for k, v in record.items() if k != "shared_state_gaps"})
        target.setdefault("dynamic_gaps", []).extend(record["shared_state_gaps"])
        graph.setdefault(path, set()).update(record["business_dependencies"])


def uncertainties(routes: dict, evidence: dict) -> dict:
    # An unresolved consumer may lack an edge. Its gap must still expand checks
    # when a reachable writer touches the same singleton.
    states = {
        site["state"]
        for path in routes
        for site in evidence.get(path, {}).get("shared_state_accesses", [])
    }
    return {
        path: gaps
        for path, record in evidence.items()
        if (gaps := record.get("dynamic_gaps", []))
        and (path in routes or any(g.get("state") in states for g in gaps))
    }
