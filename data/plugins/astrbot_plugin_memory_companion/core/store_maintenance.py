from __future__ import annotations

import asyncio
import time
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .store_support import (
    _memory_time_sql,
)


class MemoryStoreMaintenance:
    """store maintenance; the facade owns all shared resources."""

    async def maintenance_repair(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._maintenance_repair_sync)

    def _maintenance_repair_sync(self) -> dict[str, Any]:
        with self._lock, self._transaction_sync():
            manual_fixed = self._normalize_legacy_manual_visibility_sync()
            internal_bot_self_fixed = self._normalize_internal_bot_self_scopes_sync()
            utterance_fixed_cur = self._conn.execute(
                """
                UPDATE memories
                SET reality_level='observed_utterance', updated_at=?
                WHERE memory_type='conversation_event' AND reality_level='real_user_fact'
                """,
                (utc_now(),),
            )
            utterance_fixed = int(utterance_fixed_cur.rowcount or 0)

        fingerprint_fixed = 0
        batch_size = max(1, int(self.MAINTENANCE_REPAIR_BATCH_SIZE))
        with self._lock:
            marker = self._conn.execute(
                "SELECT value FROM schema_metadata WHERE key='maintenance_fingerprint_repair_version'"
            ).fetchone()
        full_fingerprint_audit = (
            marker is None
            or clean_text(marker["value"], 40)
            != self.MAINTENANCE_FINGERPRINT_REPAIR_VERSION
        )
        last_rowid = 0
        while True:
            with self._lock, self._transaction_sync():
                fingerprint_filter = (
                    ""
                    if full_fingerprint_audit
                    else (
                        " AND (content_fingerprint='' OR COALESCE(merged_count, 0)<1)"
                    )
                )
                rows = self._conn.execute(
                    "SELECT rowid AS _repair_rowid, * FROM memories "
                    f"WHERE rowid>?{fingerprint_filter} ORDER BY rowid LIMIT ?",
                    (last_rowid, batch_size),
                ).fetchall()
                for row in rows:
                    record = MemoryRecord.from_row(row)
                    old_fingerprint = record.content_fingerprint
                    record.content_fingerprint = ""
                    record.ensure_defaults()
                    if (
                        record.content_fingerprint != old_fingerprint
                        or int(row["merged_count"] or 0) < 1
                    ):
                        self._conn.execute(
                            "UPDATE memories SET content_fingerprint=?, merged_count=max(merged_count, 1), updated_at=? WHERE id=?",
                            (record.content_fingerprint, utc_now(), record.id),
                        )
                        fingerprint_fixed += 1
                if rows:
                    last_rowid = int(rows[-1]["_repair_rowid"])
            if len(rows) < batch_size:
                break
            time.sleep(0)

        with self._lock, self._transaction_sync():
            duplicates = self._conn.execute(
                """
                SELECT content_fingerprint, COUNT(*) AS count
                FROM memories
                WHERE content_fingerprint!='' AND lifecycle!='archived'
                GROUP BY content_fingerprint
                HAVING count > 1
                """
            ).fetchall()
            merged = 0
            for dup in duplicates:
                rows = self._conn.execute(
                    """
                    SELECT id, importance, confidence, merged_count, created_at
                    FROM memories
                    WHERE content_fingerprint=? AND lifecycle!='archived'
                    ORDER BY merged_count DESC, importance DESC, created_at ASC
                    """,
                    (dup["content_fingerprint"],),
                ).fetchall()
                keep = rows[0]
                for row in rows[1:]:
                    self._conn.execute(
                        """
                        UPDATE memories
                        SET lifecycle='archived', validity_status='archived', supersedes_id=?, updated_at=?
                        WHERE id=?
                        """,
                        (keep["id"], utc_now(), row["id"]),
                    )
                    self._conn.execute(
                        """
                        UPDATE memories
                        SET importance=max(importance, ?),
                            confidence=max(confidence, ?),
                            merged_count=COALESCE(merged_count, 1) + COALESCE(?, 1),
                            updated_at=?
                        WHERE id=?
                        """,
                        (
                            row["importance"],
                            row["confidence"],
                            row["merged_count"],
                            utc_now(),
                            keep["id"],
                        ),
                    )
                    if self._fts_enabled:
                        self._conn.execute(
                            "DELETE FROM memory_fts WHERE memory_id=?",
                            (row["id"],),
                        )
                    merged += 1
            if full_fingerprint_audit:
                self._conn.execute(
                    """
                    INSERT INTO schema_metadata(key,value,updated_at)
                    VALUES('maintenance_fingerprint_repair_version',?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (self.MAINTENANCE_FINGERPRINT_REPAIR_VERSION, utc_now()),
                )
            # Fingerprint fixes do not affect FTS text, and archived duplicates
            # are removed from the index above. Rebuild only if the searchable
            # row count still differs from the index.
            fts_rebuild_needed = False
            if self._fts_enabled:
                recallable_count = int(
                    self._conn.execute(
                        f"SELECT COUNT(*) FROM memories WHERE {self._recallable_memory_sql()}"
                    ).fetchone()[0]
                    or 0
                )
                fts_count = int(
                    self._conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
                    or 0
                )
                fts_rebuild_needed = recallable_count != fts_count
            fts_rebuilt = (
                self._rebuild_memory_fts_sync()
                if self._fts_enabled and fts_rebuild_needed
                else 0
            )
        return {
            "manual_visibility_fixed": manual_fixed,
            "internal_bot_self_scope_fixed": internal_bot_self_fixed,
            "utterance_reality_fixed": utterance_fixed,
            "fingerprint_fixed": fingerprint_fixed,
            "duplicates_archived": merged,
            "fts_rebuilt": fts_rebuilt,
        }

    async def list_decay_candidate_pool(self, limit: int = 2000) -> list[MemoryRecord]:
        return await asyncio.to_thread(self._list_decay_candidate_pool_sync, limit)

    def _list_decay_candidate_pool_sync(self, limit: int) -> list[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM memories
                WHERE (lifecycle='stable_memory' AND review_status!='pending')
                   OR (lifecycle='short_term_candidate' AND review_status='pending')
                ORDER BY
                    {_memory_time_sql()} ASC,
                    created_at ASC
                LIMIT ?
                """,
                (max(1, int(limit or 1)),),
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    async def archive_raw_events_older_than(
        self, cutoff_at: str, limit: int = 1000
    ) -> int:
        return await asyncio.to_thread(
            self._archive_raw_events_older_than_sync, cutoff_at, limit
        )

    def _archive_raw_events_older_than_sync(self, cutoff_at: str, limit: int) -> int:
        cutoff_at = clean_text(cutoff_at, 80)
        if not cutoff_at:
            return 0
        now = utc_now()
        with self._lock:
            with self._transaction_sync():
                rows = self._conn.execute(
                    """
                    SELECT id, metadata
                    FROM memories
                    WHERE lifecycle='raw_event'
                      AND COALESCE(julianday(NULLIF(occurred_at, '')), julianday(NULLIF(created_at, '')), 0) < julianday(?)
                    ORDER BY COALESCE(julianday(NULLIF(occurred_at, '')), julianday(NULLIF(created_at, '')), 0) ASC
                    LIMIT ?
                    """,
                    (cutoff_at, max(1, int(limit or 1))),
                ).fetchall()
                archived = 0
                for row in rows:
                    metadata = json_loads(row["metadata"], {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                    metadata["retention_archived"] = {
                        "reason": "raw_event_retention",
                        "cutoff_at": cutoff_at,
                        "archived_at": now,
                    }
                    cur = self._conn.execute(
                        """
                        UPDATE memories
                        SET lifecycle='archived',
                            validity_status='archived',
                            metadata=?,
                            updated_at=?
                        WHERE id=? AND lifecycle='raw_event'
                        """,
                        (json_dumps(metadata), now, row["id"]),
                    )
                    archived += int(cur.rowcount or 0)
        return archived

    async def archive_expired_pending_candidates(
        self, cutoff_at: str, limit: int = 1000
    ) -> int:
        return await asyncio.to_thread(
            self._archive_expired_pending_candidates_sync, cutoff_at, limit
        )

    def _archive_expired_pending_candidates_sync(
        self, cutoff_at: str, limit: int
    ) -> int:
        cutoff_at = clean_text(cutoff_at, 80)
        if not cutoff_at:
            return 0
        now = utc_now()
        with self._lock, self._transaction_sync():
            rows = self._conn.execute(
                """
                SELECT id, metadata, tags, durability
                FROM memories
                WHERE lifecycle='short_term_candidate'
                  AND review_status='pending'
                  AND COALESCE(julianday(NULLIF(valid_to, '')), julianday(NULLIF(created_at, '')), 0) < julianday(?)
                ORDER BY COALESCE(julianday(NULLIF(valid_to, '')), julianday(NULLIF(created_at, '')), 0) ASC
                LIMIT ?
                """,
                (cutoff_at, max(1, int(limit or 1))),
            ).fetchall()
            archived = 0
            for row in rows:
                tags = json_loads(row["tags"], [])
                tags = (
                    {clean_text(tag, 80).casefold() for tag in tags}
                    if isinstance(tags, list)
                    else set()
                )
                if row["durability"] == "pinned" or tags & {
                    "manual",
                    "protected",
                    "keep",
                    "no_decay",
                }:
                    continue
                metadata = json_loads(row["metadata"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["pending_review_archived"] = {
                    "reason": "review_retention_expired",
                    "cutoff_at": cutoff_at,
                    "archived_at": now,
                }
                cur = self._conn.execute(
                    """
                    UPDATE memories
                    SET lifecycle='archived', validity_status='archived', review_status='expired',
                        metadata=?, updated_at=?
                    WHERE id=? AND lifecycle='short_term_candidate' AND review_status='pending'
                    """,
                    (json_dumps(metadata), now, row["id"]),
                )
                if cur.rowcount:
                    self._conn.execute(
                        "UPDATE review_queue SET status='expired', updated_at=? WHERE memory_id=? AND status='pending'",
                        (now, row["id"]),
                    )
                    archived += 1
        return archived

    async def prune_retained_rows(
        self,
        *,
        summarized_timeline_cutoff: str = "",
        unsummarized_timeline_cutoff: str = "",
        injection_log_cutoff: str = "",
        limit: int = 2000,
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._prune_retained_rows_sync,
            summarized_timeline_cutoff,
            unsummarized_timeline_cutoff,
            injection_log_cutoff,
            limit,
        )

    async def unsummarized_timeline_backlog(self) -> dict[str, Any]:
        """Report how much raw history never became a memory, and how old it is."""

        def read():
            with self._lock:
                row = self._conn.execute(
                    """
                    SELECT COUNT(*) AS total,
                           MIN(COALESCE(NULLIF(occurred_at, ''), created_at)) AS oldest
                    FROM timeline
                    WHERE summarized_at=''
                    """
                ).fetchone()
                frozen = self._conn.execute(
                    """
                    SELECT COUNT(*) FROM timeline t
                    JOIN summary_batch_events e ON e.event_id=t.id
                    JOIN summary_batches b ON b.id=e.batch_id
                    WHERE t.summarized_at='' AND b.state='quarantined'
                    """
                ).fetchone()
            return {
                "unsummarized_events": int((row["total"] if row else 0) or 0),
                "oldest_unsummarized_at": clean_text(row["oldest"] if row else "", 80),
                "quarantined_frozen_events": int((frozen[0] if frozen else 0) or 0),
            }

        return await asyncio.to_thread(read)

    def _prune_retained_rows_sync(
        self,
        summarized_timeline_cutoff: str,
        unsummarized_timeline_cutoff: str,
        injection_log_cutoff: str,
        limit: int,
    ) -> dict[str, int]:
        summarized_timeline_cutoff = clean_text(summarized_timeline_cutoff, 80)
        unsummarized_timeline_cutoff = clean_text(unsummarized_timeline_cutoff, 80)
        injection_log_cutoff = clean_text(injection_log_cutoff, 80)
        safe_limit = max(1, int(limit or 1))
        deleted = {"timeline": 0, "injection_logs": 0}
        with self._lock:
            with self._transaction_sync():
                if summarized_timeline_cutoff:
                    rows = self._conn.execute(
                        """
                        SELECT id
                        FROM timeline
                        WHERE summarized_at!=''
                          AND retention_class!='historical_archive'
                          AND COALESCE(julianday(NULLIF(occurred_at, '')),julianday(NULLIF(created_at, '')),0) < julianday(?)
                        ORDER BY COALESCE(julianday(NULLIF(occurred_at, '')),julianday(NULLIF(created_at, '')),0) ASC
                        LIMIT ?
                        """,
                        (summarized_timeline_cutoff, safe_limit),
                    ).fetchall()
                    ids = [row["id"] for row in rows]
                    if ids:
                        self._mark_timeline_sources_expired_sync(ids)
                        result: dict[str, int] = {}
                        self._delete_many_by_ids("timeline", "id", ids, result)
                        deleted["timeline"] = result.get("timeline", 0)
                if unsummarized_timeline_cutoff:
                    rows = self._conn.execute(
                        """
                        SELECT id
                        FROM timeline
                        WHERE summarized_at=''
                          AND retention_class!='historical_archive'
                          AND COALESCE(NULLIF(occurred_at, ''), created_at) < ?
                          AND NOT EXISTS(SELECT 1 FROM summary_batch_events e WHERE e.event_id=timeline.id)
                        ORDER BY COALESCE(NULLIF(occurred_at, ''), created_at) ASC
                        LIMIT ?
                        """,
                        (unsummarized_timeline_cutoff, safe_limit),
                    ).fetchall()
                    ids = [row["id"] for row in rows]
                    if ids:
                        result: dict[str, int] = {}
                        self._delete_many_by_ids("timeline", "id", ids, result)
                        deleted["timeline"] = deleted.get("timeline", 0) + result.get(
                            "timeline", 0
                        )
                if injection_log_cutoff:
                    rows = self._conn.execute(
                        """
                        SELECT id
                        FROM injection_logs
                        WHERE created_at < ?
                        ORDER BY created_at ASC
                        LIMIT ?
                        """,
                        (injection_log_cutoff, safe_limit),
                    ).fetchall()
                    ids = [row["id"] for row in rows]
                    if ids:
                        result = {}
                        self._delete_many_by_ids("injection_logs", "id", ids, result)
                        deleted["injection_logs"] = result.get("injection_logs", 0)
        return deleted

    async def archive_stale_pending_memories(
        self, cutoff_at: str, limit: int = 500
    ) -> int:
        """Cold-archive review candidates nobody looked at within the window.

        A pending candidate is excluded from every recall query and, before this,
        from the decay pool as well, so a batch that could not be validated used
        to hang in ``short_term_candidate``/``pending`` forever. Archiving keeps
        it recoverable while letting the review queue drain.
        """
        cutoff_at = clean_text(cutoff_at, 80)
        if not cutoff_at:
            return 0
        safe_limit = max(1, int(limit or 1))
        with self._lock:
            with self._transaction_sync():
                rows = self._conn.execute(
                    """
                    SELECT id
                    FROM memories
                    WHERE review_status='pending'
                      AND lifecycle='short_term_candidate'
                      AND COALESCE(NULLIF(occurred_at, ''), created_at) < ?
                    ORDER BY COALESCE(NULLIF(occurred_at, ''), created_at) ASC
                    LIMIT ?
                    """,
                    (cutoff_at, safe_limit),
                ).fetchall()
                if not rows:
                    return 0
                ids = [row["id"] for row in rows]
                now = utc_now()
                archived = 0
                for memory_id in ids:
                    metadata_row = self._conn.execute(
                        "SELECT metadata FROM memories WHERE id=?", (memory_id,)
                    ).fetchone()
                    metadata = (
                        json_loads(metadata_row["metadata"], {}) if metadata_row else {}
                    )
                    metadata = metadata if isinstance(metadata, dict) else {}
                    metadata["pending_review_expired"] = {
                        "archived_at": now,
                        "cutoff": cutoff_at,
                    }
                    cur = self._conn.execute(
                        """
                        UPDATE memories
                        SET lifecycle='archived',
                            validity_status='archived',
                            metadata=?,
                            updated_at=?
                        WHERE id=? AND lifecycle!='archived'
                        """,
                        (json_dumps(metadata), now, memory_id),
                    )
                    archived += int(cur.rowcount or 0)
                    self._conn.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,)
                    )
                    self._conn.execute(
                        "UPDATE review_queue SET status='superseded', updated_at=? "
                        "WHERE memory_id=? AND status='pending'",
                        (now, memory_id),
                    )
                self._embedding_candidate_cache.clear()
                self._embedding_candidate_cache_revision = ""
        return archived

    async def archive_memories(
        self,
        memory_ids: list[str],
        *,
        reason: str,
        supersedes_id: str = "",
    ) -> int:
        return await asyncio.to_thread(
            self._archive_memories_sync,
            memory_ids,
            reason,
            supersedes_id,
        )

    def _archive_memories_sync(
        self, memory_ids: list[str], reason: str, supersedes_id: str
    ) -> int:
        ids = [
            clean_text(memory_id, 120)
            for memory_id in memory_ids
            if clean_text(memory_id, 120)
        ]
        if not ids:
            return 0
        now = utc_now()
        reason = clean_text(reason, 120)
        supersedes_id = clean_text(supersedes_id, 120)
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, metadata FROM memories WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            archived = 0
            for row in rows:
                metadata = json_loads(row["metadata"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["decay_archived"] = {
                    "reason": reason,
                    "supersedes_id": supersedes_id,
                    "archived_at": now,
                }
                cur = self._conn.execute(
                    """
                    UPDATE memories
                    SET lifecycle='archived',
                        validity_status='archived',
                        supersedes_id=?,
                        metadata=?,
                        updated_at=?
                    WHERE id=? AND lifecycle!='archived'
                    """,
                    (supersedes_id, json_dumps(metadata), now, row["id"]),
                )
                archived += int(cur.rowcount or 0)
            self._conn.commit()
        return archived
