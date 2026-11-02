from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
)
from .store_support import (
    _memory_time_sql,
)


class MemoryStoreCandidateQueries:
    """Candidate queries; state and transaction ownership remain in the facade."""

    async def list_candidate_memories(
        self, limit: int = 500, include_pending: bool = False
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_candidate_memories_sync, limit, include_pending
        )

    def _list_candidate_memories_sync(
        self, limit: int, include_pending: bool
    ) -> list[MemoryRecord]:
        with self._lock:
            rows = self._materialized_candidate_rows(self._conn, limit, include_pending)
        return [MemoryRecord.from_row(row) for row in rows]

    def _materialized_candidate_rows(
        self,
        conn: sqlite3.Connection,
        limit: int,
        include_pending: bool,
    ) -> list[Any]:
        # Bot Personal archive references (including legacy rows identified by
        # their display text) are served by a dedicated bridge and are not
        # ordinary memory documents.
        where = f"lifecycle != 'archived' AND {self._recallable_memory_sql()}"
        params: list[Any] = []
        if not include_pending:
            where += " AND review_status != 'pending'"
        return conn.execute(
            f"""
            SELECT * FROM memories
            WHERE {where}
            ORDER BY importance DESC, {_memory_time_sql()} DESC
            LIMIT ?
            """,
            params + [max(1, int(limit))],
        ).fetchall()

    async def list_current_window_candidate_memories(
        self,
        *,
        scope: str,
        session_id: str = "",
        user_id: str = "",
        group_id: str = "",
        limit: int = 600,
        include_pending: bool = False,
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_current_window_candidate_memories_sync,
            scope,
            session_id,
            user_id,
            group_id,
            limit,
            include_pending,
        )

    def _list_current_window_candidate_memories_sync(
        self,
        scope: str,
        session_id: str,
        user_id: str,
        group_id: str,
        limit: int,
        include_pending: bool,
    ) -> list[MemoryRecord]:
        with self._lock:
            rows = self._current_window_candidate_rows(
                self._conn, scope, session_id, user_id, group_id, limit, include_pending
            )
        return [MemoryRecord.from_row(row) for row in rows]

    def _current_window_candidate_rows(
        self,
        conn: sqlite3.Connection,
        scope: str,
        session_id: str,
        user_id: str,
        group_id: str,
        limit: int,
        include_pending: bool,
    ) -> list[Any]:
        scope = clean_text(scope, 40).lower()
        session_id = clean_text(session_id, 200)
        user_id = clean_text(user_id, 120)
        group_id = clean_text(group_id, 120)
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        if scope == "group" and group_id:
            clauses.append("(scope='group' AND group_id=?)")
            params.append(group_id)
        elif scope == "private" and user_id:
            clauses.append("(scope='private' AND (subject_id=? OR object_id=?))")
            params.extend([user_id, user_id])
        if not clauses:
            return []
        where = (
            f"lifecycle != 'archived' AND {self._recallable_memory_sql()} AND ("
            + " OR ".join(clauses)
            + ")"
        )
        if not include_pending:
            where += " AND review_status != 'pending'"
        return conn.execute(
            f"""
            SELECT *
            FROM memories
            WHERE {where}
            ORDER BY importance DESC,
                     {_memory_time_sql(include_updated=True)} DESC
            LIMIT ?
            """,
            params + [max(1, int(limit or 1))],
        ).fetchall()

    async def list_time_window_candidate_memories(
        self,
        start_at: str,
        end_at: str,
        limit: int = 1200,
        include_pending: bool = False,
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_time_window_candidate_memories_sync,
            start_at,
            end_at,
            limit,
            include_pending,
        )

    def _list_time_window_candidate_memories_sync(
        self,
        start_at: str,
        end_at: str,
        limit: int,
        include_pending: bool,
    ) -> list[MemoryRecord]:
        with self._lock:
            rows = self._time_window_candidate_rows(
                self._conn, start_at, end_at, limit, include_pending
            )
        return [MemoryRecord.from_row(row) for row in rows]

    def _time_window_candidate_rows(
        self,
        conn: sqlite3.Connection,
        start_at: str,
        end_at: str,
        limit: int,
        include_pending: bool,
    ) -> list[Any]:
        start = clean_text(start_at, 80)
        end = clean_text(end_at, 80)
        if not start or not end:
            return []
        where = f"lifecycle != 'archived' AND {self._recallable_memory_sql()}"
        params: list[Any] = [start, end, start, end, start, end]
        if not include_pending:
            where += " AND review_status != 'pending'"
        return conn.execute(
            f"""
            SELECT *
            FROM memories
            WHERE {where}
              AND (
                (julianday(occurred_at) >= julianday(?) AND julianday(occurred_at) < julianday(?))
                OR (julianday(created_at) >= julianday(?) AND julianday(created_at) < julianday(?))
                OR (julianday(updated_at) >= julianday(?) AND julianday(updated_at) < julianday(?))
              )
            ORDER BY
                {_memory_time_sql(include_updated=True)} DESC,
                importance DESC
            LIMIT ?
            """,
            params + [max(1, int(limit or 1))],
        ).fetchall()

    async def list_retrieval_candidate_bundle(
        self,
        *,
        materialize_limit: int,
        current_window: dict[str, Any] | None,
        fts_terms: list[str],
        fts_limit: int,
        keyword_terms: list[str],
        keyword_limit: int,
        keyword_fallback_min_fts: int,
        time_window: tuple[str, str, int] | None,
        include_pending: bool = False,
    ) -> dict[str, Any]:
        """Load all retrieval candidate sources in one read-only pass.

        Replaces four separate ``asyncio.gather`` store calls (each of which
        re-acquired the single write connection lock) with a single threaded
        pass over a dedicated read-only connection. Under WAL this reader runs
        concurrently with writes, so candidate loading no longer serializes
        behind the main lock. Keyword-fallback semantics are preserved: the
        keyword scan only runs when FTS produced too few candidates.

        Corresponds to optimization_plan.md §6.1/§6.2.
        """
        return await asyncio.to_thread(
            self._list_retrieval_candidate_bundle_sync,
            materialize_limit,
            current_window or {},
            fts_terms,
            fts_limit,
            keyword_terms,
            keyword_limit,
            keyword_fallback_min_fts,
            time_window,
            include_pending,
        )

    def _list_retrieval_candidate_bundle_sync(
        self,
        materialize_limit: int,
        current_window: dict[str, Any],
        fts_terms: list[str],
        fts_limit: int,
        keyword_terms: list[str],
        keyword_limit: int,
        keyword_fallback_min_fts: int,
        time_window: tuple[str, str, int] | None,
        include_pending: bool,
    ) -> dict[str, Any]:
        bundle_started = time.perf_counter()
        conn, lock = self._read_connection_for_bundle()
        with lock:
            lock_acquired_at = time.perf_counter()
            ranked_rows = self._materialized_candidate_rows(
                conn, materialize_limit, include_pending
            )
            current_window_rows = self._current_window_candidate_rows(
                conn,
                clean_text(current_window.get("scope", ""), 40),
                clean_text(current_window.get("session_id", ""), 200),
                clean_text(current_window.get("user_id", ""), 120),
                clean_text(current_window.get("group_id", ""), 120),
                int(current_window.get("limit", 0) or 0),
                include_pending,
            )
            fts_rows = self._fts_candidate_rows(
                conn, fts_terms, fts_limit, include_pending
            )
            keyword_fallback_used = len(fts_rows) < max(
                0, int(keyword_fallback_min_fts or 0)
            )
            keyword_rows = (
                self._keyword_candidate_rows(
                    conn, keyword_terms, keyword_limit, include_pending
                )
                if keyword_fallback_used
                else []
            )
            time_rows: list[Any] = []
            if time_window is not None:
                start_at, end_at, tw_limit = time_window
                time_rows = self._time_window_candidate_rows(
                    conn, start_at, end_at, tw_limit, include_pending
                )
            queries_done_at = time.perf_counter()
        parsed = {
            "ranked_candidates": [
                MemoryRecord.from_row_light(row) for row in ranked_rows
            ],
            "current_window_candidates": [
                MemoryRecord.from_row_light(row) for row in current_window_rows
            ],
            "fts_candidates": [MemoryRecord.from_row_light(row) for row in fts_rows],
            "keyword_candidates": [
                MemoryRecord.from_row_light(row) for row in keyword_rows
            ],
            "keyword_fallback_used": keyword_fallback_used,
            "time_window_candidates": [
                MemoryRecord.from_row_light(row) for row in time_rows
            ],
        }
        parsed["_timing"] = {
            "lock_wait_ms": int((lock_acquired_at - bundle_started) * 1000),
            "queries_ms": int((queries_done_at - lock_acquired_at) * 1000),
            "parse_ms": int((time.perf_counter() - queries_done_at) * 1000),
            "inner_total_ms": int((time.perf_counter() - bundle_started) * 1000),
        }
        return parsed

    async def list_fts_candidate_memories(
        self,
        terms: list[str],
        limit: int = 800,
        include_pending: bool = False,
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_fts_candidate_memories_sync,
            terms,
            limit,
            include_pending,
        )

    def _list_fts_candidate_memories_sync(
        self,
        terms: list[str],
        limit: int,
        include_pending: bool,
    ) -> list[MemoryRecord]:
        with self._lock:
            rows = self._fts_candidate_rows(self._conn, terms, limit, include_pending)
        return [MemoryRecord.from_row(row) for row in rows]

    def _fts_candidate_rows(
        self,
        conn: sqlite3.Connection,
        terms: list[str],
        limit: int,
        include_pending: bool,
    ) -> list[Any]:
        if not self._fts_enabled:
            return []
        query = self._fts_match_query(terms)
        if not query:
            return []
        where = f"m.lifecycle != 'archived' AND {self._recallable_memory_sql('m')}"
        params: list[Any] = [query]
        if not include_pending:
            where += " AND m.review_status != 'pending'"
        try:
            return conn.execute(
                f"""
                SELECT m.*
                FROM memory_fts
                JOIN memories m ON m.id = memory_fts.memory_id
                WHERE memory_fts MATCH ?
                  AND {where}
                ORDER BY bm25(memory_fts), m.importance DESC,
                         {_memory_time_sql("m")} DESC
                LIMIT ?
                """,
                params + [max(1, int(limit or 1))],
            ).fetchall()
        except sqlite3.Error:
            return []

    def _fts_match_query(self, terms: list[str]) -> str:
        variants: list[str] = []
        for term in terms or []:
            text = clean_text(term, 80).lower()
            if not text:
                continue
            for variant in self._fts_term_variants(text):
                if variant and variant not in variants:
                    variants.append(variant)
            if len(variants) >= 48:
                break
        return " OR ".join(self._quote_fts_term(term) for term in variants[:48])

    @staticmethod
    def _fts_term_variants(term: str) -> list[str]:
        variants = [term]
        compact = re.sub(r"\s+", "", term)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", compact):
            variants.extend(
                compact[index : index + 2] for index in range(0, len(compact) - 1)
            )
        return variants

    @staticmethod
    def _quote_fts_term(term: str) -> str:
        return '"' + clean_text(term, 80).replace('"', '""') + '"'

    async def list_keyword_candidate_memories(
        self,
        terms: list[str],
        limit: int = 800,
        include_pending: bool = False,
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_keyword_candidate_memories_sync,
            terms,
            limit,
            include_pending,
        )

    def _list_keyword_candidate_memories_sync(
        self,
        terms: list[str],
        limit: int,
        include_pending: bool,
    ) -> list[MemoryRecord]:
        with self._lock:
            rows = self._keyword_candidate_rows(
                self._conn, terms, limit, include_pending
            )
        return [MemoryRecord.from_row(row) for row in rows]

    def _keyword_candidate_rows(
        self,
        conn: sqlite3.Connection,
        terms: list[str],
        limit: int,
        include_pending: bool,
    ) -> list[Any]:
        cleaned_terms = []
        for term in terms or []:
            text = clean_text(term, 80).lower()
            if text and text not in cleaned_terms:
                cleaned_terms.append(text)
            if len(cleaned_terms) >= 24:
                break
        if not cleaned_terms:
            return []

        where = f"lifecycle != 'archived' AND {self._recallable_memory_sql()}"
        params: list[Any] = []
        if not include_pending:
            where += " AND review_status != 'pending'"

        columns = [
            "content",
            "evidence",
            "tags",
            "metadata",
            "subject_id",
            "subject_name",
            "object_id",
            "object_name",
            "session_id",
            "group_id",
        ]
        term_clauses: list[str] = []
        for term in cleaned_terms:
            like = self._like_pattern(term)
            term_clauses.append(
                "("
                + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns)
                + ")"
            )
            params.extend([like] * len(columns))
        where += " AND (" + " OR ".join(term_clauses) + ")"

        return conn.execute(
            f"""
            SELECT *
            FROM memories
            WHERE {where}
            ORDER BY
                CASE WHEN session_id != '' THEN 0 ELSE 1 END,
                importance DESC,
                occurred_at DESC,
                created_at DESC
            LIMIT ?
            """,
            params + [max(1, int(limit or 1))],
        ).fetchall()

    @staticmethod
    def _like_pattern(term: str) -> str:
        escaped = (
            clean_text(term, 120)
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f"%{escaped}%"
