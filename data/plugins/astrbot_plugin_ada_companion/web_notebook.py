"""Local, scoped bookmarks and explicitly attributed browsing notes."""

import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import reading_store
from .reading_evidence import encoded, note_provenance


class BackpackError(ValueError):
    """A bounded browsing or notebook operation could not be completed."""


class WebNotebook:
    def __init__(self, folder: Path):
        self.folder = folder / "web_backpack"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = self.folder / "notebook.sqlite3"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS visits (
                    id TEXT PRIMARY KEY, scope TEXT, url TEXT, title TEXT,
                    text TEXT, screenshot TEXT, visited_at REAL);
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id TEXT PRIMARY KEY, scope TEXT, url TEXT, title TEXT,
                    note TEXT, created_at TEXT, updated_at TEXT, UNIQUE(scope, url));
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, scope TEXT, url TEXT, title TEXT, kind TEXT,
                    source_quote TEXT, comment TEXT, next_step TEXT,
                    created_at TEXT, updated_at TEXT, UNIQUE(scope, url, kind));
                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY, scope TEXT, at REAL, action TEXT,
                    url TEXT, result TEXT, bytes INTEGER DEFAULT 0);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(visits)")}
            if "images" not in columns:
                conn.execute(
                    "ALTER TABLE visits ADD COLUMN images TEXT NOT NULL DEFAULT '[]'"
                )
            reading_store.initialize(conn, self.db)

    def enabled(self) -> bool:
        with closing(sqlite3.connect(self.db)) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='enabled'"
            ).fetchone()
        return not row or row[0] == "true"

    def set_enabled(self, enabled: bool):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings VALUES ('enabled', ?)",
                (json.dumps(enabled),),
            )

    def record(self, scope: str, action: str, url: str, result: str, byte_count=0):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT INTO operations(scope,at,action,url,result,bytes) VALUES(?,?,?,?,?,?)",
                (scope, time.time(), action, url[:2000], result[:120], byte_count),
            )
            conn.execute(
                "DELETE FROM operations WHERE id NOT IN (SELECT id FROM operations ORDER BY id DESC LIMIT 3000)"
            )

    def usage(self, scope: str) -> dict:
        # A rolling window cannot be reset by restarting the bot or crossing midnight.
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = list(
                conn.execute(
                    "SELECT * FROM operations WHERE scope=? AND at>? ORDER BY id DESC",
                    (scope, time.time() - 86400),
                )
            )
        return {
            "actions_24h": sum(r["result"] == "started" for r in rows),
            "pages_24h": sum(
                r["result"] == "started" and r["action"] == "open" for r in rows
            ),
            "bytes_24h": sum(r["bytes"] for r in rows),
            "recent": [
                {k: r[k] for k in ("at", "action", "url", "result")} for r in rows[:10]
            ],
        }

    def save_visit(self, scope: str, data: dict, screenshot: Path | str):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                reading = (
                    reading_store.capture(conn, scope, data)
                    if data.get("ok", True)
                    else {"source_kind": "access_error", "coverage": "none"}
                )
            except ValueError as exc:
                raise BackpackError(str(exc)) from exc
            conn.execute(
                "INSERT OR REPLACE INTO visits(id,scope,url,title,text,screenshot,visited_at,images,reading) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    data["page_id"],
                    scope,
                    data["url"],
                    data["title"],
                    data["text"],
                    str(screenshot),
                    time.time(),
                    json.dumps(data.get("images", []), ensure_ascii=False),
                    encoded(reading),
                ),
            )
            self._prune_visits(conn, scope)
        data["reading"] = reading
        data.pop("_source_text", None)

    def read(self, scope: str, **kwargs) -> dict:
        """Continue an immutable captured source, retaining its original provenance."""
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = reading_store.read(conn, scope, **kwargs)
            except ValueError as exc:
                raise BackpackError(str(exc)) from exc
            conn.execute(
                "INSERT INTO visits(id,scope,url,title,text,screenshot,visited_at,images,reading) VALUES(?,?,?,?,?,'',?,'[]',?)",
                (
                    result["page_id"],
                    scope,
                    result["url"],
                    result["title"],
                    result["text"],
                    time.time(),
                    encoded(result["reading"]),
                ),
            )
            self._prune_visits(conn, scope)
        return result

    def _prune_visits(self, conn, scope):
        old = conn.execute(
            "SELECT id,screenshot FROM visits WHERE scope=? ORDER BY visited_at DESC LIMIT -1 OFFSET 30",
            (scope,),
        ).fetchall()
        for item_id, path in old:
            candidate = Path(path)
            if candidate.parent == self.folder and candidate.suffix == ".jpg":
                candidate.unlink(missing_ok=True)
            conn.execute("DELETE FROM visits WHERE id=? AND scope=?", (item_id, scope))

    def visit(self, scope: str, page_id: str) -> dict:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM visits WHERE id=? AND scope=?", (page_id, scope)
            ).fetchone()
        if not row:
            raise BackpackError(
                "Page is not in this user's browsing history. Open it first."
            )
        result = dict(row)
        result["images"] = json.loads(result["images"])
        result["reading"] = json.loads(result["reading"])
        return result

    def bookmarks(
        self, scope: str, action: str, page_id="", note="", query="", item_id=""
    ) -> dict:
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.row_factory = sqlite3.Row
            if action == "save":
                conn.execute("BEGIN IMMEDIATE")
                page = self.visit(scope, page_id)
                self._require_source(conn, scope, page)
                if page["reading"].get("coverage") == "none":
                    raise BackpackError(
                        "An access error is not a successfully read source."
                    )
                if not note.strip() or len(note) > 240:
                    raise BackpackError(
                        "Supply one short bookmark note, at most 240 characters."
                    )
                existing = conn.execute(
                    "SELECT id FROM bookmarks WHERE scope=? AND url=?",
                    (scope, page["url"]),
                ).fetchone()
                if (
                    not existing
                    and conn.execute(
                        "SELECT count(*) FROM bookmarks WHERE scope=?", (scope,)
                    ).fetchone()[0]
                    >= 500
                ):
                    raise BackpackError("The 500-bookmark limit is reached.")
                item_id = existing[0] if existing else uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO bookmarks(id,scope,url,title,note,created_at,updated_at,reading) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(scope,url) DO UPDATE SET title=excluded.title,note=excluded.note,updated_at=excluded.updated_at,reading=excluded.reading""",
                    (
                        item_id,
                        scope,
                        page["url"],
                        page["title"],
                        note.strip(),
                        now,
                        now,
                        encoded(note_provenance(page, "")),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM bookmarks WHERE id=? AND scope=?", (item_id, scope)
                ).fetchone()
                return {
                    "ok": True,
                    "duplicate": bool(existing),
                    "bookmark": self._public_record(row),
                }
            if action == "delete":
                count = conn.execute(
                    "DELETE FROM bookmarks WHERE id=? AND scope=?", (item_id, scope)
                ).rowcount
                return {"ok": bool(count), "deleted": count}
            if action != "list":
                raise BackpackError("Unknown bookmark action.")
            rows = conn.execute(
                """SELECT * FROM bookmarks WHERE scope=? AND
                (instr(lower(title),lower(?))>0 OR instr(lower(url),lower(?))>0 OR instr(lower(note),lower(?))>0)
                ORDER BY updated_at DESC LIMIT 50""",
                (scope, query[:200], query[:200], query[:200]),
            ).fetchall()
            return {
                "ok": True,
                "bookmarks": [self._public_record(r) for r in rows],
            }

    def notes(
        self,
        scope: str,
        action: str,
        page_id="",
        kind="interesting",
        source_quote="",
        comment="",
        next_step="",
        query="",
        item_id="",
        *,
        only_if_new=False,
    ) -> dict:
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.row_factory = sqlite3.Row
            if action == "save":
                conn.execute("BEGIN IMMEDIATE")
                page = self.visit(scope, page_id)
                self._require_source(conn, scope, page)
                if page["reading"].get("coverage") == "none":
                    raise BackpackError(
                        "An access error is not a successfully read source."
                    )
                if (
                    only_if_new
                    and conn.execute(
                        "SELECT 1 FROM notes WHERE scope=? AND url=?",
                        (scope, page["url"]),
                    ).fetchone()
                ):
                    return {"ok": False, "reason": "existing_note_preserved"}
                self._validate_note(page, kind, source_quote, comment, next_step)
                existing = conn.execute(
                    "SELECT id FROM notes WHERE scope=? AND url=? AND kind=?",
                    (scope, page["url"], kind),
                ).fetchone()
                if (
                    not existing
                    and conn.execute(
                        "SELECT count(*) FROM notes WHERE scope=?", (scope,)
                    ).fetchone()[0]
                    >= 500
                ):
                    raise BackpackError("The 500-note limit is reached.")
                item_id = existing[0] if existing else uuid.uuid4().hex
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    """INSERT INTO notes(id,scope,url,title,kind,source_quote,comment,next_step,created_at,updated_at,reading) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,url,kind)
                    DO UPDATE SET title=excluded.title,source_quote=excluded.source_quote,comment=excluded.comment,next_step=excluded.next_step,updated_at=excluded.updated_at,reading=excluded.reading""",
                    (
                        item_id,
                        scope,
                        page["url"],
                        page["title"],
                        kind,
                        source_quote,
                        comment.strip(),
                        next_step.strip(),
                        now,
                        now,
                        encoded(note_provenance(page, source_quote)),
                    ),
                )
                return {
                    "ok": True,
                    "id": item_id,
                    "source_url": page["url"],
                    "reading": note_provenance(page, source_quote),
                    "meaning": "Web source and personal commentary are separate; this is not shared life memory.",
                }
            if action == "delete":
                count = conn.execute(
                    "DELETE FROM notes WHERE id=? AND scope=?", (item_id, scope)
                ).rowcount
                return {"ok": bool(count), "deleted": count}
            if action != "list":
                raise BackpackError("Unknown notebook action.")
            rows = conn.execute(
                """SELECT * FROM notes WHERE scope=? AND
                (instr(lower(title),lower(?))>0 OR instr(lower(comment),lower(?))>0 OR instr(lower(next_step),lower(?))>0)
                ORDER BY updated_at DESC LIMIT 50""",
                (scope, query[:200], query[:200], query[:200]),
            ).fetchall()
            return {
                "ok": True,
                "notes": [self._public_record(r) for r in rows],
            }

    @staticmethod
    def _validate_note(page, kind, source_quote, comment, next_step):
        if kind not in ("interesting", "unfinished", "next"):
            raise BackpackError("Note kind must be interesting, unfinished or next.")
        if len(source_quote) > 600 or len(comment) > 600 or len(next_step) > 400:
            raise BackpackError("Keep the note short.")
        if source_quote and source_quote not in page["text"]:
            raise BackpackError(
                "The source quote is not present in the page text actually read."
            )
        if not (source_quote or comment.strip() or next_step.strip()):
            raise BackpackError("An empty note cannot be saved.")

    @staticmethod
    def _require_source(conn, scope, page):
        source_id = page["reading"].get("source_id")
        if (
            source_id
            and not conn.execute(
                "SELECT 1 FROM reading_sources WHERE scope=? AND id=?",
                (scope, source_id),
            ).fetchone()
        ):
            raise BackpackError(
                "The unpinned source cache expired; reopen it before saving a note."
            )

    @staticmethod
    def _public_record(row):
        result = {key: row[key] for key in row.keys() if key != "scope"}
        result["reading"] = json.loads(result["reading"]) or {
            "source_kind": "legacy_unknown",
            "coverage": "unknown",
        }
        return result

    def export(self, scope: str) -> Path:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("BEGIN")
            conn.row_factory = sqlite3.Row
            data = {"version": 2, "exported_at": datetime.now(UTC).isoformat()}
            for table in ("bookmarks", "notes"):
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE scope=?", (scope,)
                ).fetchall()
                data[table] = [self._public_record(r) for r in rows]
            data["reading_evidence"] = reading_store.export(conn, scope)
        target = self.folder / f"backpack-{scope}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
        return target
