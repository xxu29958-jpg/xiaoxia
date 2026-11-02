from __future__ import annotations

import asyncio
import threading
from copy import deepcopy
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    memory_embedding_text_hash,
    utc_now,
)
from .store_support import (
    _memory_time_sql,
    _normalize_embedding_vector_values,
    _pack_embedding_vector,
    _unpack_embedding_vector,
)


class MemoryStoreEmbeddings:
    """Embeddings; state and transaction ownership remain in the facade."""

    async def upsert_memory_embedding(
        self,
        *,
        memory_id: str,
        provider_id: str,
        text_hash: str,
        vector: list[float],
    ) -> None:
        await self._run_recoverable_database_operation(
            self._upsert_memory_embedding_sync,
            memory_id,
            provider_id,
            text_hash,
            vector,
        )

    def _upsert_memory_embedding_sync(
        self,
        memory_id: str,
        provider_id: str,
        text_hash: str,
        vector: list[float],
    ) -> None:
        memory_id = clean_text(memory_id, 120)
        provider_id = clean_text(provider_id, 160)
        text_hash = clean_text(text_hash, 80)
        values = [float(item) for item in vector if isinstance(item, (int, float))]
        if not memory_id or not provider_id or not text_hash or not values:
            return
        now = utc_now()
        packed = _pack_embedding_vector(values)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_embeddings(
                    memory_id, provider_id, text_hash, dimension, vector, created_at, updated_at
                )
                SELECT ?,?,?,?,?,?,?
                WHERE EXISTS(SELECT 1 FROM memories WHERE id=?)
                ON CONFLICT(memory_id, provider_id) DO UPDATE SET
                    text_hash=excluded.text_hash,
                    dimension=excluded.dimension,
                    vector=excluded.vector,
                    updated_at=excluded.updated_at
                """,
                (
                    memory_id,
                    provider_id,
                    text_hash,
                    len(values),
                    packed,
                    now,
                    now,
                    memory_id,
                ),
            )
            self._conn.commit()

    async def search_memory_embeddings(self, **kwargs: Any) -> Any:
        from .vector_search import search_embeddings_sync

        cancelled = threading.Event()
        try:
            return await self._run_recoverable_database_operation(
                lambda: search_embeddings_sync(self, **kwargs, cancelled=cancelled),
            )
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def list_embedding_candidate_rows(
        self,
        *,
        provider_id: str,
        limit: int = 3000,
        include_pending: bool = False,
    ) -> list[tuple[MemoryRecord, list[float], str]]:
        return await self._run_recoverable_database_operation(
            self._list_embedding_candidate_rows_sync,
            provider_id,
            limit,
            include_pending,
        )

    def _list_embedding_candidate_rows_sync(
        self,
        provider_id: str,
        limit: int,
        include_pending: bool,
    ) -> list[tuple[MemoryRecord, list[float], str]]:
        provider_id = clean_text(provider_id, 160)
        if not provider_id:
            return []
        safe_limit = max(1, int(limit or 1))
        with self._lock:
            revision = self._memory_revision_sync()
            if revision != self._embedding_candidate_cache_revision:
                self._embedding_candidate_cache.clear()
                self._embedding_candidate_cache_revision = revision
            cache_key = (provider_id, bool(include_pending), safe_limit)
            cached = self._embedding_candidate_cache.get(cache_key)
            if cached is not None:
                return deepcopy(cached)
        where = f"m.lifecycle != 'archived' AND {self._recallable_memory_sql('m')} AND e.provider_id=?"
        params: list[Any] = [provider_id]
        if not include_pending:
            where += " AND m.review_status != 'pending'"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    m.*,
                    e.vector AS embedding_vector,
                    e.text_hash AS embedding_text_hash
                FROM memory_embeddings e
                JOIN memories m ON m.id=e.memory_id
                WHERE {where}
                ORDER BY m.importance DESC, {_memory_time_sql("m")} DESC
                LIMIT ?
                """,
                params + [safe_limit],
            ).fetchall()
        result: list[tuple[MemoryRecord, list[float], str]] = []
        for row in rows:
            vector = _normalize_embedding_vector_values(
                _unpack_embedding_vector(row["embedding_vector"])
            )
            if not vector:
                continue
            result.append(
                (
                    MemoryRecord.from_row(row),
                    vector,
                    clean_text(row["embedding_text_hash"], 80),
                )
            )
        with self._lock:
            self._embedding_candidate_cache[cache_key] = result
            while (
                len(self._embedding_candidate_cache)
                > self.EMBEDDING_CANDIDATE_CACHE_MAX
            ):
                self._embedding_candidate_cache.pop(
                    next(iter(self._embedding_candidate_cache)), None
                )
        return deepcopy(result)

    async def list_memories_missing_embeddings(
        self,
        *,
        provider_id: str,
        limit: int = 80,
        include_pending: bool = False,
        embedding_max_text_chars: int = 1200,
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_memories_missing_embeddings_sync,
            provider_id,
            limit,
            include_pending,
            embedding_max_text_chars,
        )

    async def scan_memories_missing_embeddings(
        self,
        *,
        provider_id: str,
        limit: int = 80,
        include_pending: bool = False,
        embedding_max_text_chars: int = 1200,
        offset: int = 0,
    ) -> tuple[list[MemoryRecord], int, bool]:
        """Scan one bounded window and return (stale records, next offset, exhausted)."""

        return await self._run_recoverable_database_operation(
            self._scan_memories_missing_embeddings_sync,
            provider_id,
            limit,
            include_pending,
            embedding_max_text_chars,
            offset,
        )

    def _list_memories_missing_embeddings_sync(
        self,
        provider_id: str,
        limit: int,
        include_pending: bool,
        embedding_max_text_chars: int,
    ) -> list[MemoryRecord]:
        result, _next_offset, _exhausted = self._scan_memories_missing_embeddings_sync(
            provider_id,
            limit,
            include_pending,
            embedding_max_text_chars,
            0,
        )
        return result

    def _scan_memories_missing_embeddings_sync(
        self,
        provider_id: str,
        limit: int,
        include_pending: bool,
        embedding_max_text_chars: int,
        offset: int,
    ) -> tuple[list[MemoryRecord], int, bool]:
        provider_id = clean_text(provider_id, 160)
        if not provider_id:
            return [], 0, True
        where = f"m.lifecycle != 'archived' AND {self._recallable_memory_sql('m')}"
        params: list[Any] = [provider_id]
        if not include_pending:
            where += " AND m.review_status != 'pending'"
        safe_limit = max(1, int(limit or 1))
        # Hash validation is intentionally done in Python because SQLite cannot
        # evaluate the canonical embedding document. Keep the scan bounded; the
        # existing importance/time ordering brings recently changed memories into
        # the next backfill pass first.
        scan_limit = max(safe_limit, min(2000, safe_limit * 8))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT m.*
                    ,e.text_hash AS embedding_text_hash
                FROM memories m
                LEFT JOIN memory_embeddings e
                  ON e.memory_id=m.id AND e.provider_id=?
                WHERE {where}
                  ORDER BY m.importance DESC, {_memory_time_sql("m")} DESC
                  LIMIT ?
                  OFFSET ?
                """,
                params + [scan_limit, max(0, int(offset or 0))],
            ).fetchall()
        result: list[MemoryRecord] = []
        processed = 0
        for processed, row in enumerate(rows, start=1):
            record = MemoryRecord.from_row(row)
            stored_hash = clean_text(row["embedding_text_hash"], 80)
            expected_hash = memory_embedding_text_hash(
                record,
                max_chars=embedding_max_text_chars,
            )
            if not stored_hash or stored_hash != expected_hash:
                result.append(record)
                if len(result) >= safe_limit:
                    break
        next_offset = max(0, int(offset or 0)) + processed
        exhausted = processed >= len(rows) and len(rows) < scan_limit
        return result, next_offset, exhausted
