"""Versioned core facts retain their source without treating recall as new evidence."""

import hashlib
import json

from .models import MemoryRecord, json_dumps
from .sensitive_data import redact_sensitive_value


class CoreStateError(ValueError):
    pass


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def revision(record: MemoryRecord | None) -> int:
    if record is None:
        return 0
    try:
        return max(0, int(record.metadata.get("core_revision") or 0))
    except (ValueError, TypeError):
        return 0


def source_for(record: MemoryRecord) -> dict:
    return record.metadata.get("core_source") or {
        "kind": "legacy",
        "origin": record.session_id,
        "message_id": record.message_id,
        "evidence": record.evidence,
        "occurred_at": record.occurred_at,
        "meaning": "Retained legacy evidence; original user confirmation is not inferred.",
    }


def retain_version(db, record: MemoryRecord, source: dict | None = None):
    db.execute(
        "INSERT OR IGNORE INTO core_memory_revisions(memory_id,revision,record,source,recorded_at) "
        "VALUES(?,?,?,?,?)",
        (
            record.id,
            revision(record),
            json_dumps(record.to_db()),
            encoded(
                redact_sensitive_value(
                    source if source is not None else source_for(record)
                )
            ),
            record.updated_at,
        ),
    )


def permitted(record: MemoryRecord, scopes: list[dict]) -> bool:
    identity = (
        record.session_id,
        record.platform,
        record.subject.id,
        record.owner_bot_id or record.metadata.get("owner_bot_id"),
    )
    return (
        record.memory_type == "core_memory"
        and record.scope == "private"
        and record.visibility == "private_pair"
        and record.subject.kind == "user"
        and any(
            identity
            == tuple(
                scope.get(k, "")
                for k in ("session_id", "platform", "user_id", "bot_id")
            )
            and record.metadata.get("persona_id", "")
            in {"", scope.get("persona_id", "")}
            for scope in scopes
        )
    )


def check_source(source: dict, scopes: list[dict]):
    if source.get("kind") == "management":
        return
    if (
        source.get("kind") != "user_message"
        or not source.get("message_id")
        or not isinstance(source.get("text"), str)
        or not source["text"].strip()
        or len(source["text"].encode()) > 256 * 1024
        or not isinstance(source.get("received_ns"), int)
        or source["received_ns"] <= 0
        or not any(
            (
                source.get("origin"),
                source.get("platform"),
                source.get("user_id"),
                source.get("bot_id"),
            )
            == tuple(
                scope.get(k, "")
                for k in ("session_id", "platform", "user_id", "bot_id")
            )
            for scope in scopes
        )
    ):
        raise CoreStateError("core_source_required")


def check_change(current, candidate, expected, scopes, source):
    if not permitted(candidate, scopes) or (current and not permitted(current, scopes)):
        raise CoreStateError("core_scope_changed")
    if revision(current) != expected:
        raise CoreStateError("core_revision_conflict")
    previous = source_for(current) if current else {}
    if (
        source.get("kind") == "user_message"
        and previous.get("received_ns", 0) > source["received_ns"]
    ):
        raise CoreStateError("core_source_is_older")


def apply_source(record: MemoryRecord, source: dict):
    metadata = {
        **record.metadata,
        "core_source": redact_sensitive_value(
            {k: v for k, v in source.items() if k != "text"}
        ),
    }
    record.metadata = metadata
    if source.get("kind") == "user_message":
        record.message_id = source["message_id"]
        record.evidence = source["text"]
        record.occurred_at = source.get("occurred_at", "")
        record.metadata["managed_by"] = "owner_chat"
        record.reality_level = "explicit_user_statement"
    else:
        record.metadata["managed_by"] = "memory_panel"
