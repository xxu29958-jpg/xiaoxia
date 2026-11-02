from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value
from .store_support import (
    _memory_time_sql,
)


class MemoryStoreImportBatches:
    """store import batches; the facade owns all shared resources."""

    async def upsert_chat_import_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._upsert_chat_import_batch_sync, payload)

    def _upsert_chat_import_batch_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = clean_text(payload.get("id"), 120)
        if not batch_id:
            raise ValueError("chat import batch id is required")
        now = utc_now()
        json_fields = {"speaker_map", "options", "stats"}
        payload = redact_sensitive_value(payload)
        values: dict[str, Any] = {
            "id": batch_id,
            "upload_id": clean_text(payload.get("upload_id"), 120),
            "source_name": clean_text(payload.get("source_name"), 240),
            "source_hash": clean_text(payload.get("source_hash"), 120),
            "state": clean_text(payload.get("state"), 40) or "prepared",
            "session_id": clean_text(payload.get("session_id"), 200),
            "scope": clean_text(payload.get("scope"), 40) or "private",
            "platform": clean_text(payload.get("platform"), 40),
            "user_id": clean_text(payload.get("user_id"), 120),
            "user_name": clean_text(payload.get("user_name"), 120),
            "bot_id": clean_text(payload.get("bot_id"), 120),
            "bot_name": clean_text(payload.get("bot_name"), 120),
            "speaker_map": payload.get("speaker_map")
            if isinstance(payload.get("speaker_map"), dict)
            else {},
            "options": payload.get("options")
            if isinstance(payload.get("options"), dict)
            else {},
            "stats": payload.get("stats")
            if isinstance(payload.get("stats"), dict)
            else {},
            "checkpoint_segment": max(0, int(payload.get("checkpoint_segment") or 0)),
            "total_segments": max(0, int(payload.get("total_segments") or 0)),
            "completed_segments": max(0, int(payload.get("completed_segments") or 0)),
            "summary_memory_count": max(
                0, int(payload.get("summary_memory_count") or 0)
            ),
            "important_event_count": max(
                0, int(payload.get("important_event_count") or 0)
            ),
            "relationship_observation_count": max(
                0, int(payload.get("relationship_observation_count") or 0)
            ),
            "backup_path": clean_text(payload.get("backup_path"), 2000),
            "error": clean_text(redact_sensitive_text(payload.get("error")), 1000),
            "created_at": clean_text(payload.get("created_at"), 80) or now,
            "updated_at": now,
        }
        columns = list(values)
        db_values = [
            json_dumps(values[name]) if name in json_fields else values[name]
            for name in columns
        ]
        updates = ",".join(
            f"{name}=excluded.{name}"
            for name in columns
            if name not in {"id", "created_at"}
        )
        with self._lock:
            with self._transaction_sync():
                self._conn.execute(
                    f"INSERT INTO chat_import_batches({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
                    f"ON CONFLICT(id) DO UPDATE SET {updates}",
                    db_values,
                )
        return self._get_chat_import_batch_sync(batch_id) or {}

    async def get_chat_import_batch(self, batch_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_chat_import_batch_sync, batch_id)

    def _get_chat_import_batch_sync(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_import_batches WHERE id=?",
                (clean_text(batch_id, 120),),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("speaker_map", "options", "stats"):
            result[key] = json_loads(result.get(key), {})
        return result

    async def list_chat_import_batches(
        self, limit: int | None = 20
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_chat_import_batches_sync, limit)

    def _list_chat_import_batches_sync(self, limit: int | None) -> list[dict[str, Any]]:
        with self._lock:
            if limit is None:
                rows = self._conn.execute(
                    "SELECT * FROM chat_import_batches ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM chat_import_batches ORDER BY updated_at DESC LIMIT ?",
                    (max(1, min(1000, int(limit or 20))),),
                ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("speaker_map", "options", "stats"):
                item[key] = json_loads(item.get(key), {})
            results.append(item)
        return results

    async def update_chat_import_batch(
        self, batch_id: str, **changes: Any
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self._update_chat_import_batch_sync, batch_id, changes
        )

    def _update_chat_import_batch_sync(
        self, batch_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        allowed = {
            "state",
            "checkpoint_segment",
            "total_segments",
            "completed_segments",
            "summary_memory_count",
            "important_event_count",
            "relationship_observation_count",
            "stats",
            "options",
            "speaker_map",
            "backup_path",
            "error",
        }
        json_fields = {"stats", "options", "speaker_map"}
        selected = redact_sensitive_value(
            {key: value for key, value in changes.items() if key in allowed}
        )
        if not selected:
            return self._get_chat_import_batch_sync(batch_id)
        selected["updated_at"] = utc_now()
        clauses = ",".join(f"{key}=?" for key in selected)
        params = [
            json_dumps(value) if key in json_fields else value
            for key, value in selected.items()
        ]
        params.append(clean_text(batch_id, 120))
        with self._lock:
            with self._transaction_sync():
                self._conn.execute(
                    f"UPDATE chat_import_batches SET {clauses} WHERE id=?", params
                )
        return self._get_chat_import_batch_sync(batch_id)

    async def replace_chat_import_segments(
        self, batch_id: str, segments: list[dict[str, Any]]
    ) -> int:
        return await asyncio.to_thread(
            self._replace_chat_import_segments_sync, batch_id, segments
        )

    def _replace_chat_import_segments_sync(
        self, batch_id: str, segments: list[dict[str, Any]]
    ) -> int:
        batch_id = clean_text(batch_id, 120)
        now = utc_now()
        with self._lock:
            with self._transaction_sync():
                self._conn.execute(
                    "DELETE FROM chat_import_segments WHERE batch_id=?", (batch_id,)
                )
                for raw in segments:
                    self._conn.execute(
                        """
                        INSERT INTO chat_import_segments(
                            id,batch_id,segment_index,start_at,end_at,local_date,message_ids,
                            transcript,char_count,turn_count,status,attempts,result,
                            summary_memory_id,error,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            clean_text(raw.get("id"), 120),
                            batch_id,
                            max(0, int(raw.get("segment_index") or 0)),
                            clean_text(raw.get("start_at"), 80),
                            clean_text(raw.get("end_at"), 80),
                            clean_text(raw.get("local_date"), 20),
                            json_dumps(raw.get("message_ids") or []),
                            redact_sensitive_text(raw.get("transcript")),
                            max(0, int(raw.get("char_count") or 0)),
                            max(0, int(raw.get("turn_count") or 0)),
                            clean_text(raw.get("status"), 30) or "pending",
                            max(0, int(raw.get("attempts") or 0)),
                            json_dumps(redact_sensitive_value(raw.get("result") or {})),
                            clean_text(raw.get("summary_memory_id"), 120),
                            clean_text(redact_sensitive_text(raw.get("error")), 1000),
                            now,
                            now,
                        ),
                    )
        return len(segments)

    async def chat_import_segments(
        self, batch_id: str, *, statuses: set[str] | None = None
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._chat_import_segments_sync, batch_id, statuses
        )

    def _chat_import_segments_sync(
        self, batch_id: str, statuses: set[str] | None
    ) -> list[dict[str, Any]]:
        params: list[Any] = [clean_text(batch_id, 120)]
        where = "batch_id=?"
        normalized = sorted(
            {
                clean_text(item, 30)
                for item in (statuses or set())
                if clean_text(item, 30)
            }
        )
        if normalized:
            where += f" AND status IN ({','.join('?' for _ in normalized)})"
            params.extend(normalized)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM chat_import_segments WHERE {where} ORDER BY segment_index ASC",
                params,
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["message_ids"] = json_loads(item.get("message_ids"), [])
            item["result"] = json_loads(item.get("result"), {})
            results.append(item)
        return results

    async def update_chat_import_segment(self, segment_id: str, **changes: Any) -> bool:
        return await asyncio.to_thread(
            self._update_chat_import_segment_sync, segment_id, changes
        )

    def _update_chat_import_segment_sync(
        self, segment_id: str, changes: dict[str, Any]
    ) -> bool:
        allowed = {"status", "attempts", "result", "summary_memory_id", "error"}
        selected = redact_sensitive_value(
            {key: value for key, value in changes.items() if key in allowed}
        )
        if not selected:
            return False
        selected["updated_at"] = utc_now()
        clauses = ",".join(f"{key}=?" for key in selected)
        params = [
            json_dumps(value) if key == "result" else value
            for key, value in selected.items()
        ]
        params.append(clean_text(segment_id, 120))
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE chat_import_segments SET {clauses} WHERE id=?", params
            )
            self._conn.commit()
        return cur.rowcount > 0

    async def rollback_chat_import_batch(self, batch_id: str) -> dict[str, int]:
        return await asyncio.to_thread(self._rollback_chat_import_batch_sync, batch_id)

    async def chat_import_memory_counts(self, batch_id: str) -> dict[str, int]:
        return await asyncio.to_thread(self._chat_import_memory_counts_sync, batch_id)

    def _chat_import_memory_counts_sync(self, batch_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT memory_type, COUNT(*) AS count FROM memories WHERE import_batch_id=? GROUP BY memory_type",
                (clean_text(batch_id, 120),),
            ).fetchall()
        result = {
            clean_text(row["memory_type"], 80): int(row["count"] or 0) for row in rows
        }
        result["total"] = sum(result.values())
        return result

    async def chat_import_rebind_target_exists(
        self,
        *,
        batch_id: str,
        session_id: str,
        user_id: str,
        bot_id: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._private_memory_context_exists_sync,
            session_id,
            user_id,
            bot_id,
            batch_id,
        )

    async def private_memory_context_exists(
        self,
        *,
        session_id: str,
        user_id: str,
        bot_id: str,
        exclude_import_batch_id: str = "",
    ) -> bool:
        return await asyncio.to_thread(
            self._private_memory_context_exists_sync,
            session_id,
            user_id,
            bot_id,
            exclude_import_batch_id,
        )

    def _private_memory_context_exists_sync(
        self,
        session_id: str,
        user_id: str,
        bot_id: str,
        exclude_import_batch_id: str = "",
    ) -> bool:
        return (
            self._private_memory_context_row_sync(
                session_id,
                user_id,
                bot_id,
                exclude_import_batch_id,
            )
            is not None
        )

    def _private_memory_context_row_sync(
        self,
        session_id: str,
        user_id: str,
        bot_id: str,
        exclude_import_batch_id: str = "",
    ) -> sqlite3.Row | None:
        session_id = clean_text(session_id, 200)
        user_id = clean_text(user_id, 120)
        bot_id = clean_text(bot_id, 120)
        exclude_import_batch_id = clean_text(exclude_import_batch_id, 120)
        if not session_id or not user_id or not bot_id:
            return None
        exclude_clause = ""
        params: list[Any] = [session_id]
        if exclude_import_batch_id:
            exclude_clause = "AND COALESCE(import_batch_id, '')!=?"
            params.append(exclude_import_batch_id)
        params.extend([user_id, user_id, bot_id, bot_id, bot_id])
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT *
                FROM memories
                WHERE scope='private' AND session_id=?
                  {exclude_clause}
                  AND (
                    (subject_kind='user' AND subject_id=?)
                    OR (object_kind='user' AND object_id=?)
                  )
                  AND (
                    (subject_kind='bot' AND subject_id=?)
                    OR (object_kind='bot' AND object_id=?)
                    OR CASE
                        WHEN json_valid(metadata)
                        THEN COALESCE(CAST(json_extract(metadata, '$.owner_bot_id') AS TEXT), '')
                        ELSE ''
                       END=?
                  )
                ORDER BY
                  CASE WHEN COALESCE(import_batch_id, '')='' THEN 0 ELSE 1 END,
                  {_memory_time_sql()} DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return row

    async def list_chat_import_memories(self, batch_id: str) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_chat_import_memories_sync,
            batch_id,
        )

    def _list_chat_import_memories_sync(self, batch_id: str) -> list[MemoryRecord]:
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM memories
                WHERE import_batch_id=? AND lifecycle!='archived'
                ORDER BY importance DESC, {_memory_time_sql()} ASC
                """,
                (clean_text(batch_id, 120),),
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    async def list_chat_import_memories_missing_embeddings(
        self,
        batch_id: str,
        provider_id: str,
        *,
        include_pending: bool = False,
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_chat_import_memories_missing_embeddings_sync,
            batch_id,
            provider_id,
            include_pending,
        )

    def _list_chat_import_memories_missing_embeddings_sync(
        self,
        batch_id: str,
        provider_id: str,
        include_pending: bool,
    ) -> list[MemoryRecord]:
        where = "m.import_batch_id=? AND m.lifecycle!='archived'"
        if not include_pending:
            where += " AND m.review_status!='pending'"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT m.* FROM memories m
                LEFT JOIN memory_embeddings e
                  ON e.memory_id=m.id AND e.provider_id=?
                WHERE {where} AND (e.memory_id IS NULL OR e.text_hash='')
                ORDER BY m.importance DESC, {_memory_time_sql("m")} ASC
                """,
                (clean_text(provider_id, 160), clean_text(batch_id, 120)),
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    def _rollback_chat_import_batch_sync(self, batch_id: str) -> dict[str, int]:
        batch_id = clean_text(batch_id, 120)
        deleted = {"memories": 0, "timeline": 0, "segments": 0}
        with self._lock:
            with self._transaction_sync():
                memory_rows = self._conn.execute(
                    "SELECT id FROM memories WHERE import_batch_id=?",
                    (batch_id,),
                ).fetchall()
                memory_ids = [clean_text(row["id"], 120) for row in memory_rows]
                for memory_id in memory_ids:
                    self._conn.execute(
                        "DELETE FROM review_queue WHERE memory_id=?", (memory_id,)
                    )
                    self._conn.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,)
                    )
                    self._conn.execute(
                        "DELETE FROM relationship_edges WHERE source_memory_id=?",
                        (memory_id,),
                    )
                    self._conn.execute(
                        "DELETE FROM knowledge_edges WHERE source_memory_id=?",
                        (memory_id,),
                    )
                    self._delete_memory_fts_row(memory_id)
                    deleted["memories"] += int(
                        self._conn.execute(
                            "DELETE FROM memories WHERE id=?", (memory_id,)
                        ).rowcount
                        or 0
                    )
                timeline_rows = self._conn.execute(
                    "SELECT id FROM timeline WHERE import_batch_id=?", (batch_id,)
                ).fetchall()
                deleted["memories"] += (
                    self._delete_memories_referencing_timeline_ids_sync(
                        [row["id"] for row in timeline_rows]
                    )
                )
                deleted["timeline"] = int(
                    self._conn.execute(
                        "DELETE FROM timeline WHERE import_batch_id=?", (batch_id,)
                    ).rowcount
                    or 0
                )
                deleted["segments"] = int(
                    self._conn.execute(
                        "DELETE FROM chat_import_segments WHERE batch_id=?", (batch_id,)
                    ).rowcount
                    or 0
                )
                self._conn.execute(
                    """
                    UPDATE chat_import_batches
                    SET state='rolled_back', checkpoint_segment=0, completed_segments=0,
                        summary_memory_count=0, important_event_count=0,
                        relationship_observation_count=0, error='', updated_at=?
                    WHERE id=?
                    """,
                    (utc_now(), batch_id),
                )
        return deleted
