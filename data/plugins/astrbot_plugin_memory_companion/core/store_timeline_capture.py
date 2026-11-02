from __future__ import annotations

import asyncio
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    new_id,
    stable_fingerprint,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value
from .store_support import (
    _timeline_order_key,
    _timeline_time_sql,
)


class MemoryStoreTimelineCapture:
    """store timeline capture; the facade owns all shared resources."""

    async def add_timeline_event(
        self,
        *,
        event_type: str,
        session_id: str,
        scope: str,
        subject_id: str,
        object_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        occurred_at: str = "",
    ) -> str:
        return await asyncio.to_thread(
            self._guard_operation_sync,
            self._add_timeline_event_sync,
            event_type,
            session_id,
            scope,
            subject_id,
            object_id,
            content,
            metadata or {},
            occurred_at,
        )

    def _add_timeline_event_sync(
        self,
        event_type: str,
        session_id: str,
        scope: str,
        subject_id: str,
        object_id: str,
        content: str,
        metadata: dict[str, Any],
        occurred_at: str,
    ) -> str:
        now = utc_now()
        content = redact_sensitive_text(clean_text(content, 4000))
        metadata = redact_sensitive_value(metadata)
        row_id = new_id("tl")
        event_type = clean_text(event_type, 80)
        session_id = clean_text(session_id, 200)
        subject_id = clean_text(subject_id, 120)
        message_id = clean_text(metadata.get("message_id"), 120)
        dedupe_key = (
            stable_fingerprint(
                "timeline", event_type, session_id, subject_id, message_id
            )
            if message_id
            else ""
        )
        with self._lock:
            with self._transaction_sync():
                from .message_sources import suppressed

                if suppressed(
                    self._conn,
                    source_id=row_id,
                    event_type=event_type,
                    scope=clean_text(scope, 40),
                    session_id=session_id,
                    subject_id=subject_id,
                    object_id=clean_text(object_id, 120),
                    message_id=message_id,
                    metadata=metadata,
                ):
                    return ""
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO timeline(
                        id, event_type, session_id, scope, subject_id, object_id,
                        content, metadata, message_id, dedupe_key,
                        occurred_at, created_at, summarized_at,
                        retention_class, import_batch_id, source_sequence
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?, '',?,?,?)
                    """,
                    (
                        row_id,
                        event_type,
                        session_id,
                        clean_text(scope, 40),
                        subject_id,
                        clean_text(object_id, 120),
                        clean_text(content, 4000),
                        json_dumps(metadata),
                        message_id,
                        dedupe_key,
                        occurred_at or now,
                        now,
                        clean_text(metadata.get("retention_class"), 40) or "normal",
                        clean_text(metadata.get("import_batch_id"), 120),
                        max(0, int(metadata.get("source_sequence") or 0)),
                    ),
                )
                if cur.rowcount == 0 and dedupe_key:
                    existing = self._conn.execute(
                        "SELECT id FROM timeline WHERE dedupe_key=?",
                        (dedupe_key,),
                    ).fetchone()
                    if existing:
                        return clean_text(existing["id"], 120)
                # A governance tombstone can suppress an insert. Never return
                # the uncommitted random ID as a successful capture receipt.
                if cur.rowcount == 0:
                    return ""
        return row_id

    async def add_historical_timeline_events(
        self, rows: list[dict[str, Any]]
    ) -> dict[str, str]:
        inserted, _ = await asyncio.to_thread(
            self._add_historical_timeline_events_sync, rows
        )
        return inserted

    async def add_historical_timeline_events_with_status(
        self,
        rows: list[dict[str, Any]],
    ) -> tuple[dict[str, str], set[str]]:
        return await asyncio.to_thread(self._add_historical_timeline_events_sync, rows)

    def _add_historical_timeline_events_sync(
        self,
        rows: list[dict[str, Any]],
    ) -> tuple[dict[str, str], set[str]]:
        now = utc_now()
        inserted: dict[str, str] = {}
        newly_inserted: set[str] = set()
        with self._lock:
            with self._transaction_sync():
                for raw in rows:
                    if not isinstance(raw, dict):
                        continue
                    event_type = clean_text(raw.get("event_type"), 80)
                    session_id = clean_text(raw.get("session_id"), 200)
                    subject_id = clean_text(raw.get("subject_id"), 120)
                    message_id = clean_text(raw.get("message_id"), 120)
                    if (
                        not event_type
                        or not session_id
                        or not subject_id
                        or not message_id
                    ):
                        continue
                    dedupe_key = stable_fingerprint(
                        "timeline", event_type, session_id, subject_id, message_id
                    )
                    row_id = clean_text(raw.get("id"), 120) or new_id("tl")
                    metadata = (
                        raw.get("metadata")
                        if isinstance(raw.get("metadata"), dict)
                        else {}
                    )
                    from .message_sources import suppressed

                    if suppressed(
                        self._conn,
                        source_id=row_id,
                        event_type=event_type,
                        scope=clean_text(raw.get("scope"), 40),
                        session_id=session_id,
                        subject_id=subject_id,
                        object_id=clean_text(raw.get("object_id"), 120),
                        message_id=message_id,
                        metadata=redact_sensitive_value(metadata),
                    ):
                        continue
                    cursor = self._conn.execute(
                        """
                        INSERT OR IGNORE INTO timeline(
                            id, event_type, session_id, scope, subject_id, object_id,
                            content, metadata, message_id, dedupe_key,
                            occurred_at, created_at, summarized_at,
                            retention_class, import_batch_id, source_sequence
                        )
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?, '',?,?,?)
                        """,
                        (
                            row_id,
                            event_type,
                            session_id,
                            clean_text(raw.get("scope"), 40),
                            subject_id,
                            clean_text(raw.get("object_id"), 120),
                            clean_text(redact_sensitive_text(raw.get("content")), 4000),
                            json_dumps(redact_sensitive_value(metadata)),
                            message_id,
                            dedupe_key,
                            clean_text(raw.get("occurred_at"), 80) or now,
                            now,
                            clean_text(raw.get("retention_class"), 40)
                            or "historical_archive",
                            clean_text(raw.get("import_batch_id"), 120),
                            max(0, int(raw.get("source_sequence") or 0)),
                        ),
                    )
                    if cursor.rowcount > 0:
                        newly_inserted.add(message_id)
                    existing = self._conn.execute(
                        "SELECT id FROM timeline WHERE dedupe_key=?",
                        (dedupe_key,),
                    ).fetchone()
                    if existing:
                        inserted[message_id] = clean_text(existing["id"], 120)
        return inserted, newly_inserted

    async def recent_timeline(
        self,
        limit: int = 10,
        scope: str = "",
        session_id: str = "",
        entity_id: str = "",
        *,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._guard_operation_sync,
            self._recent_timeline_sync,
            limit,
            scope,
            session_id,
            entity_id,
            offset,
        )

    def _recent_timeline_sync(
        self,
        limit: int,
        scope: str,
        session_id: str,
        entity_id: str,
        offset: int,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, int(limit))
        safe_offset = max(0, int(offset))
        if scope and session_id and entity_id:
            return self._recent_timeline_entity_split_sync(
                safe_limit, scope, session_id, entity_id, safe_offset
            )
        params: list[Any] = []
        where = "1=1"
        if scope:
            where += " AND scope=?"
            params.append(scope)
        if session_id:
            where += " AND session_id=?"
            params.append(session_id)
        if entity_id:
            where += " AND (subject_id=? OR object_id=?)"
            params.extend([entity_id, entity_id])
        event_time_sql = _timeline_time_sql()
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM timeline
                WHERE {where}
                ORDER BY {event_time_sql} DESC, created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                params + [safe_limit, safe_offset],
            ).fetchall()
        return [dict(row) for row in rows]

    def _recent_timeline_entity_split_sync(
        self,
        limit: int,
        scope: str,
        session_id: str,
        entity_id: str,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Read the subject/object branches through dedicated indexes and merge.

        ``(subject_id=? OR object_id=?)`` forces a scan plus temp B-tree on
        large sessions. Two index-driven branch reads keep the same rows and
        ordering: any row in the merged top-(offset+limit) must be in its own
        branch's top-(offset+limit), so bounded branch reads are exact.
        """
        branch_size = limit + offset
        merged: dict[str, dict[str, Any]] = {}
        event_time_sql = _timeline_time_sql()
        with self._lock:
            for column in ("subject_id", "object_id"):
                rows = self._conn.execute(
                    f"""
                    SELECT * FROM timeline
                    WHERE scope=? AND session_id=? AND {column}=?
                    ORDER BY {event_time_sql} DESC, created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (scope, session_id, entity_id, branch_size),
                ).fetchall()
                for row in rows:
                    merged.setdefault(str(row["id"]), dict(row))
        ordered = sorted(
            merged.values(),
            key=_timeline_order_key,
            reverse=True,
        )
        return ordered[offset : offset + limit]

    async def recent_cross_window_timeline(
        self,
        *,
        source_scope: str,
        current_session_id: str,
        since_at: str,
        limit: int = 48,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._recent_cross_window_timeline_sync,
            source_scope,
            current_session_id,
            since_at,
            limit,
        )

    def _recent_cross_window_timeline_sync(
        self,
        source_scope: str,
        current_session_id: str,
        since_at: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        scope = clean_text(source_scope, 40)
        session_id = clean_text(current_session_id, 200)
        cutoff = clean_text(since_at, 80)
        if scope not in {"private", "group"} or not session_id or not cutoff:
            return []
        event_time_sql = _timeline_time_sql()
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM timeline
                WHERE scope=?
                  AND session_id!=?
                  AND {event_time_sql} >= julianday(?)
                  AND event_type IN ('user_message', 'bot_response')
                ORDER BY {event_time_sql} DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                (scope, session_id, cutoff, max(1, min(240, int(limit or 48)))),
            ).fetchall()
        return [dict(row) for row in rows]

    async def timeline_window(
        self,
        *,
        start_at: str,
        end_at: str,
        limit: int = 30,
        scope: str = "",
        session_id: str = "",
        entity_id: str = "",
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._timeline_window_sync,
            start_at,
            end_at,
            limit,
            scope,
            session_id,
            entity_id,
        )

    def _timeline_window_sync(
        self,
        start_at: str,
        end_at: str,
        limit: int,
        scope: str,
        session_id: str,
        entity_id: str,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [clean_text(start_at, 80), clean_text(end_at, 80)]
        event_time_sql = _timeline_time_sql()
        where = f"{event_time_sql} >= julianday(?) AND {event_time_sql} < julianday(?)"
        if scope:
            where += " AND scope=?"
            params.append(clean_text(scope, 40))
        if session_id:
            where += " AND session_id=?"
            params.append(clean_text(session_id, 200))
        if entity_id:
            where += " AND (subject_id=? OR object_id=?)"
            params.extend([clean_text(entity_id, 120), clean_text(entity_id, 120)])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM timeline
                WHERE {where}
                ORDER BY {event_time_sql} DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                params + [max(1, int(limit or 1))],
            ).fetchall()
        return [dict(row) for row in rows]
