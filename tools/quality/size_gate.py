"""Compare real source metrics to the same predecessor, including file renames."""

import ast
import copy
import hashlib

from .metrics import DEFINITIONS, physical_lines, python_metrics
from .ownership import file_kind, findings


def identities(text: str) -> dict[str, str]:
    tree = ast.parse(text)
    result = {}

    def visit(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, DEFINITIONS):
                name = f"{prefix}.{child.name}" if prefix else child.name
                stable = copy.deepcopy(child)
                stable.name = "__definition__"
                digest = hashlib.sha256(ast.dump(stable).encode()).hexdigest()
                result[name] = digest
                visit(child, name)
            else:
                visit(child, prefix)

    visit(tree)
    return result


def measurements(
    path: str, data: bytes | None, policy: dict
) -> tuple[list[dict], dict]:
    if data is None:
        return [], {}
    text = data.decode("utf-8-sig")
    record = {"kind": file_kind(path), "lines": physical_lines(text)}
    identity = {}
    if path.endswith((".py", ".pyi")):
        metrics = python_metrics(text, path)
        if "parse_error" in metrics:
            raise ValueError(f"{path}: {metrics['parse_error']}")
        record.update(metrics)
        identity = identities(text)
    return findings(record, policy["thresholds"]), identity


def compare(
    path: str, previous: str, before: bytes | None, after: bytes | None, policy: dict
) -> list[dict]:
    old, old_ids = measurements(previous, before, policy)
    new, new_ids = measurements(path, after, policy)
    old_values = {(f["metric"], f.get("symbol")): f["value"] for f in old}
    old_by_identity = {value: key for key, value in old_ids.items()}
    result = []
    if (
        before is not None
        and file_kind(previous) == "production"
        and file_kind(path) != "production"
    ):
        result.append(
            {
                "path": path,
                "previous_path": previous,
                "metric": "maintenance_responsibility",
                "level": "review",
                "state": "reclassified",
                "blocking": True,
            }
        )
    for finding in new:
        name = finding.get("symbol")
        old_name = name if name in old_ids else old_by_identity.get(new_ids.get(name))
        old_value = old_values.get((finding["metric"], old_name))
        state = "new" if old_value is None else "unchanged"
        if old_value is not None and finding["value"] != old_value:
            state = "worsened" if finding["value"] > old_value else "reduced"
        result.append(
            {
                **finding,
                "path": path,
                "previous_path": previous,
                "previous_symbol": old_name,
                "previous_value": old_value,
                "state": state,
                "blocking": finding["level"] == "review"
                and state in {"new", "worsened"},
            }
        )
    return result
