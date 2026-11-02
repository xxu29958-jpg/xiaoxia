"""Bounded, attributed notebook material for the existing shared-owner sender."""

import hashlib
import json
import math
import sqlite3
import time
from contextlib import closing
from datetime import datetime


class InterestMaterials:
    def __init__(self, notebook, policy, scope):
        self.notebook, self.policy, self.scope = notebook, policy, scope
        self.setting = "initiative-dismissed:" + scope

    def _dismissed(self):
        with closing(sqlite3.connect(self.notebook.db)) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (self.setting,)
            ).fetchone()
        values = json.loads(row[0]) if row else {}
        if not isinstance(values, dict) or any(
            not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in values.values()
        ):
            raise ValueError("Invalid discovery dismissal state")
        return values

    def candidates(self, now=None, *, include_shared=False):
        now = time.time() if now is None else now
        dismissed = self._dismissed()
        used = {
            item.get("material_key")
            for item in self.policy.data["deliveries"]
            if item["at"] > now - 30 * 86400
        }
        candidates, seen = [], set()
        for kind, rows in (
            ("note", self.notebook.notes(self.scope, "list").get("notes", [])),
            (
                "bookmark",
                self.notebook.bookmarks(self.scope, "list").get("bookmarks", []),
            ),
        ):
            for row in rows:
                try:
                    at = datetime.fromisoformat(row["updated_at"]).timestamp()
                except (KeyError, TypeError, ValueError):
                    continue
                key = hashlib.sha256(row["url"].encode()).hexdigest()[:24]
                if (
                    not now - 7 * 86400 <= at <= now + 60
                    or (key in used and not include_shared)
                    or key in seen
                    or dismissed.get(key, 0) > now - 30 * 86400
                ):
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "key": key,
                        "kind": kind,
                        "url": row["url"],
                        "title": row["title"][:160],
                        "source_quote": row.get("source_quote", "")[:600],
                        "personal_comment": row.get("comment", row.get("note", ""))[
                            :600
                        ],
                        "next_step": row.get("next_step", "")[:400],
                        "updated_at": row["updated_at"],
                    }
                )
        return sorted(candidates, key=lambda x: x["updated_at"], reverse=True)[:5]

    def current(self, material):
        return material in self.candidates()

    def dismiss(self, key):
        if key not in {item["key"] for item in self.candidates()}:
            raise ValueError("这条素材已经更新，请刷新后再操作。")
        now = time.time()
        values = {k: v for k, v in self._dismissed().items() if v > now - 30 * 86400}
        values[key] = now
        values = dict(sorted(values.items(), key=lambda x: x[1], reverse=True)[:100])
        with closing(sqlite3.connect(self.notebook.db)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                (self.setting, json.dumps(values)),
            )
