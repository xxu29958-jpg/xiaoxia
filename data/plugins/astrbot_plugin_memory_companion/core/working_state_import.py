"""Adopt old dialogue JSON once, leaving its original bytes intact."""

import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from .core_state_versions import CoreStateError, digest, encoded
from .models import utc_now
from .sensitive_data import redact_sensitive_value
from .working_state_rules import valid_clock


def read_legacy(path):
    exists = path.exists()
    raw = path.read_bytes() if exists else b""
    if len(raw) > 8 * 1024 * 1024:
        raise CoreStateError("working_legacy_too_large")
    value = json.loads(raw) if exists else {"version": 1, "sessions": {}}
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("sessions"), dict)
    ):
        raise CoreStateError("working_legacy_invalid")
    for origin, records in value["sessions"].items():
        if not isinstance(origin, str) or not isinstance(records, list):
            raise CoreStateError("working_legacy_invalid")
        for record in records:
            validate_record(record)
    return hashlib.sha256(raw).hexdigest(), value["sessions"]


def validate_record(record):
    if not isinstance(record, dict) or any(
        not isinstance(record.get(k), str) or not record[k].strip()
        for k in ("id", "title", "content", "evidence_quote")
    ):
        raise CoreStateError("working_legacy_invalid")
    for key, limit in (
        ("id", 120),
        ("title", 80),
        ("content", 400),
        ("evidence_quote", 400),
    ):
        if len(record[key]) > limit:
            raise CoreStateError("working_legacy_invalid")
    if record.get("kind") not in {"topic", "shared_joke"} or record.get(
        "status"
    ) not in {"open", "resolved"}:
        raise CoreStateError("working_legacy_invalid")
    for key in ("created_at", "updated_at", "expires_at"):
        valid_clock(record.get(key))


async def adopt(state, path, owner, origins, authorize):
    return await state.store._run_recoverable_database_operation(
        adopt_sync,
        state,
        path,
        owner,
        tuple(origins),
        authorize,
    )


def adopt_sync(state, path, owner, origins, authorize):
    source_hash, sessions = read_legacy(path)
    with state.store._lock, state.store._transaction_sync():
        state._authorize(owner, authorize)
        receipts = [
            adopt_origin(state, owner, origin, sessions.get(origin, []), source_hash)
            for origin in origins
        ]
        state._authorize(owner, authorize)
        if read_legacy(path)[0] != source_hash:
            raise CoreStateError("working_legacy_changed_during_adoption")
        return {
            "sources": receipts,
            "source_hash": source_hash,
            "original_preserved": True,
        }


def adopt_origin(state, owner, origin, originals, source_hash):
    # Per-origin markers permit an explicitly added channel without reimporting
    # already adopted/erased entries, and survive moving the installation.
    source_key = digest(["astrbot_plugin_ada_companion/dialogue.json:v1", origin])
    db = state.store._conn
    row = db.execute(
        "SELECT source_hash,receipt FROM working_imports WHERE owner=? AND source_key=?",
        (owner, source_key),
    ).fetchone()
    if row:
        if row[0] != source_hash:
            raise CoreStateError("working_legacy_changed_after_adoption")
        return json.loads(row[1])
    for original in originals:
        record = imported_record(original, origin, owner, source_hash)
        if state._current(owner, record["id"]):
            raise CoreStateError("working_legacy_id_conflict")
        state.write(record, importing=True)
    receipt = {
        "imported": len(originals),
        "source_hash": source_hash,
        "recorded_at": utc_now(),
    }
    db.execute(
        "INSERT INTO working_imports VALUES(?,?,?,?)",
        (owner, source_key, source_hash, encoded(receipt)),
    )
    return receipt


def imported_record(original, origin, owner, source_hash):
    fields = {
        "id",
        "kind",
        "title",
        "content",
        "evidence_quote",
        "status",
        "created_at",
        "updated_at",
        "expires_at",
        "resolution_quote",
    }
    record = redact_sensitive_value(
        deepcopy({k: v for k, v in original.items() if k in fields})
    )
    record.update(
        owner=owner,
        revision=1,
        version=uuid4().hex,
        recorded_at=utc_now(),
        source={
            "kind": "legacy",
            "origin": origin,
            "source_hash": source_hash,
            "text": record["evidence_quote"],
            "meaning": "Legacy dialogue statement; original event identity and confirmation are unknown.",
        },
    )
    if record["status"] == "resolved":
        record["resolution_basis"] = "legacy_report_not_execution_receipt"
    return record
