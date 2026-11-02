from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .source_semantic_authorization import _writable_generation
from .source_semantic_common import semantic_document_id


def remove_semantic_documents_for_source(
    conn: sqlite3.Connection,
    generation: str,
    source_id: str,
) -> int:
    """Remove projections whose anchor or dependency disappeared."""
    _writable_generation(conn, generation)
    cursor = conn.execute(
        """DELETE FROM source_semantic_documents
           WHERE generation=? AND (
             anchor_source_id=? OR document_id IN (
               SELECT document_id FROM source_semantic_dependencies WHERE source_id=?
             )
           )""",
        (generation, source_id, source_id),
    )
    return int(cursor.rowcount or 0)


def semantic_document_is_ready(
    conn: sqlite3.Connection,
    *,
    generation: str,
    view_kind: str,
    anchor_source_id: str,
    char_start: int,
    char_end: int,
    input_text: str,
    dependencies: Sequence[Mapping[str, Any]],
) -> bool:
    """Skip provider work only when the exact source projection already exists."""
    document_id = semantic_document_id(
        generation,
        anchor_source_id,
        view_kind,
        char_start,
        char_end,
    )
    input_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    row = conn.execute(
        """SELECT input_hash,state FROM source_semantic_documents
           WHERE document_id=? AND generation=?""",
        (document_id, generation),
    ).fetchone()
    if row is None or row["state"] != "ready" or row["input_hash"] != input_hash:
        return False
    stored = conn.execute(
        """SELECT source_id,source_version,char_start,char_end,role
           FROM source_semantic_dependencies WHERE document_id=? ORDER BY dependency_order""",
        (document_id,),
    ).fetchall()
    expected = [
        (
            item.get("source_id"),
            item.get("source_version"),
            item.get("char_start"),
            item.get("char_end"),
            item.get("role"),
        )
        for item in dependencies
    ]
    return [tuple(item) for item in stored] == expected


def prune_semantic_documents(
    conn: sqlite3.Connection,
    generation: str,
    anchor_source_id: str,
    keep_document_ids: Sequence[str],
) -> int:
    """Remove old or out-of-span projections after a source was fully rebuilt."""
    _writable_generation(conn, generation)
    keep = set(keep_document_ids)
    rows = conn.execute(
        """SELECT document_id FROM source_semantic_documents
           WHERE generation=? AND anchor_source_id=?""",
        (generation, anchor_source_id),
    ).fetchall()
    remove = [str(row[0]) for row in rows if str(row[0]) not in keep]
    if remove:
        conn.executemany(
            "DELETE FROM source_semantic_documents WHERE generation=? AND document_id=?",
            [(generation, document_id) for document_id in remove],
        )
    return len(remove)


def acknowledge_dirty(
    conn: sqlite3.Connection,
    generation: str,
    source_id: str,
    change_sequence: int,
) -> bool:
    """Acknowledge only the exact dirty version processed by a worker."""
    state = conn.execute(
        "SELECT state FROM source_semantic_generations WHERE generation=?",
        (generation,),
    ).fetchone()
    if state is None or state[0] not in {"building", "ready"}:
        return False
    pending_order_change = conn.execute(
        """SELECT 1 FROM source_semantic_order_changes
           WHERE generation=? AND source_id=? AND change_sequence<=? LIMIT 1""",
        (generation, source_id, change_sequence),
    ).fetchone()
    if pending_order_change is not None:
        return False
    cursor = conn.execute(
        "DELETE FROM source_semantic_dirty WHERE generation=? AND source_id=? AND change_sequence=?",
        (generation, source_id, change_sequence),
    )
    return cursor.rowcount == 1
