"""A revisable interpretation is a hypothesis about an event, not another event."""

from copy import deepcopy
from uuid import uuid4

from .core_state_versions import CoreStateError
from .sensitive_data import redact_sensitive_text

FIELDS = {
    "item_id": 120,
    "expected_version": 80,
    "title": 100,
    "understanding": 1000,
    "response_implication": 600,
    "uncertainty": 400,
    "evidence_quote": 400,
    "status": 20,
}


def normalize(change, source):
    if not isinstance(change, dict) or set(change) - FIELDS.keys():
        raise CoreStateError("invalid_appraisal_fields")
    result = {}
    for field, limit in FIELDS.items():
        value = change.get(field, "")
        if not isinstance(value, str) or len(value) > limit:
            raise CoreStateError("invalid_appraisal_field_" + field)
        result[field] = redact_sensitive_text(value.strip())
    if result["status"] not in {"active", "resolved"}:
        raise CoreStateError("invalid_appraisal_status")
    if not all(result[k] for k in ("title", "understanding", "response_implication")):
        raise CoreStateError("incomplete_appraisal")
    quote = result["evidence_quote"]
    if (
        source["kind"] != "user_message"
        or len(quote) < 3
        or quote not in source["text"]
    ):
        raise CoreStateError("current_user_evidence_required")
    return result


def check_version(current, change, source):
    if current is None:
        if change["item_id"] or change["expected_version"]:
            raise CoreStateError("appraisal_missing")
        return
    if current["version"] != change["expected_version"]:
        raise CoreStateError("appraisal_version_conflict")
    if source["received_ns"] < current["source"]["received_ns"]:
        raise CoreStateError("appraisal_source_is_older")


def revise(current, change, source, *, owner, item_id, now):
    record = (
        deepcopy(current)
        if current
        else {
            "id": item_id,
            "owner": owner,
            "created_at": now,
            "revision": 0,
            "original_source": deepcopy(source),
            "original_quote": change["evidence_quote"],
            "basis": "model_interpretation_of_user_report_not_observed_fact",
        }
    )
    record.update(
        {k: change[k] for k in FIELDS if k not in {"item_id", "expected_version"}}
    )
    record.update(
        revision=record["revision"] + 1,
        version=uuid4().hex,
        updated_at=now,
        source=deepcopy(source),
    )
    return record


def public_record(record):
    result = deepcopy(record)
    for field in ("source", "original_source"):
        source = result[field]
        text = source["text"]
        source.update(text=text[:500], text_truncated=len(text) > 500)
    return result
