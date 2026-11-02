from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    utc_now,
)


class MemoryStoreRecordState:
    """store record state; the facade owns all shared resources."""

    async def update_memory_visibility(self, memory_id: str, visibility: str) -> bool:
        return await asyncio.to_thread(
            self._update_memory_visibility_sync, memory_id, visibility
        )

    def _update_memory_visibility_sync(self, memory_id: str, visibility: str) -> bool:
        memory_id = clean_text(memory_id, 120)
        visibility = clean_text(visibility, 40)
        now = utc_now()
        with self._lock:  # noqa: SIM117
            with self._transaction_sync():
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                if row is None:
                    return False
                record = MemoryRecord.from_row(row)
                target = deepcopy(record)
                target.visibility = visibility
                if self._profile_single_value_domain_conflict_sync(target):
                    return False
                cur = self._conn.execute(
                    "UPDATE memories SET visibility=?, updated_at=? WHERE id=?",
                    (visibility, now, memory_id),
                )
                if cur.rowcount:
                    self._embedding_candidate_cache.clear()
                    self._embedding_candidate_cache_revision = ""
                return cur.rowcount > 0

    async def update_memory_lifecycle(self, memory_id: str, lifecycle: str) -> bool:
        return await asyncio.to_thread(
            self._update_memory_lifecycle_sync, memory_id, lifecycle
        )

    def _update_memory_lifecycle_sync(self, memory_id: str, lifecycle: str) -> bool:
        lifecycle = clean_text(lifecycle, 40)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET lifecycle=?, validity_status=?, updated_at=? WHERE id=?",
                (
                    lifecycle,
                    "archived" if lifecycle == "archived" else "active",
                    utc_now(),
                    clean_text(memory_id, 120),
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def mark_accessed(self, memory_ids: list[str]) -> None:
        await asyncio.to_thread(self._mark_accessed_sync, memory_ids)

    def _mark_accessed_sync(self, memory_ids: list[str]) -> None:
        ids = [memory_id for memory_id in memory_ids if memory_id]
        if not ids:
            return
        now = utc_now()
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            self._conn.execute(
                f"""
                UPDATE memories
                SET access_count = access_count + 1, last_accessed_at = ?
                WHERE id IN ({placeholders})
                """,
                [now] + ids,
            )
            self._conn.commit()

    async def mark_injected(self, memory_ids: list[str], when: str = "") -> int:
        """Record only memories that reached the final injected context."""

        return await asyncio.to_thread(self._mark_injected_sync, memory_ids, when)

    def _mark_injected_sync(self, memory_ids: list[str], when: str = "") -> int:
        ids = list(
            dict.fromkeys(
                clean_text(memory_id, 120)
                for memory_id in memory_ids or []
                if clean_text(memory_id, 120)
            )
        )
        if not ids:
            return 0
        timestamp = clean_text(when, 80) or utc_now()
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            cur = self._conn.execute(
                f"""
                UPDATE memories
                SET injection_count = injection_count + 1,
                    last_injected_at = ?,
                    reinforcement_score = min(1.0, reinforcement_score + 0.025)
                WHERE id IN ({placeholders})
                """,
                [timestamp, *ids],
            )
            self._conn.commit()
        return max(0, int(cur.rowcount or 0))

    async def list_review_queue(self, limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_review_queue_sync, limit)

    def _list_review_queue_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    rq.id,
                    rq.memory_id,
                    rq.reason,
                    rq.status,
                    rq.created_at,
                    m.memory_type,
                    m.subject_id,
                    m.subject_name,
                    m.object_id,
                    m.object_name,
                    m.scope,
                    m.session_id,
                    m.group_id,
                    m.visibility,
                    m.sayability,
                    m.reality_level,
                    m.lifecycle,
                    m.content,
                    m.evidence,
                    m.confidence,
                    m.importance,
                    m.tags,
                    m.metadata,
                    m.source_plugin,
                    m.import_batch_id,
                    m.occurred_at
                FROM review_queue rq
                LEFT JOIN memories m ON m.id = rq.memory_id
                WHERE rq.status = 'pending'
                ORDER BY rq.created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    async def stats(self) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(self._stats_sync)

    async def memory_revision(self) -> str:
        return await self._run_recoverable_database_operation(
            self._memory_revision_sync
        )

    def _memory_revision_sync(self) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT revision FROM retrieval_revision WHERE singleton=1"
            ).fetchone()
        if not row:
            return "0"
        return str(int(row["revision"] or 0))

    def _stats_sync(self) -> dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            pending = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE review_status='pending'"
            ).fetchone()[0]
            stable = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE lifecycle='stable_memory'"
            ).fetchone()[0]
            identities = self._conn.execute(
                "SELECT COUNT(*) FROM identities"
            ).fetchone()[0]
            timeline = self._conn.execute("SELECT COUNT(*) FROM timeline").fetchone()[0]
            relationships = self._conn.execute(
                "SELECT COUNT(*) FROM relationship_edges"
            ).fetchone()[0]
            knowledge_nodes = self._conn.execute(
                "SELECT COUNT(*) FROM knowledge_nodes"
            ).fetchone()[0]
            knowledge_edges = self._conn.execute(
                "SELECT COUNT(*) FROM knowledge_edges"
            ).fetchone()[0]
            open_threads = self._conn.execute(
                "SELECT COUNT(*) FROM cross_window_threads WHERE status='open'"
            ).fetchone()[0]
            injection_logs = self._conn.execute(
                "SELECT COUNT(*) FROM injection_logs"
            ).fetchone()[0]
            acl_rules = self._conn.execute(
                "SELECT COUNT(*) FROM memory_acl_rules WHERE enabled=1"
            ).fetchone()[0]
            by_scope = {
                row["scope"]: row["count"]
                for row in self._conn.execute(
                    "SELECT scope, COUNT(*) AS count FROM memories GROUP BY scope"
                ).fetchall()
            }
            # Rows owned by a different Bot are invisible to recall by design, so
            # an owner that drifted (a redeployed adapter, a second platform) used
            # to look like missing memory. Surface the split next to the counts.
            by_owner_bot = {
                row["owner"] or "<empty>": row["count"]
                for row in self._conn.execute(
                    "SELECT owner_bot_id AS owner, COUNT(*) AS count FROM memories "
                    "GROUP BY owner_bot_id ORDER BY count DESC LIMIT 20"
                ).fetchall()
            }
        current_wal_files = self._database_file_snapshot()
        last_wal_health = dict(self._last_wal_health)
        memory_storage = {
            "database_bytes": max(0, int(current_wal_files.get("db_bytes") or 0)),
            "wal_bytes": max(0, int(current_wal_files.get("wal_bytes") or 0)),
            "shm_bytes": max(0, int(current_wal_files.get("shm_bytes") or 0)),
        }
        memory_storage["total_bytes"] = sum(memory_storage.values())
        return {
            "db_path": str(self.db_path),
            "memory_storage_bytes": memory_storage["total_bytes"],
            "memory_storage": memory_storage,
            "total_memories": total,
            "pending_review": pending,
            "stable_memories": stable,
            "identities": identities,
            "timeline_events": timeline,
            "relationships": relationships,
            "knowledge_nodes": knowledge_nodes,
            "knowledge_edges": knowledge_edges,
            "open_threads": open_threads,
            "injection_logs": injection_logs,
            "acl_rules": acl_rules,
            "by_scope": by_scope,
            "by_owner_bot": by_owner_bot,
            "wal": {
                **last_wal_health,
                **current_wal_files,
                "current_files": current_wal_files,
                "last_health_check": last_wal_health,
                "last_database_error": dict(self._last_database_error),
                "database_recovery_attempts": self._database_recovery_attempts,
                "database_recovery_successes": self._database_recovery_successes,
            },
        }
