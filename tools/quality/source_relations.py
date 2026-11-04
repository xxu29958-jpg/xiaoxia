"""Extract source evidence for shared resources, registrations and runtime imports."""

import ast
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import PurePosixPath

from . import boundaries
from .frontend import EXTENSIONS, analyze

TABLE = re.compile(
    r"\b(FROM|JOIN|UPDATE|INTO|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+[\"`\[]?([A-Za-z_]\w*)",
    re.I,
)
SQL_STATEMENT = re.compile(
    r"\b(?:SELECT\s+.+?\s+FROM|INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\b",
    re.I | re.S,
)


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{call_name(node.value)}.{node.attr}"
    return ""


def resource_matches(
    source: str, reference: str, resources: Sequence[str]
) -> list[str]:
    # A runtime "policy.json" beside a plugin is not quality/policy.json.
    # Resolve explicit repository paths, then only candidates in the source's
    # directory tree or ancestor directories. Ambiguity remains a map gap.
    if reference in resources and "/" in reference:
        return [reference]
    origin = PurePosixPath(source).parent
    matches = []
    for name in resources:
        target = PurePosixPath(name)
        related = (
            target.parent == origin
            or origin in target.parents
            or target.parent in origin.parents
        )
        if target.name == PurePosixPath(reference).name and related:
            matches.append(name)
    return sorted(matches)


def facts(path: str, text: str) -> dict:
    tree = ast.parse(text, filename=path)
    result: dict = {
        "public_interfaces": [],
        "registrations": [],
        "tables": [],
        "resource_references": [],
        "dynamic_imports": [],
        "dynamic_gaps": [],
        "mutable_globals": [],
        "runtime_patch_candidates": [],
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                result["public_interfaces"].append(
                    {
                        "name": node.name,
                        "line": node.lineno,
                        "kind": type(node).__name__,
                        "signature": ast.dump(node.args)
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        else ",".join(ast.dump(base) for base in node.bases),
                    }
                )
            for decorator in node.decorator_list:
                target = (
                    decorator.func if isinstance(decorator, ast.Call) else decorator
                )
                name = call_name(target)
                if any(
                    part in name
                    for part in ("register", "filter.", "route", "llm_tool")
                ):
                    result["registrations"].append(
                        {"symbol": node.name, "entry": name, "line": decorator.lineno}
                    )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Ordinary prose such as "from memory into context" is not SQL.
            if SQL_STATEMENT.search(node.value):
                result["tables"].extend(
                    {
                        "name": match[2],
                        "operation": match[1].upper(),
                        "line": node.lineno,
                    }
                    for match in TABLE.finditer(node.value)
                )
            if (
                node.value.endswith((".sql", ".json", ".yaml", ".yml", ".toml"))
                and "\n" not in node.value
                and not PurePosixPath(node.value).name.startswith((".", "*"))
            ):
                result["resource_references"].append(
                    {"name": node.value, "line": node.lineno}
                )
        elif isinstance(node, ast.Call) and call_name(node.func).endswith(
            ("import_module", "__import__")
        ):
            value = node.args[0] if node.args else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                result["dynamic_imports"].append(value.value)
            else:
                result["dynamic_gaps"].append(
                    {"line": node.lineno, "kind": "computed_import"}
                )
        elif isinstance(node, ast.Assign):
            attributes = [
                call_name(target)
                for target in node.targets
                if isinstance(target, ast.Attribute)
            ]
            if attributes and isinstance(node.value, (ast.Name, ast.Lambda)):
                result["runtime_patch_candidates"].append(
                    {"targets": attributes, "line": node.lineno}
                )
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
            node.value, (ast.Dict, ast.List, ast.Set)
        ):
            result["mutable_globals"].append(
                {"line": node.lineno, "kind": type(node.value).__name__}
            )
    result["state_write_candidates"] = [
        t for t in result["tables"] if t["operation"] in {"UPDATE", "INTO"}
    ]
    return result


def relationships(
    sources: dict[str, bytes], resources: Sequence[str] = ()
) -> tuple[dict[str, set[str]], dict]:
    scripts = {
        p: data for p, data in sources.items() if PurePosixPath(p).suffix in EXTENSIONS
    }
    sources = {p: data for p, data in sources.items() if p not in scripts}
    module_paths = {boundaries.module_name(path): path for path in sources}
    static = boundaries.graph(sources)
    graph = {
        module_paths[node]: {module_paths[edge] for edge in edges}
        for node, edges in static.items()
    }
    parsed = {
        path: facts(path, data.decode("utf-8-sig")) for path, data in sources.items()
    }
    aliases = {
        name.removeprefix("data.plugins."): path for name, path in module_paths.items()
    }
    all_modules = module_paths | aliases
    resources_by_name = defaultdict(list)
    for resource in resources:
        resources_by_name[PurePosixPath(resource).name].append(resource)
    for path, record in parsed.items():
        for name in record["dynamic_imports"]:
            if name in all_modules:
                graph[path].add(all_modules[name])
            else:
                record["dynamic_gaps"].append(
                    {"kind": "unresolved_literal_import", "module": name}
                )
        for reference in record["resource_references"]:
            matches = resource_matches(
                path,
                reference["name"],
                resources_by_name[PurePosixPath(reference["name"]).name],
            )
            if len(matches) == 1:
                graph[path].update(matches)
            if len(matches) != 1:
                record["dynamic_gaps"].append(
                    {
                        "kind": "resource_resolution",
                        "reference": reference,
                        "matches": matches,
                    }
                )
    frontend = analyze(scripts, list(resources))
    for path, record in frontend.items():
        graph[path] = set(record["dependencies"])
        parsed[path] = record
    return graph, parsed
