"""Bounded size exceptions retain identity, evidence, expiry and renewal history."""

from datetime import date

SIZE_RULES = {
    "function_lines",
    "class_lines",
    "production_file_lines",
    "control_nesting",
}
REQUIRED = {
    "id",
    "metric",
    "path",
    "symbol",
    "fingerprint",
    "maximum",
    "reason",
    "alternative_guarantees",
    "validation",
    "owner",
    "exit_trigger",
    "review_by",
    "history",
}


def validate(policy: dict, previous: dict | None = None) -> list[dict]:
    issues = []
    seen = set()
    for item in policy.get("exceptions", []):
        missing = sorted(key for key in REQUIRED if not item.get(key))
        try:
            date.fromisoformat(item.get("review_by", ""))
        except ValueError:
            missing.append("valid_review_by")
        identifier = item.get("id")
        if identifier in seen or missing or item.get("metric") not in SIZE_RULES:
            issues.append(
                {"id": identifier, "rule": "invalid_exception", "missing": missing}
            )
        seen.add(identifier)
    current = {e.get("id"): e for e in policy.get("exceptions", [])}
    for item in (previous or {}).get("exceptions", []):
        successor = current.get(item["id"], {})
        old_history = item.get("history", [])
        if successor.get("history", [])[: len(old_history)] != old_history:
            issues.append({"id": item["id"], "rule": "exception_history_erased"})
        if not successor:
            issues.append(
                {"id": item["id"], "rule": "exception_deleted_instead_of_retired"}
            )
    return issues


def matching(item: dict, path: str, symbol: str | None, digest: str | None) -> bool:
    return (item.get("fingerprint") == digest and bool(digest)) or (
        item.get("path") == path and item.get("symbol") == symbol
    )


def apply(finding: dict, policy: dict, digest: str | None, today: date) -> dict:
    candidates = [
        item
        for item in policy.get("exceptions", [])
        if item.get("metric") == finding["metric"]
        and (
            matching(item, finding["path"], finding.get("symbol"), digest)
            or matching(
                item, finding["previous_path"], finding.get("previous_symbol"), digest
            )
        )
    ]
    if not candidates:
        return finding
    item = candidates[0]
    valid = not validate({"exceptions": [item]}) and len(candidates) == 1
    valid = valid and item.get("status", "active") == "active"
    valid = valid and item.get("fingerprint") == digest
    valid = valid and finding.get("value", 0) <= item.get("maximum", 0)
    valid = valid and item["review_by"] >= today.isoformat()
    return {
        **finding,
        "blocking": not valid,
        "exception": {
            **item,
            "status_at_check": "valid" if valid else "requires_review",
            "checked_on": today.isoformat(),
        },
    }


def for_record(record: dict, policy: dict, as_of: str) -> list[dict]:
    result = []
    for item in policy.get("exceptions", []):
        matches = [
            s
            for s in record.get("symbols", [])
            if matching(item, record["path"], s["name"], s.get("fingerprint"))
        ]
        if not matches and item.get("path") != record["path"]:
            continue
        result.append(
            {
                **item,
                "measured_path": record["path"],
                "expired_at_revision": item["review_by"] < as_of,
                "identity_matches": any(
                    s["fingerprint"] == item["fingerprint"] for s in matches
                ),
            }
        )
    return result
