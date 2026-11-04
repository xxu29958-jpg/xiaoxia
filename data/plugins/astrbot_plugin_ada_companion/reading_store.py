"""Reading evidence belongs to the existing notebook database and transactions."""

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from .reading_evidence import (
    MAX_EXCERPT_CHARS,
    encoded,
    excerpt,
    provenance,
    source_record,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_sources (
    scope TEXT NOT NULL, id TEXT NOT NULL, record TEXT NOT NULL,
    last_accessed REAL NOT NULL, PRIMARY KEY(scope,id)
);
CREATE TABLE IF NOT EXISTS reading_ranges (
    scope TEXT NOT NULL, source_id TEXT NOT NULL,
    start INTEGER NOT NULL, end INTEGER NOT NULL, provided_at REAL NOT NULL,
    PRIMARY KEY(scope,source_id,start,end)
);
"""


def initialize(conn, path):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)")}
    if "reading" not in columns:
        populated = any(
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            for table in ("notes", "bookmarks", "visits")
        )
        if populated:
            target = Path(path).with_name(
                "notebook.before-reading-v1-" + uuid.uuid4().hex + ".sqlite3"
            )
            with closing(sqlite3.connect(target)) as backup:
                conn.backup(backup)
    conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
    with conn:
        for table in ("notes", "bookmarks", "visits"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "reading" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN reading TEXT NOT NULL DEFAULT '{{}}'"
                )


def capture(conn, scope, data):
    record = source_record(data)
    existing = conn.execute(
        "SELECT record FROM reading_sources WHERE scope=? AND id=?",
        (scope, record["id"]),
    ).fetchone()
    if existing:
        record = json.loads(existing[0])
    else:
        conn.execute(
            "INSERT INTO reading_sources VALUES(?,?,?,?)",
            (scope, record["id"], encoded(record), time.time()),
        )
    start = data.get("reading_offset", 0)
    text = data["text"]
    if (
        type(start) is not int
        or start < 0
        or start > record["captured_chars"]
        or not isinstance(text, str)
        or len(text) > MAX_EXCERPT_CHARS
        or record["text"][start : start + len(text)] != text
    ):
        raise ValueError("Presented text does not match the captured source.")
    view = provided(conn, scope, record, start, start + len(text))
    prune(conn, scope)
    return view


def provided(conn, scope, record, start, end):
    ranges = conn.execute(
        "SELECT start,end,provided_at FROM reading_ranges WHERE scope=? AND source_id=? AND end>=? AND start<=?",
        (scope, record["id"], start, end),
    ).fetchall()
    merged_start = min([start, *(r[0] for r in ranges)])
    merged_end = max([end, *(r[1] for r in ranges)])
    if start < end:
        conn.execute(
            "DELETE FROM reading_ranges WHERE scope=? AND source_id=? AND end>=? AND start<=?",
            (scope, record["id"], start, end),
        )
        conn.execute(
            "INSERT INTO reading_ranges VALUES(?,?,?,?,?)",
            (
                scope,
                record["id"],
                merged_start,
                merged_end,
                min([time.time(), *(r[2] for r in ranges)]),
            ),
        )
    conn.execute(
        "UPDATE reading_sources SET last_accessed=? WHERE scope=? AND id=?",
        (time.time(), scope, record["id"]),
    )
    return {
        **provenance(record, start, end),
        "resume_offset": resume_offset(conn, scope, record),
    }


def resume_offset(conn, scope, record):
    offset = 0
    for start, end in conn.execute(
        "SELECT start,end FROM reading_ranges WHERE scope=? AND source_id=? ORDER BY start",
        (scope, record["id"]),
    ):
        if start > offset:
            break
        offset = max(offset, end)
    return offset


def read(conn, scope, *, source_id="", page_id="", item_id="", offset=-1, limit=4000):
    if not source_id:
        table, key = ("notes", item_id) if item_id else ("visits", page_id)
        row = conn.execute(
            f"SELECT reading FROM {table} WHERE scope=? AND id=?", (scope, key)
        ).fetchone()
        source_id = json.loads(row[0]).get("source_id", "") if row else ""
    row = conn.execute(
        "SELECT record FROM reading_sources WHERE scope=? AND id=?", (scope, source_id)
    ).fetchone()
    if not row:
        raise ValueError(
            "Source is unavailable in this scope; reopen it to establish a new reading version."
        )
    record = json.loads(row[0])
    if type(offset) is int and offset == -1:
        offset = resume_offset(conn, scope, record)
    text, start, end = excerpt(record, offset, limit)
    reading = provided(conn, scope, record, start, end)
    return {
        "ok": True,
        "page_id": uuid.uuid4().hex,
        "url": record["url"],
        "title": record["title"],
        "text": text,
        "reading": reading,
        "cached_source": True,
        "instruction": "Use next_offset to continue this same frozen source. Open its URL separately to check a newer version.",
    }


def prune(conn, scope):
    pinned = {
        json.loads(row[0]).get("source_id")
        for table in ("notes", "bookmarks")
        for row in conn.execute(f"SELECT reading FROM {table} WHERE scope=?", (scope,))
    }
    recent = conn.execute(
        "SELECT id FROM reading_sources WHERE scope=? ORDER BY last_accessed DESC,id",
        (scope,),
    ).fetchall()
    unused = [row[0] for row in recent if row[0] not in pinned]
    for source_id in unused[30:]:
        conn.execute(
            "DELETE FROM reading_ranges WHERE scope=? AND source_id=?",
            (scope, source_id),
        )
        conn.execute(
            "DELETE FROM reading_sources WHERE scope=? AND id=?", (scope, source_id)
        )


def export(conn, scope):
    sources = [
        json.loads(row[0])
        for row in conn.execute(
            "SELECT record FROM reading_sources WHERE scope=?", (scope,)
        )
    ]
    ranges = [
        {key: row[key] for key in ("source_id", "start", "end", "provided_at")}
        for row in conn.execute("SELECT * FROM reading_ranges WHERE scope=?", (scope,))
    ]
    return {"sources": sources, "provided_ranges": ranges}
