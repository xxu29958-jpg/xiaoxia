from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value
from .source_query import SourceQuery, SourceQueryError, source_partition
from .store_support import (
    _timeline_time_sql,
)


class MemoryStoreSourceQueries:
    """store source queries; the facade owns all shared resources."""

    async def source_revision(self) -> str:
        return await self._run_recoverable_database_operation(
            self._source_revision_sync
        )

    async def query_progress_revisions(self) -> tuple[str, str]:
        """One bounded metadata read for progress authorization/version checks."""
        return await self._run_recoverable_database_operation(
            self._query_progress_revisions_sync
        )

    def _query_progress_revisions_sync(self) -> tuple[str, str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT s.revision, m.revision FROM source_query_revision s "
                "CROSS JOIN retrieval_revision m WHERE s.singleton=1 AND m.singleton=1"
            ).fetchone()
        if row is None:
            raise SourceQueryError("source_index_unavailable")
        return str(row[0]), str(row[1])

    def _source_revision_sync(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT revision FROM source_query_revision WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise SourceQueryError("source_index_unavailable")
            return str(row[0])

    async def query_source_page(
        self,
        ctx: Any,
        query: SourceQuery,
        *,
        limit: int,
        boundary: list[Any] | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._query_source_page_sync,
            ctx,
            query,
            limit,
            boundary,
            expected_revision,
        )

    def _query_source_page_sync(
        self,
        ctx: Any,
        query: SourceQuery,
        limit: int,
        boundary: list[Any] | None,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        partition, owner_params = source_partition(ctx)
        limit = max(1, min(int(limit), 12))
        conn, lock = self._read_connection_for_bundle()
        with lock:
            cutoff = time.monotonic() + 2.0
            conn.set_progress_handler(lambda: int(time.monotonic() > cutoff), 2000)
            try:
                conn.execute("BEGIN")
                revision_row = conn.execute(
                    "SELECT revision FROM source_query_revision WHERE singleton=1"
                ).fetchone()
                revision = str(revision_row[0]) if revision_row else ""
                if not revision or (
                    expected_revision is not None and expected_revision != revision
                ):
                    raise SourceQueryError("cursor_invalid")
                clauses = [partition]
                if query.action != "read":
                    clauses.append("julianday(t.occurred_at) IS NOT NULL")
                params: list[Any] = list(owner_params)
                if query.start_at:
                    clauses.append("julianday(t.occurred_at)>=julianday(?)")
                    params.append(query.start_at)
                if query.end_at:
                    clauses.append("julianday(t.occurred_at)<julianday(?)")
                    params.append(query.end_at)
                path = "time_range"
                if query.terms:
                    path = "partition_substring"
                    if self._source_fts_enabled and all(
                        len(term) >= 3 for term in query.terms
                    ):
                        clauses.append(
                            "t.rowid IN (SELECT rowid FROM timeline_source_fts WHERE timeline_source_fts MATCH ?)"
                        )
                        params.append(
                            " OR ".join(
                                '"' + term.replace('"', '""') + '"'
                                for term in query.terms
                            )
                        )
                        path = "fts5_trigram"
                    clauses.append(
                        "("
                        + " OR ".join(
                            "instr(lower(t.content),lower(?))>0" for _ in query.terms
                        )
                        + ")"
                    )
                    params.extend(query.terms)
                where = " AND ".join(clauses)
                order_columns = "julianday(t.occurred_at),t.created_at,t.id"
                projection = "t.*,julianday(t.occurred_at) AS source_sort_time"
                read_count = 0

                def key(row: dict[str, Any]) -> list[Any]:
                    return [row["source_sort_time"], row["created_at"], row["id"]]

                def select(
                    edge: list[Any] | None, *, ascending: bool, count: int
                ) -> tuple[list[dict[str, Any]], bool]:
                    nonlocal read_count
                    edge_clause = ""
                    edge_params: list[Any] = []
                    if edge:
                        edge_clause = f" AND ({order_columns}) {'>' if ascending else '<'} (?,?,?)"
                        if query.action == "context":
                            # The inclusive scalar bound enables the time index; the tuple remains exact.
                            edge_clause = (
                                f" AND julianday(t.occurred_at) {'>=' if ascending else '<='} ?"
                                + edge_clause
                            )
                            edge_params.append(edge[0])
                    sort = "ASC" if ascending else "DESC"
                    order = ",".join(
                        column + " " + sort for column in order_columns.split(",")
                    )
                    found = [
                        dict(row)
                        for row in conn.execute(
                            f"SELECT {projection} FROM timeline t WHERE {where}{edge_clause} ORDER BY {order} LIMIT ?",
                            params
                            + edge_params
                            + (list(edge) if edge else [])
                            + [count + 1],
                        ).fetchall()
                    ]
                    read_count += len(found)
                    return found[:count], len(found) > count

                continuations: dict[str, list[Any]] = {}
                if query.action in {"context", "read"}:
                    path = "source_" + query.action
                    anchor = conn.execute(
                        f"SELECT {projection} FROM timeline t WHERE {where} AND t.id=?",
                        params + [query.source_ref[len("timeline:") :]],
                    ).fetchone()
                    if anchor is None:
                        raise SourceQueryError("source_unavailable")
                    read_count += 1
                    anchor_key = key(dict(anchor))
                    if query.action == "read":
                        rows = [dict(anchor)]
                    elif query.direction == "around":
                        before_count = (limit - 1) // 2
                        after_count = limit - 1 - before_count
                        before, more_before = select(
                            anchor_key, ascending=False, count=before_count
                        )
                        after, more_after = select(
                            anchor_key, ascending=True, count=after_count
                        )
                        rows = [*reversed(before), dict(anchor), *after]
                        if more_before:
                            continuations["before"] = (
                                key(before[-1]) if before else anchor_key
                            )
                        if more_after:
                            continuations["after"] = (
                                key(after[-1]) if after else anchor_key
                            )
                    else:
                        ascending = query.direction == "after"
                        rows, more = select(
                            boundary or anchor_key, ascending=ascending, count=limit
                        )
                        if more and rows:
                            continuations[query.direction] = key(rows[-1])
                        if not ascending:
                            rows.reverse()
                else:
                    rows, more = select(boundary, ascending=False, count=limit)
                    if more and rows:
                        continuations["next"] = key(rows[-1])
                return {
                    "rows": rows,
                    "revision": revision,
                    "continuations": continuations,
                    "path": path,
                    "read_count": read_count,
                }
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise SourceQueryError("source_query_timeout") from exc
                raise
            finally:
                conn.set_progress_handler(None, 0)
                if conn.in_transaction:
                    conn.rollback()

    async def query_source_range_batch(
        self,
        ctx: Any,
        *,
        start_at: str,
        end_at: str,
        limit: int,
        boundary: list[Any] | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._query_source_range_batch_sync,
            ctx,
            start_at,
            end_at,
            limit,
            boundary,
            expected_revision,
        )

    def _query_source_range_batch_sync(
        self,
        ctx: Any,
        start_at: str,
        end_at: str,
        limit: int,
        boundary: list[Any] | None,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        partition, params = source_partition(ctx)
        limit = max(1, min(256, int(limit)))
        conn, lock = self._read_connection_for_bundle()
        with lock:
            cutoff = time.monotonic() + 2.0
            conn.set_progress_handler(lambda: int(time.monotonic() > cutoff), 2000)
            try:
                conn.execute("BEGIN")
                revision_row = conn.execute(
                    "SELECT revision FROM source_query_revision WHERE singleton=1"
                ).fetchone()
                revision = str(revision_row[0]) if revision_row else ""
                if not revision or (
                    expected_revision is not None and expected_revision != revision
                ):
                    raise SourceQueryError("cursor_invalid")
                where = (
                    partition
                    + " AND julianday(t.occurred_at)>=julianday(?) AND julianday(t.occurred_at)<julianday(?)"
                )
                params.extend([start_at, end_at])
                if boundary is not None:
                    # The cursor identifies the next unread row, including a partially delivered body.
                    where += " AND julianday(t.occurred_at)>=? AND (julianday(t.occurred_at),t.created_at,t.id)>=(?,?,?)"
                    params.extend([boundary[0], *boundary])
                projection = (
                    "t.id,t.event_type,t.scope,t.session_id,t.subject_id,t.object_id,t.occurred_at,t.created_at,"
                    "CASE WHEN length(t.content)<=262144 THEN t.content ELSE NULL END AS content,"
                    "CASE WHEN length(t.metadata)<=65536 THEN t.metadata ELSE NULL END AS metadata,"
                    "length(t.content) AS source_content_chars,length(t.metadata) AS source_metadata_chars,"
                    "julianday(t.occurred_at) AS source_sort_time"
                )
                rows = [
                    dict(row)
                    for row in conn.execute(
                        f"SELECT {projection} FROM timeline t WHERE {where} "
                        "ORDER BY julianday(t.occurred_at),t.created_at,t.id LIMIT ?",
                        params + [limit + 1],
                    ).fetchall()
                ]
                lookahead = rows[limit] if len(rows) > limit else None
                return {
                    "rows": rows[:limit],
                    "revision": revision,
                    "read_count": len(rows),
                    "eof": lookahead is None,
                    "next_boundary": (
                        [
                            lookahead["source_sort_time"],
                            lookahead["created_at"],
                            lookahead["id"],
                        ]
                        if lookahead is not None
                        else None
                    ),
                }
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise SourceQueryError("source_query_timeout") from exc
                raise
            finally:
                conn.set_progress_handler(None, 0)
                if conn.in_transaction:
                    conn.rollback()

    async def get_timeline_by_ids(
        self, event_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        return await self._run_recoverable_database_operation(
            self._get_timeline_by_ids_sync, event_ids
        )

    def _get_timeline_by_ids_sync(
        self, event_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        ids = list(
            dict.fromkeys(
                clean_text(event_id, 160)
                for event_id in event_ids
                if clean_text(event_id, 160)
            )
        )
        if not ids:
            return {}
        rows: list[Any] = []
        with self._lock:
            for index in range(0, len(ids), 500):
                chunk = ids[index : index + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    self._conn.execute(
                        f"SELECT * FROM timeline WHERE id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                )
        return {clean_text(row["id"], 160): dict(row) for row in rows}

    async def unsummarized_timeline_window(
        self,
        *,
        session_id: str,
        scope: str = "",
        limit: int = 40,
        after_timeline_id: str = "",
        exclude_assigned: bool = False,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._unsummarized_timeline_window_sync,
            session_id,
            scope,
            limit,
            after_timeline_id,
            exclude_assigned,
        )

    def _unsummarized_timeline_window_sync(
        self,
        session_id: str,
        scope: str,
        limit: int,
        after_timeline_id: str = "",
        exclude_assigned: bool = False,
    ) -> dict[str, Any]:
        params: list[Any] = [clean_text(session_id, 200)]
        where = "session_id=? AND summarized_at=''"
        if exclude_assigned:
            where += " AND NOT EXISTS (SELECT 1 FROM summary_batch_events e WHERE e.event_id=timeline.id)"
        if scope:
            where += " AND scope=?"
            params.append(clean_text(scope, 40))
        event_time_sql = _timeline_time_sql()
        with self._lock:
            cursor = None
            if clean_text(after_timeline_id, 160):
                cursor = self._conn.execute(
                    "SELECT occurred_at, created_at, id FROM timeline WHERE id=? AND session_id=?",
                    (clean_text(after_timeline_id, 160), clean_text(session_id, 200)),
                ).fetchone()
            if cursor:
                where += (
                    f" AND ({event_time_sql}, created_at, id) > "
                    "(COALESCE(julianday(NULLIF(?, '')), julianday(NULLIF(?, '')), 0), ?, ?)"
                )
                params.extend(
                    [
                        clean_text(cursor["occurred_at"], 80),
                        clean_text(cursor["created_at"], 80),
                        clean_text(cursor["created_at"], 80),
                        clean_text(cursor["id"], 160),
                    ]
                )
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM timeline WHERE {where}",
                params,
            ).fetchone()[0]
            first = self._conn.execute(
                f"""
                SELECT occurred_at
                FROM timeline
                WHERE {where}
                ORDER BY {event_time_sql} ASC, created_at ASC, id ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM timeline
                WHERE {where}
                ORDER BY {event_time_sql} ASC, created_at ASC, id ASC
                LIMIT ?
                """,
                params + [max(1, int(limit))],
            ).fetchall()
        return {
            "total": int(total or 0),
            "first_occurred_at": first["occurred_at"] if first else "",
            "rows": [dict(row) for row in rows],
        }

    async def mark_timeline_summarized(self, event_ids: list[str]) -> int:
        return await asyncio.to_thread(self._mark_timeline_summarized_sync, event_ids)

    def _mark_timeline_summarized_sync(
        self, event_ids: list[str], _commit: bool = True
    ) -> int:
        ids = [
            clean_text(event_id, 120)
            for event_id in event_ids
            if clean_text(event_id, 120)
        ]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE timeline SET summarized_at=? WHERE id IN ({placeholders})",
                [utc_now()] + ids,
            )
            # _commit=False 用于批量导入：由外层事务统一提交，避免逐次 fsync。
            if _commit:
                self._conn.commit()
        return int(cur.rowcount or 0)

    async def get_summary_failure(self, session_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_summary_failure_sync, session_id)

    def _get_summary_failure_sync(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM summary_failures WHERE session_id=?",
                (clean_text(session_id, 200),),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = json_loads(item.get("metadata"), {})
        return item

    async def record_summary_failure(
        self,
        *,
        session_id: str,
        scope: str,
        start_timeline_id: str,
        end_timeline_id: str,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._record_summary_failure_sync,
            session_id,
            scope,
            start_timeline_id,
            end_timeline_id,
            error,
            metadata or {},
        )

    def _record_summary_failure_sync(
        self,
        session_id: str,
        scope: str,
        start_timeline_id: str,
        end_timeline_id: str,
        error: str,
        metadata: dict[str, Any],
    ) -> int:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO summary_failures(
                    session_id, scope, start_timeline_id, end_timeline_id,
                    retry_count, last_error, metadata, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    scope=excluded.scope,
                    start_timeline_id=excluded.start_timeline_id,
                    end_timeline_id=excluded.end_timeline_id,
                    retry_count=summary_failures.retry_count + 1,
                    last_error=excluded.last_error,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    clean_text(session_id, 200),
                    clean_text(scope, 40),
                    clean_text(start_timeline_id, 120),
                    clean_text(end_timeline_id, 120),
                    clean_text(redact_sensitive_text(error), 1000),
                    json_dumps(redact_sensitive_value(metadata)),
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT retry_count FROM summary_failures WHERE session_id=?",
                (clean_text(session_id, 200),),
            ).fetchone()
            self._conn.commit()
        return int(row["retry_count"] if row else 1)

    async def clear_summary_failure(self, session_id: str) -> bool:
        return await asyncio.to_thread(self._clear_summary_failure_sync, session_id)

    async def mark_summary_failure_dead_letter(
        self, session_id: str, max_retries: int
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_summary_failure_dead_letter_sync,
            session_id,
            max_retries,
        )

    async def mark_summary_failure_cooldown(
        self,
        session_id: str,
        max_retries: int,
        cooldown_seconds: int,
        state: str = "transient_cooldown",
    ) -> bool:
        return await asyncio.to_thread(
            self._mark_summary_failure_state_sync,
            session_id,
            clean_text(state, 40) or "transient_cooldown",
            {
                "max_retries": max(1, int(max_retries or 1)),
                "cooldown_seconds": max(0, int(cooldown_seconds or 0)),
                "cooldown_at": utc_now(),
            },
        )

    def _mark_summary_failure_dead_letter_sync(
        self, session_id: str, max_retries: int
    ) -> bool:
        return self._mark_summary_failure_state_sync(
            session_id,
            "dead_letter",
            {
                "max_retries": max(1, int(max_retries or 1)),
                "dead_letter_at": utc_now(),
            },
        )

    def _mark_summary_failure_state_sync(
        self,
        session_id: str,
        state: str,
        extra_metadata: dict[str, Any],
    ) -> bool:
        session_id = clean_text(session_id, 200)
        with self._lock:
            with self._transaction_sync():
                row = self._conn.execute(
                    "SELECT metadata FROM summary_failures WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if not row:
                    return False
                metadata = json_loads(row["metadata"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata.update(extra_metadata or {})
                metadata["state"] = clean_text(state, 40)
                cur = self._conn.execute(
                    "UPDATE summary_failures SET metadata=?, updated_at=? WHERE session_id=?",
                    (json_dumps(metadata), utc_now(), session_id),
                )
                return cur.rowcount > 0

    def _clear_summary_failure_sync(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM summary_failures WHERE session_id=?",
                (clean_text(session_id, 200),),
            )
            self._conn.commit()
        return int(cur.rowcount or 0) > 0
