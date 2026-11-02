from __future__ import annotations

import asyncio
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
)


class MemoryCompanionServiceHistoryImport:
    """service history import; the facade owns all shared resources."""

    def preview_historical_chat_upload(
        self,
        *,
        filename: str,
        content: bytes,
        base_year: int = 0,
    ) -> dict[str, Any]:
        return self.chat_importer.stage_upload(
            filename=filename,
            content=content,
            base_year=base_year,
        )

    async def qq_history_capabilities(self) -> dict[str, Any]:
        return await self.qq_history_reader.capabilities()

    async def preview_qq_historical_chat(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        read_result = await self.qq_history_reader.read(
            platform_id=clean_text(payload.get("platform_id"), 160),
            user_id=clean_text(payload.get("user_id"), 40),
            start_at=payload.get("start_at"),
            end_at=payload.get("end_at"),
        )
        return await asyncio.to_thread(
            self.chat_importer.stage_structured_messages,
            source_name=read_result["source_name"],
            source_hash=read_result["source_hash"],
            messages=read_result["messages"],
            stats=read_result["stats"],
            source_kind=read_result["source_kind"],
            source_metadata=read_result["source_metadata"],
            identity_context=read_result["identity_context"],
            speaker_suggestions=read_result["speaker_suggestions"],
            read_stats=read_result["read_stats"],
            truncated=read_result["truncated"],
            warnings=read_result["warnings"],
        )

    async def start_historical_chat_import(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.chat_importer.start_import(payload)

    async def historical_chat_import_status(
        self,
        batch_id: str = "",
        *,
        upgrade_legacy: bool = True,
    ) -> dict[str, Any]:
        return await self.chat_importer.status(batch_id, upgrade_legacy=upgrade_legacy)

    async def pause_historical_chat_import(self, batch_id: str) -> dict[str, Any]:
        return await self.chat_importer.pause_batch(batch_id)

    async def resume_historical_chat_import(self, batch_id: str) -> dict[str, Any]:
        return await self.chat_importer.resume_batch(batch_id)

    async def rollback_historical_chat_import(self, batch_id: str) -> dict[str, Any]:
        return await self.chat_importer.rollback_batch(batch_id)

    async def rebind_historical_chat_import(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self.chat_importer.rebind_batch(payload)
        self._retrieval_result_cache.clear()
        batch_id = clean_text(result.get("batch_id"), 120)
        records = await self.store.list_chat_import_memories(batch_id)
        embeddings_scheduled = 0
        if self.config.bool("retrieval.embedding_enabled", False):
            for record in records:
                self._schedule_memory_embedding(record.id, record)
                embeddings_scheduled += 1
        if self.config.bool("knowledge_graph.enabled", True):
            for record in records:
                metadata = record.metadata if isinstance(record.metadata, dict) else {}
                if not any(
                    metadata.get(key)
                    for key in ("topics", "key_facts", "participants", "associations")
                ):
                    continue
                await self._index_summary_knowledge_graph(
                    self._context_from_memory_record(record),
                    record,
                    metadata,
                    record.id,
                )
        rebind = result.get("rebind")
        if isinstance(rebind, dict):
            rebind["embeddings_scheduled"] = embeddings_scheduled
        return result

    async def rebind_memory_owners(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Preview or apply a narrowly selected Bot-owner repair.

        Unowned legacy rows are the default repair target.  Rebinding rows
        already owned by another Bot requires an explicit source owner, which
        keeps multi-Bot isolation intact while still allowing migrations.
        """
        payload = payload if isinstance(payload, dict) else {}
        target_bot_id = clean_text(
            payload.get("target_bot_id") or payload.get("bot_id"), 120
        )
        if not target_bot_id or target_bot_id.casefold() in {"self", "bot", "bot_self"}:
            raise ValueError("target_bot_id 必须是明确的 Bot ID")

        raw_ids = payload.get("memory_ids")
        memory_ids = (
            [clean_text(item, 120) for item in raw_ids]
            if isinstance(raw_ids, list)
            else []
        )
        memory_ids = list(dict.fromkeys(item for item in memory_ids if item))
        selector = {
            "session_id": clean_text(payload.get("session_id"), 200),
            "source_plugin": clean_text(payload.get("source_plugin"), 120),
            "memory_type": clean_text(payload.get("memory_type"), 80),
            "visibility": clean_text(payload.get("visibility"), 40),
            "scope": clean_text(payload.get("scope"), 40),
        }
        if not memory_ids and not any(selector.values()):
            raise ValueError("请提供 memory_ids，或至少一个精确筛选条件")
        if len(memory_ids) > 2000:
            raise ValueError("一次最多重新绑定 2000 条记忆")

        if memory_ids:
            records_by_id = await self.store.get_memories_by_ids(memory_ids)
            records = [
                records_by_id[item] for item in memory_ids if item in records_by_id
            ]
        else:
            records = await self.store.list_memories(
                limit=min(2000, max(1, int(payload.get("limit") or 500))),
                include_pending=True,
                session_id=selector["session_id"],
                memory_type=selector["memory_type"],
                visibility=selector["visibility"],
                scope=selector["scope"],
            )
            if selector["source_plugin"]:
                records = [
                    item
                    for item in records
                    if clean_text(item.source_plugin, 120) == selector["source_plugin"]
                ]

        source_owner_present = (
            "source_bot_id" in payload or "from_owner_bot_id" in payload
        )
        source_owner = clean_text(
            payload.get("source_bot_id") or payload.get("from_owner_bot_id"), 120
        )
        candidates: list[MemoryRecord] = []
        skipped: dict[str, int] = {}
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            current_owner = clean_text(
                record.owner_bot_id or metadata.get("owner_bot_id"), 120
            )
            if source_owner_present:
                if current_owner != source_owner:
                    skipped["source_owner_mismatch"] = (
                        skipped.get("source_owner_mismatch", 0) + 1
                    )
                    continue
            elif current_owner and current_owner != "self":
                skipped["already_owned"] = skipped.get("already_owned", 0) + 1
                continue
            candidates.append(record)

        preview = {
            "count": len(candidates),
            "sample_ids": [record.id for record in candidates[:20]],
            "skipped": skipped,
            "target_bot_id": target_bot_id,
            "selector": {key: value for key, value in selector.items() if value},
            "source_owner": source_owner if source_owner_present else "<empty-or-self>",
        }
        if not bool(payload.get("confirm")):
            return {"mode": "preview", "preview": preview}
        if not candidates:
            return {"mode": "applied", "updated": 0, "preview": preview}

        backup = await self.store.backup_async(".before_memory_owner_rebind")
        updated = 0
        for record in candidates:
            if await self.store.update_memory_owner_bot(record.id, target_bot_id):
                updated += 1
        self._retrieval_result_cache.clear()
        return {
            "mode": "applied",
            "updated": updated,
            "preview": preview,
            "backup_path": str(backup),
        }
