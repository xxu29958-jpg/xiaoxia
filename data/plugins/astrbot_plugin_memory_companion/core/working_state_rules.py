"""Working objects describe continuing intentions, never proof of external execution."""

import math
from copy import deepcopy
from uuid import uuid4

from .core_state_versions import CoreStateError, digest
from .models import clean_text, utc_now
from .sensitive_data import redact_sensitive_text, redact_sensitive_value

KINDS = frozenset({"topic", "shared_joke", "goal"})
ACTIONS = frozenset({"save", "resolve", "cancel", "forget"})


def normalize_change(change):
    action = change.get("action", "")
    if action not in ACTIONS:
        raise CoreStateError("invalid_working_action")
    result = {
        "action": action,
        "item_id": clean_text(change.get("item_id"), 120),
        "expected_version": clean_text(change.get("expected_version"), 80),
        "evidence_quote": str(change.get("evidence_quote", "")).strip(),
    }
    if len(result["evidence_quote"]) > 400:
        raise CoreStateError("working_quote_too_long")
    if action == "save":
        kind = str(change.get("kind", "")).strip().lower()
        title = clean_text(change.get("title"), 81)
        content = clean_text(change.get("content"), 2001)
        if kind not in KINDS | {""} or not title or not content:
            raise CoreStateError("invalid_working_object")
        if len(title) > 80 or len(content) > 2000:
            raise CoreStateError("working_object_too_long")
        result.update(
            kind=kind,
            title=redact_sensitive_text(title),
            content=redact_sensitive_text(content),
        )
    elif not result["item_id"]:
        raise CoreStateError("working_id_required")
    return result


def verify_quote(change, source):
    quote = redact_sensitive_text(change["evidence_quote"])
    minimum = 3 if change["action"] == "save" else 1
    if len(quote) < minimum or quote not in source.get("text", ""):
        raise CoreStateError("current_user_evidence_required")
    change["evidence_quote"] = quote


def valid_clock(now):
    if not isinstance(now, (float, int)) or not math.isfinite(now) or now < 0:
        raise CoreStateError("working_time_invalid")


def visible(record, now):
    return record["status"] == "open" and (
        record["expires_at"] == 0 or record["expires_at"] > now
    )


def check_version(current, change, source):
    expected = change["expected_version"]
    if current and expected != current["version"]:
        raise CoreStateError("working_version_conflict")
    if not current and expected:
        raise CoreStateError("working_object_missing")
    previous = current.get("source", {}) if current else {}
    if (
        source.get("kind") == "user_message"
        and previous.get("received_ns", 0) > source["received_ns"]
    ):
        raise CoreStateError("working_source_is_older")


def revised(current, change, source, *, owner, item_id, now):
    record = (
        deepcopy(current)
        if current
        else {"id": item_id, "owner": owner, "created_at": now, "revision": 0}
    )
    record.update(
        revision=record["revision"] + 1,
        version=uuid4().hex,
        updated_at=now,
        recorded_at=utc_now(),
        source=redact_sensitive_value(source),
        evidence_quote=change["evidence_quote"],
    )
    if change["action"] == "save":
        kind = change["kind"] or (current["kind"] if current else "topic")
        record.update(
            kind=kind,
            title=change["title"],
            content=change["content"],
            status="open",
            expires_at=0
            if kind == "goal"
            else now + (30 if kind == "shared_joke" else 7) * 86400,
        )
        record.pop("resolution_quote", None)
        record.pop("resolution_basis", None)
    else:
        record.update(
            status="cancelled" if change["action"] == "cancel" else "resolved",
            resolution_quote=change["evidence_quote"],
            resolution_basis="source_report_not_execution_receipt",
        )
    return record


def object_id(owner, kind, title):
    return "work_" + digest([owner, kind, title.casefold()])[:32]


def public_record(record):
    """Bound quoted source text without changing persisted evidence or identity."""
    result = deepcopy(record)
    source = result["source"]
    text = source.get("text", "")
    source.update(text=text[:2000], text_truncated=len(text) > 2000)
    return result
