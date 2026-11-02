from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    new_id,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value
from .store_support import (
    _memory_time_sql,
)


class MemoryStoreConversationThreads:
    """store conversation threads; the facade owns all shared resources."""

    async def create_cross_window_thread(
        self,
        *,
        from_session: str,
        to_session: str,
        topic: str,
        content: str,
        visibility: str = "shareable",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._create_cross_window_thread_sync,
            from_session,
            to_session,
            topic,
            content,
            visibility,
            metadata or {},
        )

    def _create_cross_window_thread_sync(
        self,
        from_session: str,
        to_session: str,
        topic: str,
        content: str,
        visibility: str,
        metadata: dict[str, Any],
    ) -> str:
        now = utc_now()
        row_id = new_id("thread")
        topic = redact_sensitive_text(topic)
        content = redact_sensitive_text(content)
        metadata = redact_sensitive_value(metadata)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cross_window_threads(
                    id, status, from_session, to_session, topic, content,
                    visibility, metadata, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row_id,
                    "open",
                    clean_text(from_session, 200),
                    clean_text(to_session, 200),
                    clean_text(topic, 200),
                    clean_text(content, 4000),
                    clean_text(visibility, 40),
                    json_dumps(metadata),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return row_id

    async def list_cross_window_threads(
        self,
        status: str = "open",
        limit: int = 20,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_cross_window_threads_sync, status, limit, session_id
        )

    def _list_cross_window_threads_sync(
        self,
        status: str,
        limit: int,
        session_id: str,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "1=1"
        if status and status != "all":
            where += " AND status=?"
            params.append(status)
        if session_id:
            where += " AND (from_session=? OR to_session=?)"
            params.extend([session_id, session_id])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM cross_window_threads
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params + [max(1, int(limit))],
            ).fetchall()
        return [dict(row) for row in rows]

    async def update_cross_window_thread_status(
        self, thread_id: str, status: str
    ) -> bool:
        return await asyncio.to_thread(
            self._update_cross_window_thread_status_sync, thread_id, status
        )

    def _update_cross_window_thread_status_sync(
        self, thread_id: str, status: str
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE cross_window_threads SET status=?, updated_at=? WHERE id=?",
                (clean_text(status, 40), utc_now(), clean_text(thread_id, 120)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def add_injection_log(
        self,
        *,
        session_id: str,
        scope: str,
        query: str,
        selected_memory_ids: list[str],
        blocked_reasons: list[dict[str, Any]],
        injection_chars: int,
    ) -> str:
        return await asyncio.to_thread(
            self._add_injection_log_sync,
            session_id,
            scope,
            query,
            selected_memory_ids,
            blocked_reasons,
            injection_chars,
        )

    def _add_injection_log_sync(
        self,
        session_id: str,
        scope: str,
        query: str,
        selected_memory_ids: list[str],
        blocked_reasons: list[dict[str, Any]],
        injection_chars: int,
    ) -> str:
        row_id = new_id("inj")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO injection_logs(
                    id, session_id, scope, query, selected_memory_ids,
                    blocked_reasons, injection_chars, created_at
                )
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    row_id,
                    clean_text(session_id, 200),
                    clean_text(scope, 40),
                    clean_text(redact_sensitive_text(query), 1000),
                    json_dumps(selected_memory_ids),
                    json_dumps(redact_sensitive_value(blocked_reasons[:30])),
                    max(0, int(injection_chars or 0)),
                    utc_now(),
                ),
            )
            self._conn.commit()
        return row_id

    async def recent_injection_logs(
        self,
        limit: int = 10,
        scope: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._recent_injection_logs_sync, limit, scope, session_id
        )

    def _recent_injection_logs_sync(
        self, limit: int, scope: str, session_id: str
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "1=1"
        if scope:
            where += " AND scope=?"
            params.append(scope)
        if session_id:
            where += " AND session_id=?"
            params.append(session_id)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM injection_logs
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params + [max(1, int(limit))],
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["selected_memory_ids"] = json_loads(
                item.get("selected_memory_ids"), []
            )
            item["blocked_reasons"] = json_loads(item.get("blocked_reasons"), [])
            result.append(item)
        return result

    async def list_schedule_context_memories(
        self,
        *,
        session_id: str = "",
        user_id: str = "",
        bot_id: str = "",
        limit: int = 36,
        strict_session_only: bool = False,
    ) -> list[MemoryRecord]:
        """Read a small schedule-focused snapshot without waiting on the shared writer lock."""
        return await asyncio.to_thread(
            self._list_schedule_context_memories_sync,
            session_id,
            user_id,
            bot_id,
            limit,
            strict_session_only,
        )

    def _list_schedule_context_memories_sync(
        self,
        session_id: str,
        user_id: str,
        bot_id: str,
        limit: int,
        strict_session_only: bool,
    ) -> list[MemoryRecord]:
        session_id = clean_text(session_id, 200)
        user_id = clean_text(user_id, 120)
        bot_id = clean_text(bot_id, 120)
        if not self.db_path.is_file():
            return []
        bot_types = (
            "schedule_fragment",
            "persona_life",
            "self_action",
            "proactive_message",
            "reading_memory",
            "creative_work",
            "search_action",
            "image_action",
            "qzone_action",
            "companion_note",
        )
        profile_types = (
            "user_profile",
            "user_preference",
            "relationship_claim",
        )
        bot_placeholders = ",".join("?" for _ in bot_types)
        profile_placeholders = ",".join("?" for _ in profile_types)
        clauses = [
            """
            (
                visibility='bot_self'
                AND memory_type IN (BOT_TYPE_PLACEHOLDERS)
                AND (
                    scope!='private'
                    OR session_id=''
                    OR session_id=?
                    OR (? != '' AND (subject_id=? OR object_id=?))
                )
                AND (
                    CASE
                        WHEN json_valid(metadata)
                        THEN COALESCE(CAST(json_extract(metadata, '$.owner_bot_id') AS TEXT), '')
                        ELSE ''
                    END
                ) IN ('', 'self', ?)
                AND (subject_kind!='bot' OR subject_id IN ('', 'self', ?))
                AND (object_kind!='bot' OR object_id IN ('', 'self', ?))
            )
            """.replace("BOT_TYPE_PLACEHOLDERS", bot_placeholders)
        ]
        params: list[Any] = [
            *bot_types,
            session_id,
            user_id,
            user_id,
            user_id,
            bot_id,
            bot_id,
            bot_id,
        ]
        if user_id:
            clauses.append(
                f"""
                (
                    scope='private'
                    AND visibility IN ('private_pair', 'shareable')
                    AND memory_type IN ({profile_placeholders})
                    AND (session_id=? OR subject_id=? OR object_id=?)
                    AND (
                        CASE
                            WHEN json_valid(metadata)
                            THEN COALESCE(CAST(json_extract(metadata, '$.owner_bot_id') AS TEXT), '')
                            ELSE ''
                        END
                    ) IN ('', 'self', ?)
                    AND (subject_kind!='bot' OR subject_id IN ('', 'self', ?))
                    AND (object_kind!='bot' OR object_id IN ('', 'self', ?))
                )
                """
            )
            params.extend(
                [*profile_types, session_id, user_id, user_id, bot_id, bot_id, bot_id]
            )
        # This is the dedicated Companion schedule context path; Bot Personal
        # records remain available here and are not part of ordinary recall.
        where = (
            "lifecycle!='archived' AND review_status!='pending' AND ("
            + " OR ".join(clauses)
            + ")"
        )
        if strict_session_only:
            if not session_id:
                return []
            where += " AND session_id=?"
            params.append(session_id)
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        with closing(
            sqlite3.connect(uri, uri=True, timeout=0.35, check_same_thread=False)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA busy_timeout=350")
            type_count = max(1, len(bot_types) + (len(profile_types) if user_id else 0))
            per_type_limit = max(
                2, min(12, (max(1, int(limit or 36)) + type_count - 1) // type_count)
            )
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        memories.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY memory_type
                            ORDER BY
                                {_memory_time_sql(include_updated=True)} DESC,
                                importance DESC
                        ) AS fast_type_rank
                    FROM memories
                    WHERE {where}
                )
                SELECT *
                FROM ranked
                WHERE fast_type_rank<=?
                ORDER BY
                    {_memory_time_sql(include_updated=True)} DESC,
                    importance DESC
                """,
                [*params, per_type_limit],
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows[:120]]
