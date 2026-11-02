from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .source_evidence import message_source_version
from .source_semantic_authorization import (
    _authorization_result,
    _build_scope_matches,
    _writable_generation,
)
from .source_semantic_common import (
    _json_object,
    _source_partition,
    _source_position,
    _trusted_timestamp,
    semantic_document_id,
)
from .source_semantic_dirty import _snapshot_neighbors


def capture_semantic_fence(conn: sqlite3.Connection, generation: str) -> dict[str, Any]:
    generation_row = _writable_generation(conn, generation)
    revision = conn.execute(
        "SELECT revision FROM source_semantic_revision WHERE singleton=1",
    ).fetchone()
    if revision is None:
        raise RuntimeError("source_semantic_schema_missing")
    return {
        "generation": generation,
        "config_hash": generation_row["config_hash"],
        "source_revision": int(revision[0]),
    }


def _current_dependency_rows(
    conn: sqlite3.Connection,
    *,
    generation_config: Mapping[str, Any],
    dependencies: Sequence[Mapping[str, Any]],
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
    project_text: Callable[[str, dict[str, Any]], str] | None = None,
) -> list[tuple[int, str, str, int, int, str, dict[str, Any]]]:
    checked: list[tuple[int, str, str, int, int, str, dict[str, Any]]] = []
    seen: set[str] = set()
    partition: tuple[str, ...] | None = None
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, Mapping):
            raise ValueError("semantic_dependency_invalid")
        source_id = dependency.get("source_id")
        source_version = dependency.get("source_version")
        start, end = dependency.get("char_start"), dependency.get("char_end")
        role = dependency.get("role")
        if (
            not isinstance(source_id, str)
            or not source_id
            or source_id in seen
            or not isinstance(source_version, str)
            or not source_version
            or isinstance(start, bool)
            or not isinstance(start, int)
            or start < 0
            or isinstance(end, bool)
            or not isinstance(end, int)
            or end < start
            or role not in {"anchor", "context"}
        ):
            raise ValueError("semantic_dependency_invalid")
        seen.add(source_id)
        raw = conn.execute(
            "SELECT *,julianday(occurred_at) AS source_sort_time FROM timeline WHERE id=?",
            (source_id,),
        ).fetchone()
        if raw is None or raw["event_type"] not in {"user_message", "bot_response"}:
            raise ValueError("semantic_dependency_unavailable")
        row = dict(raw)
        metadata = _json_object(row.get("metadata"))
        if not _build_scope_matches(
            generation_config.get("build_scope"), row, metadata
        ) or not _authorization_result(authorize_source, row, metadata):
            raise ValueError("semantic_dependency_unauthorized")
        if message_source_version(row) != source_version:
            raise ValueError("semantic_dependency_version_changed")
        content = row.get("content")
        if not isinstance(content, str) or end > len(content):
            raise ValueError("semantic_dependency_span_invalid")
        text = content if project_text is None else project_text(content, row)
        if not isinstance(text, str):
            raise ValueError("semantic_source_text_unavailable")
        source = {
            "source_id": row["id"],
            "source_version": source_version,
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
        current_partition = _source_partition(source)
        if partition is None:
            partition = current_partition
        elif current_partition != partition:
            raise ValueError("semantic_window_partition_mismatch")
        checked.append((index, source_id, source_version, start, end, role, source))
    return checked


def validate_semantic_snapshot(
    conn: sqlite3.Connection,
    *,
    generation: str,
    expected_config_hash: str,
    source_change_sequence: int,
    view_kind: str,
    anchor_source_id: str,
    anchor_source_version: str,
    char_start: int,
    char_end: int,
    input_text: str,
    dependencies: Sequence[Mapping[str, Any]],
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
    project_text: Callable[[str, dict[str, Any]], str] | None = None,
) -> list[tuple[int, str, str, int, int, str, dict[str, Any]]]:
    """Recheck generation, global source revision, every owner, span, and window edge."""
    if view_kind not in {"fragment", "window"}:
        raise ValueError("semantic_view_kind_invalid")
    if (
        not isinstance(expected_config_hash, str)
        or not expected_config_hash
        or isinstance(source_change_sequence, bool)
        or not isinstance(source_change_sequence, int)
        or source_change_sequence < 0
        or isinstance(char_start, bool)
        or not isinstance(char_start, int)
        or char_start < 0
        or isinstance(char_end, bool)
        or not isinstance(char_end, int)
        or char_end < char_start
    ):
        raise ValueError("semantic_snapshot_fence_invalid")
    generation_row = _writable_generation(conn, generation)
    if generation_row["config_hash"] != expected_config_hash:
        raise ValueError("semantic_generation_changed")
    revision = conn.execute(
        "SELECT revision FROM source_semantic_revision WHERE singleton=1",
    ).fetchone()
    if revision is None or int(revision[0]) != source_change_sequence:
        raise ValueError("semantic_source_revision_changed")
    if not isinstance(input_text, str) or not dependencies:
        raise ValueError("semantic_document_input_invalid")
    config = _json_object(generation_row["config_json"])
    checked = _current_dependency_rows(
        conn,
        generation_config=config,
        dependencies=dependencies,
        authorize_source=authorize_source,
        project_text=project_text,
    )
    anchors = [item for item in checked if item[5] == "anchor"]
    if len(anchors) != 1 or anchors[0][1:5] != (
        anchor_source_id,
        anchor_source_version,
        char_start,
        char_end,
    ):
        raise ValueError("semantic_anchor_dependency_mismatch")
    if view_kind == "fragment" and len(checked) != 1:
        raise ValueError("semantic_fragment_dependency_invalid")
    if view_kind == "window":
        anchor = anchors[0][6]
        anchor_time = _trusted_timestamp(anchor.get("occurred_at"))
        if anchor_time is None:
            raise ValueError("semantic_window_time_untrusted")
        context_rows = [item for item in checked if item[5] == "context"]
        if len(context_rows) > 2:
            raise ValueError("semantic_window_neighbor_limit")
        anchor_snapshot = {
            "source_id": anchor["source_id"],
            "scope": anchor["scope"],
            "session_id": anchor["session_id"],
            "object_id": anchor["object_id"],
            "occurred_at": anchor["occurred_at"],
            "created_at": anchor["created_at"],
            **{
                key: anchor["metadata"].get(key, "")
                for key in (
                    "owner_bot_id",
                    "platform",
                    "persona_id",
                    "participant_user_id",
                )
            },
        }
        neighbor_ids = {
            str(row["id"]) for row in _snapshot_neighbors(conn, anchor_snapshot)
        }
        if any(item[1] not in neighbor_ids for item in context_rows):
            raise ValueError("semantic_window_adjacency_changed")
        sorted_ids = [
            item[1]
            for item in sorted(
                checked,
                key=lambda item: _source_position(item[6]),
            )
        ]
        if sorted_ids != [item[1] for item in checked]:
            raise ValueError("semantic_window_dependency_order_invalid")
    rendered = "\n".join(item[6]["text"][item[3] : item[4]] for item in checked)
    if input_text != rendered:
        raise ValueError("semantic_input_dependency_mismatch")
    return checked


def store_semantic_document(
    conn: sqlite3.Connection,
    *,
    generation: str,
    view_kind: str,
    anchor_source_id: str,
    anchor_source_version: str,
    char_start: int,
    char_end: int,
    input_text: str,
    source_change_sequence: int,
    dependencies: Sequence[Mapping[str, Any]],
    expected_config_hash: str,
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
    project_text: Callable[[str, dict[str, Any]], str] | None = None,
    vector: bytes | None = None,
    vector_dimension: int | None = None,
    updated_at: str,
) -> str:
    """Persist only a freshly authorized, version-bound text-free projection."""
    if view_kind not in {"fragment", "window"}:
        raise ValueError("semantic_view_kind_invalid")
    if (
        not isinstance(anchor_source_id, str)
        or not anchor_source_id
        or not isinstance(anchor_source_version, str)
        or not anchor_source_version
        or isinstance(char_start, bool)
        or not isinstance(char_start, int)
        or char_start < 0
        or isinstance(char_end, bool)
        or not isinstance(char_end, int)
        or char_end < char_start
    ):
        raise ValueError("semantic_anchor_invalid")
    if (
        not isinstance(input_text, str)
        or not dependencies
        or isinstance(source_change_sequence, bool)
        or not isinstance(source_change_sequence, int)
        or source_change_sequence < 0
    ):
        raise ValueError("semantic_document_input_invalid")
    if vector is not None and (
        not isinstance(vector, bytes)
        or isinstance(vector_dimension, bool)
        or not isinstance(vector_dimension, int)
        or vector_dimension < 1
    ):
        raise ValueError("semantic_vector_invalid")
    if vector is None and vector_dimension is not None:
        raise ValueError("semantic_vector_invalid")
    generation_row = _writable_generation(conn, generation)
    generation_config = _json_object(generation_row["config_json"])
    if vector is not None and vector_dimension != generation_config.get("dimensions"):
        raise ValueError("semantic_vector_dimension_mismatch")
    checked = validate_semantic_snapshot(
        conn,
        generation=generation,
        expected_config_hash=expected_config_hash,
        source_change_sequence=source_change_sequence,
        view_kind=view_kind,
        anchor_source_id=anchor_source_id,
        anchor_source_version=anchor_source_version,
        char_start=char_start,
        char_end=char_end,
        input_text=input_text,
        dependencies=dependencies,
        authorize_source=authorize_source,
        project_text=project_text,
    )

    document_id = semantic_document_id(
        generation, anchor_source_id, view_kind, char_start, char_end
    )
    input_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT INTO source_semantic_documents
            (document_id,generation,view_kind,anchor_source_id,anchor_source_version,char_start,char_end,
             input_hash,source_change_sequence,vector_blob,vector_dimension,state,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(document_id) DO UPDATE SET
                anchor_source_version=excluded.anchor_source_version,char_start=excluded.char_start,
                char_end=excluded.char_end,input_hash=excluded.input_hash,
                source_change_sequence=excluded.source_change_sequence,vector_blob=excluded.vector_blob,
                vector_dimension=excluded.vector_dimension,state=excluded.state,updated_at=excluded.updated_at""",
        (
            document_id,
            generation,
            view_kind,
            anchor_source_id,
            anchor_source_version,
            char_start,
            char_end,
            input_hash,
            source_change_sequence,
            vector,
            vector_dimension,
            "ready" if vector is not None else "pending",
            updated_at,
        ),
    )
    conn.execute(
        "DELETE FROM source_semantic_dependencies WHERE document_id=?", (document_id,)
    )
    conn.executemany(
        """INSERT INTO source_semantic_dependencies
            (document_id,dependency_order,source_id,source_version,char_start,char_end,role)
            VALUES (?,?,?,?,?,?,?)""",
        [(document_id, *dependency[:6]) for dependency in checked],
    )
    return document_id
