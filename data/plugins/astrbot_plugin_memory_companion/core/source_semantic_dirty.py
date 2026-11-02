from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from .source_semantic_authorization import _build_scope_matches, _writable_generation
from .source_semantic_common import _SOURCE_EVENT_SQL, _json_object, _trusted_timestamp


def _snapshot_position(snapshot: Mapping[str, Any]) -> tuple[str, str, str] | None:
    occurred_at = snapshot.get("occurred_at")
    if _trusted_timestamp(occurred_at) is None:
        return None
    return (
        str(occurred_at),
        str(snapshot.get("created_at") or ""),
        str(snapshot.get("source_id") or ""),
    )


def _snapshot_neighbors(
    conn: sqlite3.Connection, snapshot: Mapping[str, Any]
) -> list[sqlite3.Row]:
    position = _snapshot_position(snapshot)
    if position is None:
        return []
    owner_meta = {
        key: snapshot.get(key, "")
        for key in (
            "owner_bot_id",
            "platform",
            "persona_id",
            "participant_user_id",
        )
    }
    clauses = [
        f"t.event_type IN {_SOURCE_EVENT_SQL}",
        "t.scope=?",
        "t.session_id=?",
        "t.object_id=?",
        "t.id!=?",
    ]
    params: list[Any] = [
        snapshot.get("scope", ""),
        snapshot.get("session_id", ""),
        snapshot.get("object_id", ""),
        snapshot.get("source_id", ""),
    ]
    meta_expr = "CASE WHEN json_valid(t.metadata) THEN t.metadata ELSE '{}' END"
    for key, expected in owner_meta.items():
        clauses.append(f"COALESCE(json_extract({meta_expr},'$.{key}'),'')=?")
        params.append(expected or "")
    clauses.append("julianday(t.occurred_at) IS NOT NULL")
    base_where = " AND ".join(clauses)
    occurred_at, created_at, source_id = position
    neighbors: list[sqlite3.Row] = []
    for operator, direction in (("<", "DESC"), (">", "ASC")):
        row = conn.execute(
            f"""SELECT t.* FROM timeline t WHERE {base_where}
                AND (julianday(t.occurred_at),t.created_at,t.id)
                    {operator} (julianday(?),?,?)
                ORDER BY julianday(t.occurred_at) {direction},t.created_at {direction},t.id {direction}
                LIMIT 1""",
            (*params, occurred_at, created_at, source_id),
        ).fetchone()
        if row is not None and _trusted_timestamp(row["occurred_at"]) is not None:
            neighbors.append(row)
    return neighbors


def _dirty_values_from_row(
    generation: str,
    sequence: int,
    row: Mapping[str, Any],
) -> tuple[Any, ...]:
    row = dict(row)
    metadata = _json_object(row.get("metadata"))
    fields = (
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
    )
    return (
        generation,
        row["id"],
        sequence,
        "upsert",
        1,
        *fields,
        row["id"],
        1,
        *fields,
    )


def expand_semantic_order_changes(
    conn: sqlite3.Connection,
    generation: str,
    *,
    limit: int = 64,
) -> dict[str, Any]:
    """Invalidate and enqueue the nearest anchors around each old/new position."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
        raise ValueError("semantic_order_limit_invalid")
    generation_row = _writable_generation(conn, generation)
    build_scope = _json_object(generation_row["config_json"]).get("build_scope")
    changes = conn.execute(
        """SELECT * FROM source_semantic_order_changes
           WHERE generation=? ORDER BY change_sequence,source_id LIMIT ?""",
        (generation, limit),
    ).fetchall()
    affected: dict[str, sqlite3.Row] = {}
    changed_sources: set[str] = set()
    for change in changes:
        changed_sources.add(str(change["source_id"]))
        for present_key, snapshot_key in (
            ("old_present", "old_snapshot"),
            ("new_present", "new_snapshot"),
        ):
            if not change[present_key]:
                continue
            snapshot = _json_object(change[snapshot_key])
            snapshot_metadata = {
                key: snapshot.get(key, "")
                for key in (
                    "owner_bot_id",
                    "platform",
                    "persona_id",
                    "participant_user_id",
                )
            }
            if not _build_scope_matches(build_scope, snapshot, snapshot_metadata):
                continue
            for neighbor in _snapshot_neighbors(conn, snapshot):
                neighbor_metadata = _json_object(neighbor["metadata"])
                if _build_scope_matches(build_scope, dict(neighbor), neighbor_metadata):
                    affected[str(neighbor["id"])] = neighbor

    revision_row = conn.execute(
        "SELECT revision FROM source_semantic_revision WHERE singleton=1",
    ).fetchone()
    if revision_row is None:
        raise RuntimeError("source_semantic_schema_missing")
    current_sequence = int(revision_row[0])
    if affected:
        source_ids = sorted(affected)
        conn.executemany(
            """INSERT INTO source_semantic_dirty(
                generation,source_id,change_sequence,operation,old_present,
                old_scope,old_session_id,old_event_type,old_subject_id,old_object_id,
                old_owner_bot_id,old_bot_id,old_platform,old_persona_id,old_participant_user_id,
                old_occurred_at,old_created_at,new_source_id,new_present,new_scope,new_session_id,
                new_event_type,new_subject_id,new_object_id,new_owner_bot_id,new_bot_id,new_platform,
                new_persona_id,new_participant_user_id,new_occurred_at,new_created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(generation,source_id) DO UPDATE SET
                change_sequence=MAX(source_semantic_dirty.change_sequence,excluded.change_sequence),
                operation='upsert',new_source_id=excluded.new_source_id,new_present=1,
                new_scope=excluded.new_scope,new_session_id=excluded.new_session_id,
                new_event_type=excluded.new_event_type,new_subject_id=excluded.new_subject_id,
                new_object_id=excluded.new_object_id,new_owner_bot_id=excluded.new_owner_bot_id,
                new_bot_id=excluded.new_bot_id,new_platform=excluded.new_platform,
                new_persona_id=excluded.new_persona_id,new_participant_user_id=excluded.new_participant_user_id,
                new_occurred_at=excluded.new_occurred_at,new_created_at=excluded.new_created_at""",
            [
                _dirty_values_from_row(
                    generation, current_sequence, affected[source_id]
                )
                for source_id in source_ids
            ],
        )
        conn.executemany(
            """UPDATE source_semantic_documents SET state='stale',updated_at=datetime('now')
               WHERE generation=? AND view_kind='window' AND anchor_source_id=? AND state!='stale'""",
            [(generation, source_id) for source_id in source_ids],
        )
    for change in changes:
        conn.execute(
            """DELETE FROM source_semantic_order_changes
               WHERE generation=? AND change_sequence=? AND source_id=?""",
            (generation, change["change_sequence"], change["source_id"]),
        )
    return {
        "processed_changes": len(changes),
        "changed_source_ids": sorted(changed_sources),
        "affected_source_ids": sorted(affected),
        "change_sequence": current_sequence,
    }


def list_semantic_dirty(
    conn: sqlite3.Connection,
    generation: str,
    *,
    limit: int = 64,
) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
        raise ValueError("semantic_dirty_limit_invalid")
    generation_row = _writable_generation(conn, generation)
    build_scope = _json_object(generation_row["config_json"]).get("build_scope")
    rows = [
        dict(row)
        for row in conn.execute(
            """SELECT * FROM source_semantic_dirty WHERE generation=?
           ORDER BY change_sequence,source_id LIMIT ?""",
            (generation, limit),
        ).fetchall()
    ]
    for row in rows:
        scope_flags = {}
        for prefix in ("old", "new"):
            source = {
                "scope": row.get(f"{prefix}_scope", ""),
                "session_id": row.get(f"{prefix}_session_id", ""),
                "event_type": row.get(f"{prefix}_event_type", ""),
                "subject_id": row.get(f"{prefix}_subject_id", ""),
                "object_id": row.get(f"{prefix}_object_id", ""),
            }
            metadata = {
                "owner_bot_id": row.get(f"{prefix}_owner_bot_id", ""),
                "bot_id": row.get(f"{prefix}_bot_id", ""),
                "platform": row.get(f"{prefix}_platform", ""),
                "persona_id": row.get(f"{prefix}_persona_id", ""),
                "participant_user_id": row.get(f"{prefix}_participant_user_id", ""),
            }
            scope_flags[prefix] = bool(
                row[f"{prefix}_present"]
            ) and _build_scope_matches(
                build_scope,
                source,
                metadata,
            )
        row["old_build_scope_matches"] = scope_flags["old"]
        row["new_build_scope_matches"] = scope_flags["new"]
        row["build_scope_matches"] = scope_flags["old"] or scope_flags["new"]
    return rows
