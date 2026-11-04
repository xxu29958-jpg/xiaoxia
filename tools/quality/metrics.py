"""Physical spans and Python syntax measurements, without executing source."""

import ast
import copy
import hashlib
from collections import Counter

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
CONTROL = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.Match,
)


def physical_lines(text: str) -> int:
    return len(text.splitlines())


def fingerprint(node: ast.AST) -> str:
    stable = copy.deepcopy(node)
    if isinstance(stable, DEFINITIONS):
        stable.name = "__definition__"
    return hashlib.sha256(ast.dump(stable).encode()).hexdigest()


def nesting(node: ast.AST, depth: int = 0) -> int:
    """Each nested definition starts its own control-flow measurement."""
    highest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, DEFINITIONS):
            continue
        is_elif = (
            isinstance(node, ast.If)
            and isinstance(child, ast.If)
            and child in node.orelse
            and child.col_offset == node.col_offset
        )
        current = depth + int(isinstance(child, CONTROL) and not is_elif)
        highest = max(highest, current, nesting(child, current))
    return highest


def symbols(tree: ast.AST, prefix: str = "") -> list[dict]:
    result = []
    for child in ast.iter_child_nodes(tree):
        if not isinstance(child, DEFINITIONS):
            result.extend(symbols(child, prefix))
            continue
        name = f"{prefix}.{child.name}" if prefix else child.name
        start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
        if child.end_lineno is None:
            raise ValueError(f"Parsed definition has no end location: {name}")
        result.append(
            {
                "name": name,
                "kind": "class" if isinstance(child, ast.ClassDef) else "function",
                "start": start,
                "definition_line": child.lineno,
                "end": child.end_lineno,
                "lines": child.end_lineno - start + 1,
                "control_nesting": nesting(child),
                "fingerprint": fingerprint(child),
            }
        )
        result.extend(symbols(child, name))
    return result


def python_metrics(text: str, filename: str) -> dict:
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError as error:
        return {"parse_error": {"line": error.lineno, "reason": error.msg}}
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append("." * node.level + (node.module or ""))
    definitions = symbols(tree)
    counts = Counter(s["name"] for s in definitions)
    occurrences = Counter()
    for symbol in definitions:
        name = symbol["name"]
        occurrences[name] += 1
        symbol["key"] = name if counts[name] == 1 else f"{name}#{occurrences[name]}"
    return {"symbols": definitions, "imports": sorted(set(imports))}


def threshold(value: int, limits: dict) -> str | None:
    review = limits.get("review_above", limits.get("navigation_review_above"))
    if review is not None and value > review:
        return "review"
    if value > limits["warn_above"]:
        return "warn"
    return None
