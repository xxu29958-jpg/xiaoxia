from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .source_semantic_authorization import (
    _authorization_result,
    _build_scope_matches,
    _writable_generation,
)
from .source_semantic_common import _SOURCE_EVENT_SQL, _canonical_json, _json_object


def register_generation(
    conn: sqlite3.Connection,
    generation: str,
    configuration: Mapping[str, Any],
    *,
    created_at: str,
) -> int:
    """Start a generation after its caller records the full-scan plan."""
    required = {
        "provider_id",
        "model_revision",
        "dimensions",
        "distance_metric",
        "query_encoding",
        "document_encoding",
        "processing_version",
        "fragment_profile",
        "window_profile",
        "build_scope",
    }
    if not isinstance(generation, str) or not generation or len(generation) > 160:
        raise ValueError("invalid_semantic_generation")
    if not isinstance(configuration, Mapping) or not required.issubset(configuration):
        raise ValueError("semantic_generation_config_incomplete")
    dimensions = configuration.get("dimensions")
    if (
        isinstance(dimensions, bool)
        or not isinstance(dimensions, int)
        or dimensions < 1
    ):
        raise ValueError("semantic_generation_dimensions_invalid")
    _build_scope_matches(configuration.get("build_scope"), {}, {})
    config_json = _canonical_json(dict(configuration))
    config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
    history_backfill = configuration.get("history_backfill", True) is True
    row = conn.execute(
        "SELECT revision FROM source_semantic_revision WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise RuntimeError("source_semantic_schema_missing")
    base_sequence = int(row[0])
    try:
        conn.execute(
            """INSERT INTO source_semantic_generations
                (generation,config_hash,config_json,state,base_sequence,scan_cursor,
                 checkpoint_sequence,created_at,updated_at)
                VALUES (?,?,?,'building',?,?,?, ?,?)""",
            (
                generation,
                config_hash,
                config_json,
                base_sequence,
                '{"complete":true,"id":""}' if not history_backfill else "{}",
                base_sequence,
                created_at,
                created_at,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("semantic_generation_already_exists") from exc
    return base_sequence


def find_active_generation(
    conn: sqlite3.Connection, config_hash: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM source_semantic_generations
           WHERE config_hash=? AND state IN ('building','ready','paused')
           ORDER BY created_at DESC,generation DESC LIMIT 1""",
        (config_hash,),
    ).fetchone()
    return dict(row) if row is not None else None


def enqueue_semantic_source(
    conn: sqlite3.Connection,
    generation: str,
    source_id: str,
    *,
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> bool:
    """Durably enqueue one current source without copying its body."""
    generation_row = _writable_generation(conn, generation)
    build_scope = _json_object(generation_row["config_json"]).get("build_scope")
    raw = conn.execute(
        "SELECT * FROM timeline WHERE id=? AND event_type IN " + _SOURCE_EVENT_SQL,
        (source_id,),
    ).fetchone()
    if raw is None:
        return False
    row = dict(raw)
    metadata = _json_object(row.get("metadata"))
    if not _build_scope_matches(
        build_scope, row, metadata
    ) or not _authorization_result(authorize_source, row, metadata):
        return False
    revision = conn.execute(
        "SELECT revision FROM source_semantic_revision WHERE singleton=1",
    ).fetchone()
    if revision is None:
        raise RuntimeError("source_semantic_schema_missing")
    conn.execute(
        """INSERT OR IGNORE INTO source_semantic_dirty(
             generation,source_id,change_sequence,operation,old_present,
             new_source_id,new_present,new_scope,new_session_id,new_event_type,
             new_subject_id,new_object_id,new_owner_bot_id,new_bot_id,new_platform,
             new_persona_id,new_participant_user_id,new_occurred_at,new_created_at
           ) VALUES (?,?,?,'upsert',0,?,1,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            generation,
            source_id,
            int(revision[0]),
            source_id,
            row.get("scope"),
            row.get("session_id"),
            row.get("event_type"),
            row.get("subject_id"),
            row.get("object_id"),
            metadata.get("owner_bot_id", ""),
            metadata.get("bot_id", ""),
            metadata.get("platform", ""),
            metadata.get("persona_id", ""),
            metadata.get("participant_user_id", ""),
            row.get("occurred_at"),
            row.get("created_at"),
        ),
    )
    return True


def list_generations(
    conn: sqlite3.Connection,
    *,
    states: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return generation metadata without exposing source text."""
    allowed = {"building", "ready", "paused", "failed", "retired"}
    if states is None:
        rows = conn.execute(
            "SELECT * FROM source_semantic_generations ORDER BY created_at,generation"
        ).fetchall()
    else:
        requested = {str(state) for state in states}
        if not requested or not requested <= allowed:
            raise ValueError("semantic_generation_state_invalid")
        placeholders = ",".join("?" for _ in requested)
        rows = conn.execute(
            f"SELECT * FROM source_semantic_generations WHERE state IN ({placeholders}) "
            "ORDER BY created_at,generation",
            sorted(requested),
        ).fetchall()
    return [dict(row) for row in rows]


def generation_status(conn: sqlite3.Connection, generation: str) -> dict[str, Any]:
    """Expose bounded maintenance counters used by the worker and diagnostics."""
    row = conn.execute(
        "SELECT * FROM source_semantic_generations WHERE generation=?", (generation,)
    ).fetchone()
    if row is None:
        raise ValueError("semantic_generation_not_found")
    try:
        cursor = json.loads(row["scan_cursor"] or "{}")
    except (TypeError, ValueError):
        cursor = {}
    current = conn.execute(
        "SELECT revision FROM source_semantic_revision WHERE singleton=1"
    ).fetchone()
    counts = conn.execute(
        """SELECT
             SUM(CASE WHEN state='ready' THEN 1 ELSE 0 END) AS ready_documents,
             SUM(CASE WHEN state='pending' THEN 1 ELSE 0 END) AS pending_documents,
             SUM(CASE WHEN state='stale' THEN 1 ELSE 0 END) AS stale_documents,
             COUNT(*) AS document_count
           FROM source_semantic_documents WHERE generation=?""",
        (generation,),
    ).fetchone()
    dirty = conn.execute(
        "SELECT COUNT(*) FROM source_semantic_dirty WHERE generation=?", (generation,)
    ).fetchone()
    order_changes = conn.execute(
        "SELECT COUNT(*) FROM source_semantic_order_changes WHERE generation=?",
        (generation,),
    ).fetchone()
    return {
        "generation": generation,
        "state": row["state"],
        "config_hash": row["config_hash"],
        "base_sequence": int(row["base_sequence"]),
        "checkpoint_sequence": int(row["checkpoint_sequence"]),
        "source_revision": int(current[0]) if current is not None else None,
        "scan_complete": bool(
            isinstance(cursor, dict) and cursor.get("complete") is True
        ),
        "scan_cursor": cursor.get("id", "") if isinstance(cursor, dict) else "",
        "dirty_count": int(dirty[0] or 0) if dirty is not None else 0,
        "order_change_count": int(order_changes[0] or 0)
        if order_changes is not None
        else 0,
        "document_count": int(counts["document_count"] or 0)
        if counts is not None
        else 0,
        "ready_documents": int(counts["ready_documents"] or 0)
        if counts is not None
        else 0,
        "pending_documents": int(counts["pending_documents"] or 0)
        if counts is not None
        else 0,
        "stale_documents": int(counts["stale_documents"] or 0)
        if counts is not None
        else 0,
    }


def pause_generation(
    conn: sqlite3.Connection,
    generation: str,
    *,
    updated_at: str,
) -> bool:
    """Pause a generation while keeping its change queue active."""
    cursor = conn.execute(
        """UPDATE source_semantic_generations SET state='paused',updated_at=?
           WHERE generation=? AND state IN ('building','ready')""",
        (updated_at, generation),
    )
    return cursor.rowcount == 1


def resume_generation(
    conn: sqlite3.Connection,
    generation: str,
    *,
    updated_at: str,
) -> bool:
    """Resume work; the next maintenance pass decides whether it is ready."""
    cursor = conn.execute(
        """UPDATE source_semantic_generations SET state='building',updated_at=?
           WHERE generation=? AND state='paused'""",
        (updated_at, generation),
    )
    return cursor.rowcount == 1


def retire_generation(
    conn: sqlite3.Connection,
    generation: str,
    *,
    updated_at: str,
) -> bool:
    """Retire a generation so in-flight results cannot be committed."""
    cursor = conn.execute(
        """UPDATE source_semantic_generations SET state='retired',updated_at=?
           WHERE generation=? AND state!='retired'""",
        (updated_at, generation),
    )
    return cursor.rowcount == 1


def mark_generation_ready(
    conn: sqlite3.Connection,
    generation: str,
    *,
    updated_at: str,
) -> dict[str, Any]:
    """Publish ready only after scan, order changes, dirty rows and documents settle."""
    row = conn.execute(
        "SELECT * FROM source_semantic_generations WHERE generation=?", (generation,)
    ).fetchone()
    if row is None:
        raise ValueError("semantic_generation_not_found")
    if row["state"] != "building":
        raise ValueError("semantic_generation_not_building")
    try:
        cursor = json.loads(row["scan_cursor"] or "{}")
    except (TypeError, ValueError):
        cursor = {}
    current = conn.execute(
        "SELECT revision FROM source_semantic_revision WHERE singleton=1"
    ).fetchone()
    config = _json_object(row["config_json"])
    blockers = {
        "scan_complete": config.get("history_backfill", True) is True
        and not (isinstance(cursor, dict) and cursor.get("complete") is True),
        "dirty_count": int(
            conn.execute(
                "SELECT COUNT(*) FROM source_semantic_dirty WHERE generation=?",
                (generation,),
            ).fetchone()[0]
            or 0
        ),
        "order_change_count": int(
            conn.execute(
                "SELECT COUNT(*) FROM source_semantic_order_changes WHERE generation=?",
                (generation,),
            ).fetchone()[0]
            or 0
        ),
        "pending_documents": int(
            conn.execute(
                "SELECT COUNT(*) FROM source_semantic_documents WHERE generation=? AND state='pending'",
                (generation,),
            ).fetchone()[0]
            or 0
        ),
        "stale_documents": int(
            conn.execute(
                "SELECT COUNT(*) FROM source_semantic_documents WHERE generation=? AND state='stale'",
                (generation,),
            ).fetchone()[0]
            or 0
        ),
    }
    if any(blockers.values()):
        return {"ready": False, "blockers": blockers}
    revision = (
        int(current[0]) if current is not None else int(row["checkpoint_sequence"])
    )
    updated = conn.execute(
        """UPDATE source_semantic_generations
           SET state='ready',checkpoint_sequence=?,updated_at=?
           WHERE generation=? AND state='building'""",
        (revision, updated_at, generation),
    )
    if updated.rowcount != 1:
        raise ValueError("semantic_generation_state_changed")
    return {"ready": True, "blockers": blockers, "source_revision": revision}
