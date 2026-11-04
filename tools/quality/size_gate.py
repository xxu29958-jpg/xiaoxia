"""Compare real source metrics to the same predecessor, including file renames."""

from datetime import UTC, datetime

from .line_counts import role
from .metrics import physical_lines, python_metrics
from .ownership import file_kind, findings
from .quality_exceptions import apply


def identities(text: str) -> dict[str, str]:
    metrics = python_metrics(text, "identity.py")
    if "parse_error" in metrics:
        raise ValueError(f"Cannot identify invalid Python: {metrics['parse_error']}")
    return {s["key"]: s["fingerprint"] for s in metrics["symbols"]}


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
    carry_layout_findings(path, after, old, new, old_ids, new_ids)
    result = []
    if before is not None and (
        (file_kind(previous) == "production" and file_kind(path) != "production")
        or (role(previous) == "production" and role(path) != "production")
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
        layout_only = (
            name
            and old_name
            and new_ids.get(name) == old_ids.get(old_name)
            and state == "reduced"
        )
        item = {
            **finding,
            "path": path,
            "previous_path": previous,
            "previous_symbol": old_name,
            "previous_value": old_value,
            "state": "layout_only"
            if layout_only or finding.get("layout_only_threshold_crossing")
            else state,
            "blocking": finding["level"] == "review" and state in {"new", "worsened"},
        }
        result.append(apply(item, policy, new_ids.get(name), datetime.now(UTC).date()))
    return result


def carry_layout_findings(
    path: str, after: bytes | None, old: list, new: list, old_ids: dict, new_ids: dict
) -> None:
    """Retain debt when an identical definition crosses a physical threshold."""
    if not after or not path.endswith((".py", ".pyi")):
        return
    covered = {(f["metric"], f.get("symbol")) for f in new}
    current = {
        s["key"]: s for s in python_metrics(after.decode("utf-8-sig"), path)["symbols"]
    }
    for finding in old:
        symbol = finding.get("symbol")
        names = [
            name for name, digest in new_ids.items() if digest == old_ids.get(symbol)
        ]
        for name in names:
            if not symbol or (finding["metric"], name) in covered:
                continue
            measured = current[name]
            metric = (
                "control_nesting" if finding["metric"] == "control_nesting" else "lines"
            )
            new.append(
                {
                    **finding,
                    "symbol": name,
                    "line": measured["start"],
                    "value": measured[metric],
                    "layout_only_threshold_crossing": True,
                }
            )
