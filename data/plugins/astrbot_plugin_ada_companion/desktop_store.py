"""Persistent desktop switch and bounded operation metadata, without typed content."""

import sqlite3
import time
from contextlib import closing


class DesktopStore:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
                INSERT OR IGNORE INTO settings VALUES (1,1);
                CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, at REAL, action TEXT, outcome TEXT);
            """)

    def status(self):
        with closing(sqlite3.connect(self.path)) as db:
            return {
                "enabled": bool(
                    db.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0]
                ),
                "actions_24h": db.execute(
                    "SELECT COUNT(*) FROM actions WHERE at>? AND action!='view'",
                    (time.time() - 86400,),
                ).fetchone()[0],
                "recent": [
                    dict(zip(("at", "action", "outcome"), row))
                    for row in db.execute(
                        "SELECT at,action,outcome FROM actions ORDER BY id DESC LIMIT 12"
                    )
                ],
            }

    def enable(self, enabled):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE settings SET enabled=? WHERE id=1", (enabled,))

    def record(self, action, outcome):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO actions(at,action,outcome) VALUES (?,?,?)",
                (time.time(), action, outcome),
            )
            db.execute("DELETE FROM actions WHERE at<?", (time.time() - 7 * 86400,))
