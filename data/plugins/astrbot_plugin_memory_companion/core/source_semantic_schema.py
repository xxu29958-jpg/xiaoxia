"""Semantic projection schema; the caller owns the SQLite connection."""

import sqlite3
from pathlib import Path

from .source_semantic_common import (
    _SOURCE_EVENT_SQL,
    _TRACKED_GENERATION_STATES_SQL,
    _source_snapshot,
)


def initialize(conn: sqlite3.Connection) -> None:
    """Create projection tables and change triggers on the caller connection."""
    source = Path(__file__).with_name("sql") / "source_semantic_schema.sql"
    sql = source.read_text(encoding="utf-8")
    # Replace only named schema constants; SQLite JSON paths also contain '$'.
    for name, value in {
        "SOURCE_EVENT_TYPES": _SOURCE_EVENT_SQL,
        "TRACKED_GENERATION_STATES": _TRACKED_GENERATION_STATES_SQL,
        "NEW_SOURCE_SNAPSHOT": _source_snapshot("new"),
        "OLD_SOURCE_SNAPSHOT": _source_snapshot("old"),
    }.items():
        sql = sql.replace("${" + name + "}", value)
    conn.executescript(sql)
