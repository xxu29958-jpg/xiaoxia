from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from .source_evidence import message_source_version
from .source_semantic_authorization import (
    _authorization_result,
    _build_scope_matches,
    _build_scope_sql,
    _writable_generation,
)
from .source_semantic_common import _SOURCE_EVENT_SQL, _canonical_json, _json_object
from .source_semantic_dirty import _snapshot_neighbors


def scan_generation_sources(
    conn: sqlite3.Connection,
    generation: str,
    *,
    limit: int = 64,
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
    updated_at: str,
) -> dict[str, Any]:
    """Advance a stable-ID historical scan; timeline changes remain in dirty queues."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 128:
        raise ValueError("semantic_scan_limit_invalid")
    generation_row = _writable_generation(conn, generation)
    if generation_row["state"] != "building":
        raise ValueError("semantic_generation_scan_closed")
    try:
        cursor_state = json.loads(generation_row["scan_cursor"] or "{}")
    except (TypeError, ValueError):
        raise ValueError("semantic_scan_cursor_invalid")
    if not isinstance(cursor_state, dict):
        raise ValueError("semantic_scan_cursor_invalid")
    after_id = cursor_state.get("id", "")
    if not isinstance(after_id, str):
        raise ValueError("semantic_scan_cursor_invalid")
    if cursor_state.get("complete") is True:
        return {
            "sources": [],
            "next_cursor": after_id,
            "has_more": False,
            "done": True,
            "base_sequence": int(generation_row["base_sequence"]),
            "checkpoint_sequence": int(generation_row["checkpoint_sequence"]),
        }

    build_scope = _json_object(generation_row["config_json"]).get("build_scope")
    _build_scope_matches(build_scope, {}, {})
    base_sequence = int(generation_row["base_sequence"])
    scope_where, scope_params = _build_scope_sql(build_scope)
    rows = conn.execute(
        f"""SELECT t.id,t.event_type,t.session_id,t.scope,t.subject_id,t.object_id,
                   t.content,t.metadata,t.occurred_at,t.created_at,
                   julianday(t.occurred_at) AS source_sort_time
            FROM timeline t
            WHERE t.id>? AND t.event_type IN {_SOURCE_EVENT_SQL} AND {scope_where}
            ORDER BY t.id LIMIT ?""",
        (after_id, *scope_params, limit + 1),
    )
    sources: list[dict[str, Any]] = []
    next_id = after_id
    scanned = 0
    has_more = False
    for raw in rows:
        if scanned >= limit:
            has_more = True
            break
        row = dict(raw)
        scanned += 1
        next_id = str(row["id"])
        metadata = _json_object(row.get("metadata"))
        if not _build_scope_matches(build_scope, row, metadata):
            continue
        if not _authorization_result(authorize_source, row, metadata):
            continue
        source = {
            "source_id": row["id"],
            "source_version": message_source_version(row),
            "scope": row["scope"],
            "session_id": row["session_id"],
            "event_type": row["event_type"],
            "subject_id": row["subject_id"],
            "object_id": row["object_id"],
            "occurred_at": row["occurred_at"],
            "created_at": row["created_at"],
            "source_sort_time": row["source_sort_time"],
            "metadata": metadata,
        }
        sources.append(source)
        conn.execute(
            """INSERT OR IGNORE INTO source_semantic_dirty(
                 generation,source_id,change_sequence,operation,old_present,
                 new_source_id,new_present,new_scope,new_session_id,new_event_type,
                 new_subject_id,new_object_id,new_owner_bot_id,new_bot_id,new_platform,
                 new_persona_id,new_participant_user_id,new_occurred_at,new_created_at
               ) VALUES (?,?,?,'upsert',0,?,1,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                generation,
                row["id"],
                base_sequence,
                row["id"],
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
    done = not has_more
    next_cursor = _canonical_json({"id": next_id, "complete": done})
    checkpoint = conn.execute(
        """UPDATE source_semantic_generations
           SET scan_cursor=?,checkpoint_sequence=?,updated_at=?
           WHERE generation=? AND state='building' AND scan_cursor=?""",
        (
            next_cursor,
            base_sequence,
            updated_at,
            generation,
            generation_row["scan_cursor"],
        ),
    )
    if checkpoint.rowcount != 1:
        raise ValueError("semantic_scan_checkpoint_changed")
    return {
        "sources": sources,
        "next_cursor": next_id,
        "has_more": has_more,
        "done": done,
        "base_sequence": base_sequence,
        "checkpoint_sequence": base_sequence,
    }


def read_semantic_source(
    conn: sqlite3.Connection,
    generation: str,
    source_id: str,
    expected_version: str,
    *,
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
    project_text: Callable[[str, dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """Read full text only after rechecking generation scope, owner, and version."""
    generation_row = _writable_generation(conn, generation)
    row = conn.execute(
        "SELECT *,julianday(occurred_at) AS source_sort_time FROM timeline WHERE id=?",
        (source_id,),
    ).fetchone()
    if row is None or row["event_type"] not in {"user_message", "bot_response"}:
        raise ValueError("semantic_source_unavailable")
    row = dict(row)
    metadata = _json_object(row.get("metadata"))
    build_scope = _json_object(generation_row["config_json"]).get("build_scope")
    if not _build_scope_matches(
        build_scope, row, metadata
    ) or not _authorization_result(authorize_source, row, metadata):
        raise ValueError("semantic_source_unauthorized")
    actual_version = message_source_version(row)
    if not expected_version or actual_version != expected_version:
        raise ValueError("semantic_source_version_changed")
    if not isinstance(row.get("content"), str):
        raise ValueError("semantic_source_text_unavailable")
    text = (
        project_text(row["content"], row)
        if project_text is not None
        else row["content"]
    )
    if not isinstance(text, str):
        raise ValueError("semantic_source_text_unavailable")
    return {
        "source_id": row["id"],
        "source_version": actual_version,
        "scope": row["scope"],
        "session_id": row["session_id"],
        "event_type": row["event_type"],
        "subject_id": row["subject_id"],
        "object_id": row["object_id"],
        "occurred_at": row["occurred_at"],
        "created_at": row["created_at"],
        "source_sort_time": row["source_sort_time"],
        "metadata": metadata,
        "text": text,
    }


def read_semantic_neighbors(
    conn: sqlite3.Connection,
    generation: str,
    anchor: Mapping[str, Any],
    *,
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
    project_text: Callable[[str, dict[str, Any]], str] | None = None,
) -> list[dict[str, Any]]:
    """Read only currently adjacent, authorized source rows in the same partition."""
    _writable_generation(conn, generation)
    snapshot = dict(anchor)
    metadata = (
        snapshot.get("metadata")
        if isinstance(snapshot.get("metadata"), Mapping)
        else {}
    )
    snapshot.update(metadata)
    neighbors = _snapshot_neighbors(conn, snapshot)
    results = []
    for row in neighbors:
        source_id = str(row["id"])
        try:
            results.append(
                read_semantic_source(
                    conn,
                    generation,
                    source_id,
                    message_source_version(dict(row)),
                    authorize_source=authorize_source,
                    project_text=project_text,
                )
            )
        except ValueError as exc:
            if str(exc) in {
                "semantic_source_unauthorized",
                "semantic_source_unavailable",
            }:
                continue
            raise
    return results
