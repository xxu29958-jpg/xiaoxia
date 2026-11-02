from __future__ import annotations

import asyncio
from typing import Any

from .astrbot_compat import logger
from .models import (
    clean_text,
    json_dumps,
    json_loads,
    new_id,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value


class MemoryStoreImportRepair:
    """store import repair; the facade owns all shared resources."""

    async def repair_chat_import_identity_links(
        self,
        *,
        batch_id: str,
        session_id: str,
        entity_links: dict[str, dict[str, dict[str, str]]] | None = None,
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._repair_chat_import_identity_links_sync,
            batch_id,
            session_id,
            entity_links or {},
        )

    def _repair_chat_import_identity_links_sync(
        self,
        batch_id: str,
        session_id: str,
        entity_links: dict[str, dict[str, dict[str, str]]],
    ) -> dict[str, int]:
        batch_id = clean_text(batch_id, 120)
        session_id = clean_text(session_id, 200)
        if not batch_id or not session_id:
            return {"memories": 0, "entities": 0, "timeline": 0, "batch": 0}
        repaired_memories = 0
        repaired_entities = 0
        now = utc_now()
        with self._lock:
            with self._transaction_sync():
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE import_batch_id=?",
                    (batch_id,),
                ).fetchall()
                for row in rows:
                    link = entity_links.get(clean_text(row["id"], 120), {})

                    def resolved(prefix: str) -> tuple[str, str, str, str]:
                        current = (
                            clean_text(row[f"{prefix}_kind"], 40),
                            clean_text(row[f"{prefix}_id"], 120),
                            clean_text(row[f"{prefix}_name"], 80),
                            clean_text(row[f"{prefix}_role"], 80),
                        )
                        candidate = (
                            link.get(prefix)
                            if isinstance(link.get(prefix), dict)
                            else {}
                        )
                        candidate_id = clean_text(candidate.get("id"), 120)
                        if not candidate_id:
                            return current
                        return (
                            clean_text(candidate.get("kind"), 40) or current[0],
                            candidate_id,
                            clean_text(candidate.get("name"), 80) or current[2],
                            clean_text(candidate.get("role"), 80) or current[3],
                        )

                    subject = resolved("subject")
                    object_ref = resolved("object")
                    entity_changed = subject != (
                        clean_text(row["subject_kind"], 40),
                        clean_text(row["subject_id"], 120),
                        clean_text(row["subject_name"], 80),
                        clean_text(row["subject_role"], 80),
                    ) or object_ref != (
                        clean_text(row["object_kind"], 40),
                        clean_text(row["object_id"], 120),
                        clean_text(row["object_name"], 80),
                        clean_text(row["object_role"], 80),
                    )
                    session_changed = clean_text(row["session_id"], 200) != session_id
                    if not entity_changed and not session_changed:
                        continue
                    atom = self._memory_atom_record_for_row(
                        row,
                        subject_kind=subject[0],
                        subject_id=subject[1],
                        subject_name=subject[2],
                        subject_role=subject[3],
                        object_kind=object_ref[0],
                        object_id=object_ref[1],
                        object_name=object_ref[2],
                        object_role=object_ref[3],
                        session_id=session_id,
                    )
                    self._conn.execute(
                        """
                        UPDATE memories
                        SET subject_kind=?, subject_id=?, subject_name=?, subject_role=?,
                            object_kind=?, object_id=?, object_name=?, object_role=?,
                            session_id=?, canonical_key=?, content_fingerprint=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            *subject,
                            *object_ref,
                            session_id,
                            atom.canonical_key,
                            atom.content_fingerprint,
                            now,
                            row["id"],
                        ),
                    )
                    repaired_memories += 1
                    repaired_entities += int(entity_changed)
                timeline_cur = self._conn.execute(
                    "UPDATE timeline SET session_id=? WHERE import_batch_id=? AND session_id!=?",
                    (session_id, batch_id, session_id),
                )
                batch_cur = self._conn.execute(
                    "UPDATE chat_import_batches SET session_id=?, updated_at=? WHERE id=? AND session_id!=?",
                    (session_id, now, batch_id, session_id),
                )
        return {
            "memories": repaired_memories,
            "entities": repaired_entities,
            "timeline": int(timeline_cur.rowcount or 0),
            "batch": int(batch_cur.rowcount or 0),
        }

    async def neutralize_chat_import_summary_perspective(
        self, batch_id: str
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._neutralize_chat_import_summary_perspective_sync, batch_id
        )

    def _neutralize_chat_import_summary_perspective_sync(
        self, batch_id: str
    ) -> dict[str, int]:
        batch_id = clean_text(batch_id, 120)
        if not batch_id:
            return {"memories": 0, "embeddings_removed": 0}
        updated = 0
        embeddings_removed = 0
        now = utc_now()
        with self._lock:
            with self._transaction_sync():
                rows = self._conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE import_batch_id=? AND memory_type='conversation_summary'
                    """,
                    (batch_id,),
                ).fetchall()
                for row in rows:
                    metadata = json_loads(row["metadata"], {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                    try:
                        detail_version = int(metadata.get("detail_schema_version") or 0)
                    except (TypeError, ValueError):
                        detail_version = 0
                    if (
                        clean_text(metadata.get("summary_perspective"), 40)
                        == "neutral_third_person"
                        and detail_version >= 1
                    ):
                        continue
                    canonical = clean_text(metadata.get("canonical_summary"), 1600)
                    current = clean_text(row["content"], 4000)
                    if not canonical or canonical == current:
                        continue
                    metadata.setdefault("legacy_perspective_summary", current)
                    metadata["summary_perspective"] = "neutral_third_person"
                    atom = self._memory_atom_record_for_row(
                        row,
                        reset_semantic_keys=True,
                        content=canonical,
                        metadata=json_dumps(metadata),
                    )
                    self._conn.execute(
                        """
                        UPDATE memories
                        SET content=?, metadata=?, canonical_key=?, content_fingerprint=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            canonical,
                            json_dumps(metadata),
                            atom.canonical_key,
                            atom.content_fingerprint,
                            now,
                            row["id"],
                        ),
                    )
                    embeddings_removed += int(
                        self._conn.execute(
                            "DELETE FROM memory_embeddings WHERE memory_id=?",
                            (row["id"],),
                        ).rowcount
                        or 0
                    )
                    refreshed = self._conn.execute(
                        "SELECT * FROM memories WHERE id=?",
                        (row["id"],),
                    ).fetchone()
                    self._upsert_memory_fts_row(refreshed)
                    updated += 1
        return {"memories": updated, "embeddings_removed": embeddings_removed}

    async def update_chat_import_summary_detail(
        self,
        *,
        memory_id: str,
        detailed_summary: str,
        canonical_summary: str,
        detail_schema_version: int,
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._update_chat_import_summary_detail_sync,
            memory_id,
            detailed_summary,
            canonical_summary,
            detail_schema_version,
        )

    def _update_chat_import_summary_detail_sync(
        self,
        memory_id: str,
        detailed_summary: str,
        canonical_summary: str,
        detail_schema_version: int,
    ) -> dict[str, int]:
        memory_id = clean_text(memory_id, 120)
        detailed_summary = clean_text(detailed_summary, 2200)
        canonical_summary = clean_text(canonical_summary, 800)
        if not memory_id or not detailed_summary:
            return {"memories": 0, "embeddings_removed": 0}
        with self._lock:
            with self._transaction_sync():
                row = self._conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE id=? AND source_plugin='historical_chat_import'
                      AND memory_type='conversation_summary'
                    """,
                    (memory_id,),
                ).fetchone()
                if not row:
                    return {"memories": 0, "embeddings_removed": 0}
                metadata = json_loads(row["metadata"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                current = clean_text(row["content"], 4000)
                canonical_summary = (
                    canonical_summary
                    or clean_text(metadata.get("canonical_summary"), 800)
                    or current
                )
                if current != detailed_summary:
                    metadata.setdefault("legacy_brief_summary", current)
                metadata["canonical_summary"] = canonical_summary
                metadata["summary_perspective"] = "neutral_third_person"
                metadata["detail_schema_version"] = max(
                    1, int(detail_schema_version or 1)
                )
                atom = self._memory_atom_record_for_row(
                    row,
                    reset_semantic_keys=True,
                    content=detailed_summary,
                    metadata=json_dumps(metadata),
                )
                self._conn.execute(
                    """
                    UPDATE memories
                    SET content=?, metadata=?, canonical_key=?, content_fingerprint=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        detailed_summary,
                        json_dumps(metadata),
                        atom.canonical_key,
                        atom.content_fingerprint,
                        utc_now(),
                        memory_id,
                    ),
                )
                removed = int(
                    self._conn.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=?",
                        (memory_id,),
                    ).rowcount
                    or 0
                )
                refreshed = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                self._upsert_memory_fts_row(refreshed)
        return {"memories": 1, "embeddings_removed": removed}

    async def update_chat_import_daily_digest(
        self,
        *,
        batch_id: str,
        date: str,
        detailed_summary: str,
        segment_ids: list[str],
        source_message_ids: list[str],
        detail_schema_version: int,
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._update_chat_import_daily_digest_sync,
            batch_id,
            date,
            detailed_summary,
            segment_ids,
            source_message_ids,
            detail_schema_version,
        )

    def _update_chat_import_daily_digest_sync(
        self,
        batch_id: str,
        date: str,
        detailed_summary: str,
        segment_ids: list[str],
        source_message_ids: list[str],
        detail_schema_version: int,
    ) -> dict[str, int]:
        batch_id = clean_text(batch_id, 120)
        date = clean_text(date, 20)
        detailed_summary = clean_text(detailed_summary, 2200)
        if not batch_id or not date or not detailed_summary:
            return {"memories": 0, "embeddings_removed": 0}
        with self._lock:
            with self._transaction_sync():
                row = self._conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE import_batch_id=? AND memory_type='daily_digest'
                      AND json_valid(metadata) AND json_extract(metadata, '$.date')=?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (batch_id, date),
                ).fetchone()
                if not row:
                    return {"memories": 0, "embeddings_removed": 0}
                metadata = json_loads(row["metadata"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                current = clean_text(row["content"], 4000)
                if current != detailed_summary:
                    metadata.setdefault("legacy_brief_summary", current)
                metadata["segment_ids"] = [
                    clean_text(item, 120)
                    for item in segment_ids
                    if clean_text(item, 120)
                ][:32]
                metadata["source_message_ids"] = [
                    clean_text(item, 120)
                    for item in source_message_ids
                    if clean_text(item, 120)
                ][:64]
                metadata["summary_perspective"] = "neutral_third_person"
                metadata["detail_schema_version"] = max(
                    1, int(detail_schema_version or 1)
                )
                atom = self._memory_atom_record_for_row(
                    row,
                    reset_semantic_keys=True,
                    content=detailed_summary,
                    evidence="；".join(metadata["source_message_ids"]),
                    metadata=json_dumps(metadata),
                )
                evidence = "；".join(metadata["source_message_ids"])
                self._conn.execute(
                    """
                    UPDATE memories
                    SET content=?, evidence=?, metadata=?, canonical_key=?, content_fingerprint=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        detailed_summary,
                        evidence,
                        json_dumps(metadata),
                        atom.canonical_key,
                        atom.content_fingerprint,
                        utc_now(),
                        row["id"],
                    ),
                )
                removed = int(
                    self._conn.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=?",
                        (row["id"],),
                    ).rowcount
                    or 0
                )
                refreshed = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (row["id"],)
                ).fetchone()
                self._upsert_memory_fts_row(refreshed)
        return {"memories": 1, "embeddings_removed": removed}

    async def approve_livingmemory_imports(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._approve_livingmemory_imports_sync)

    def _approve_livingmemory_imports_sync(self) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id
                FROM memories
                WHERE source_plugin='livingmemory' AND review_status='pending'
                """
            ).fetchall()
            ids = [row["id"] for row in rows]
            if not ids:
                return {"updated": 0, "review_queue_updated": 0}
            placeholders = ",".join("?" for _ in ids)
            memory_cur = self._conn.execute(
                f"""
                UPDATE memories
                SET review_status='auto',
                    lifecycle='stable_memory',
                    updated_at=?
                WHERE id IN ({placeholders})
                """,
                [now] + ids,
            )
            queue_cur = self._conn.execute(
                f"""
                UPDATE review_queue
                SET status='auto',
                    updated_at=?
                WHERE memory_id IN ({placeholders}) AND status='pending'
                """,
                [now] + ids,
            )
            self._conn.commit()
        return {
            "updated": int(memory_cur.rowcount or 0),
            "review_queue_updated": int(queue_cur.rowcount or 0),
        }

    async def list_livingmemory_content_repair_candidates(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_livingmemory_content_repair_candidates_sync
        )

    def _list_livingmemory_content_repair_candidates_sync(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, content, evidence, metadata
                FROM memories
                WHERE source_plugin='livingmemory'
                """
            ).fetchall()
        candidates = []
        for row in rows:
            content = clean_text(row["content"], 80)
            if content.isdigit():
                candidates.append(dict(row))
        return candidates

    async def update_livingmemory_import_payload(
        self, memory_id: str, payload: dict[str, Any]
    ) -> bool:
        return await asyncio.to_thread(
            self._update_livingmemory_import_payload_sync, memory_id, payload
        )

    def _update_livingmemory_import_payload_sync(
        self, memory_id: str, payload: dict[str, Any]
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE memories
                SET content=?,
                    evidence=?,
                    metadata=?,
                    scope=?,
                    session_id=?,
                    group_id=?,
                    visibility=?,
                    object_kind=?,
                    object_id=?,
                    object_role=?,
                    occurred_at=COALESCE(NULLIF(?, ''), occurred_at),
                    content_fingerprint='',
                    updated_at=?
                WHERE id=? AND source_plugin='livingmemory'
                """,
                (
                    clean_text(redact_sensitive_text(payload.get("content")), 4000),
                    clean_text(redact_sensitive_text(payload.get("evidence")), 4000),
                    json_dumps(redact_sensitive_value(payload.get("metadata") or {})),
                    clean_text(payload.get("scope"), 40),
                    clean_text(payload.get("session_id"), 200),
                    clean_text(payload.get("group_id"), 120),
                    clean_text(payload.get("visibility"), 40),
                    clean_text(payload.get("object_kind"), 40),
                    clean_text(payload.get("object_id"), 120),
                    clean_text(payload.get("object_role"), 80),
                    clean_text(payload.get("occurred_at"), 80),
                    utc_now(),
                    clean_text(memory_id, 120),
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def add_import_batch(
        self,
        *,
        source_plugin: str,
        source_path: str,
        mode: str,
        stats: dict[str, Any],
    ) -> str:
        return await asyncio.to_thread(
            self._add_import_batch_sync, source_plugin, source_path, mode, stats
        )

    def _add_import_batch_sync(
        self, source_plugin: str, source_path: str, mode: str, stats: dict[str, Any]
    ) -> str:
        row_id = new_id("import")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO import_batches(id, source_plugin, source_path, mode, stats, created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    row_id,
                    source_plugin,
                    source_path,
                    mode,
                    json_dumps(stats),
                    utc_now(),
                ),
            )
            self._conn.commit()
        return row_id

    async def import_batch_ops(self, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量导入写入（portable JSONL 导入用），合并为单事务一次 commit。

        ops 为有序写入描述列表，元素形如：
          - {"kind": "memory", "record": MemoryRecord, "review_reason": str}
          - {"kind": "identity", "params": {platform, entity, aliases, profile, confidence}}
          - {"kind": "relationship", "params": {详见 _upsert_relationship_sync 关键字参数}}
          - {"kind": "timeline", "params": {event_type, session_id, scope, subject_id,
             object_id, content, metadata, occurred_at}, "summarize": bool}
          - {"kind": "acl_rule", "params": {owner_scope, owner_id, reader_scope,
             reader_id, effect, enabled, note}}
          - {"kind": "acl_policy", "params": {window_scope, window_id, read_mode,
             share_mode, capture_enabled, recall_enabled}}
        单项失败被 SAVEPOINT 隔离，不拖累整批；全部完成后一次 commit。
        返回与 ops 等长的结果列表，每项为 {"ok": bool, ...}。
        """
        return await asyncio.to_thread(self._import_batch_ops_sync, ops)

    def _import_batch_ops_sync(self, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not ops:
            return results
        with self._lock:
            with self._transaction_sync():
                for index, op in enumerate(ops):
                    kind = "unknown"
                    try:
                        if not isinstance(op, dict):
                            raise TypeError("batch operation must be a mapping")
                        kind = clean_text(op.get("kind"), 40) or "unknown"
                        with self._transaction_sync():  # 单项 SAVEPOINT 隔离失败
                            if kind == "memory":
                                memory_id = self._insert_memory_sync(
                                    op["record"],
                                    op.get("review_reason") or "",
                                    _commit=False,
                                )
                                results.append({"ok": True, "memory_id": memory_id})
                            elif kind == "identity":
                                row_id = self._upsert_identity_row_locked(
                                    **op["params"]
                                )
                                results.append({"ok": True, "row_id": row_id})
                            elif kind == "relationship":
                                row_id = self._upsert_relationship_sync(
                                    _commit=False, **op["params"]
                                )
                                results.append({"ok": True, "row_id": row_id})
                            elif kind == "timeline":
                                event_id = self._add_timeline_event_sync(**op["params"])
                                if op.get("summarize"):
                                    self._mark_timeline_summarized_sync(
                                        [event_id], _commit=False
                                    )
                                results.append({"ok": True, "event_id": event_id})
                            elif kind == "acl_rule":
                                result = self._upsert_acl_rule_sync(
                                    _commit=False, **op["params"]
                                )
                                results.append({"ok": True, "result": result})
                            elif kind == "acl_policy":
                                result = self._upsert_acl_policy_sync(
                                    _commit=False, **op["params"]
                                )
                                results.append({"ok": True, "result": result})
                            else:
                                results.append(
                                    {"ok": False, "code": f"unknown_batch_op:{kind}"}
                                )
                    except Exception as exc:
                        logger.warning(
                            "[MemoryCompanion] 批量导入单项失败 index=%s kind=%s error=%s",
                            index,
                            kind,
                            exc,
                            exc_info=True,
                        )
                        results.append(
                            {
                                "ok": False,
                                "code": "batch_op_error",
                                "error": clean_text(str(exc), 300),
                            }
                        )
        return results
