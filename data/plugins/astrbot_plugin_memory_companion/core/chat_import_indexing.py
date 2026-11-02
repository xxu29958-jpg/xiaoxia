from __future__ import annotations

import asyncio
from typing import Any

from .models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
    clean_text,
    stable_fingerprint,
    utc_now,
)


class HistoricalChatImporterIndexing:
    """Indexing; shared runtime resources belong to the facade."""

    async def _index_batch_embeddings(self, batch: dict[str, Any]) -> dict[str, Any]:
        config = getattr(self.service, "config", None)
        enabled = bool(
            getattr(config, "bool", lambda *_: False)(
                "retrieval.embedding_enabled", False
            )
        )
        result: dict[str, Any] = {
            "enabled": enabled,
            "status": "disabled",
            "eligible": 0,
            "indexed": 0,
        }
        if not enabled:
            return result
        ctx = SessionContext(
            session_id=str(batch.get("session_id") or ""),
            scope=str(batch.get("scope") or "private"),
            platform=str(batch.get("platform") or ""),
            user_id=str(batch.get("user_id") or ""),
            user_name=str(batch.get("user_name") or ""),
            bot_id=str(batch.get("bot_id") or ""),
        )
        try:
            provider, provider_id = await self.service._resolve_embedding_provider(ctx)
            result["provider_id"] = clean_text(provider_id, 160)
            if provider is None or not provider_id:
                result["status"] = "provider_unavailable"
                return result
            records = await self.store.list_chat_import_memories(batch["id"])
            include_pending = bool(
                getattr(config, "bool", lambda *_: False)(
                    "retrieval.embedding_index_pending", False
                )
            )
            eligible = [
                record
                for record in records
                if include_pending or record.review_status != "pending"
            ]
            result["eligible"] = len(eligible)
            missing = await self.store.list_chat_import_memories_missing_embeddings(
                batch["id"],
                provider_id,
                include_pending=include_pending,
            )
            outcomes = await asyncio.gather(
                *(
                    self.service._embed_memory_record(provider, provider_id, record)
                    for record in missing
                ),
                return_exceptions=True,
            )
            result["already_indexed"] = max(0, len(eligible) - len(missing))
            result["indexed"] = result["already_indexed"] + sum(
                outcome is True for outcome in outcomes
            )
            result["errors"] = sum(outcome is not True for outcome in outcomes)
            result["status"] = (
                "complete" if result["indexed"] == result["eligible"] else "partial"
            )
            return result
        except Exception as exc:
            result["status"] = "partial"
            result["error"] = clean_text(exc, 180)
            return result

    async def _insert_reconciled_memories(
        self, batch: dict[str, Any], output: dict[str, Any]
    ) -> None:
        for raw in output.get("daily_digests") or []:
            if not isinstance(raw, dict):
                continue
            date = clean_text(raw.get("date"), 20)
            summary = clean_text(raw.get("summary"), 1800)
            if not date or not summary:
                continue
            record = MemoryRecord(
                id="mem_"
                + stable_fingerprint(batch["id"], "daily_digest", date, summary)[:20],
                memory_type="daily_digest",
                subject=EntityRef(
                    kind="user", id=batch["user_id"], name=batch.get("user_name") or ""
                ),
                object=EntityRef.bot_self(
                    bot_id=batch["bot_id"], bot_name=batch.get("bot_name") or "Bot"
                ),
                scope="private",
                session_id=batch["session_id"],
                platform=batch.get("platform") or "",
                visibility="private_pair",
                sayability="indirect",
                reality_level="imported_summary",
                lifecycle="stable_memory",
                content=summary,
                evidence="；".join(raw.get("source_message_ids") or []),
                confidence=0.72,
                importance=max(0.0, min(1.0, float(raw.get("importance") or 0.58))),
                tags=["daily_digest", "historical_chat", date],
                metadata={
                    "date": date,
                    "timezone": "Asia/Shanghai",
                    "segment_ids": raw.get("segment_ids") or [],
                    "source_message_ids": raw.get("source_message_ids") or [],
                    "import_batch_id": batch["id"],
                    "owner_bot_id": batch["bot_id"],
                    "summary_perspective": "neutral_third_person",
                    "detail_schema_version": self.DETAIL_SCHEMA_VERSION,
                },
                occurred_at=f"{date}T00:00:00+08:00",
                source_plugin="historical_chat_import",
                import_batch_id=batch["id"],
            )
            await self.store.insert_memory(record)
        for raw in output.get("stable_facts") or []:
            if not isinstance(raw, dict):
                continue
            content = clean_text(raw.get("content"), 700)
            if not content:
                continue
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.68)))
            except Exception:
                confidence = 0.68
            record = MemoryRecord(
                id="mem_"
                + stable_fingerprint(batch["id"], "stable_fact", content)[:20],
                memory_type="stable_fact",
                subject=EntityRef(
                    kind="user", id=batch["user_id"], name=batch.get("user_name") or ""
                ),
                object=EntityRef.bot_self(
                    bot_id=batch["bot_id"], bot_name=batch.get("bot_name") or "Bot"
                ),
                scope="private",
                session_id=batch["session_id"],
                platform=batch.get("platform") or "",
                visibility="private_pair",
                sayability="direct",
                reality_level="historical_evidence",
                lifecycle="stable_memory",
                content=content,
                evidence="；".join(raw.get("source_message_ids") or []),
                confidence=confidence,
                importance=0.7,
                review_status="auto" if confidence >= 0.75 else "pending",
                tags=["stable_fact", "historical_chat"],
                metadata={
                    "valid_from": clean_text(raw.get("valid_from"), 80),
                    "segment_ids": raw.get("segment_ids") or [],
                    "source_message_ids": raw.get("source_message_ids") or [],
                    "import_batch_id": batch["id"],
                    "owner_bot_id": batch["bot_id"],
                },
                occurred_at=clean_text(raw.get("valid_from"), 80) or utc_now(),
                source_plugin="historical_chat_import",
                import_batch_id=batch["id"],
            )
            await self.store.insert_memory(record)

    async def _insert_phase_summary(
        self,
        batch: dict[str, Any],
        content: str,
        summaries: list[dict[str, Any]],
    ) -> None:
        content = clean_text(content, 1600)
        if not content or not summaries:
            return
        start_at = clean_text(summaries[0].get("start_at"), 80)
        end_at = clean_text(summaries[-1].get("end_at"), 80)
        record = MemoryRecord(
            id="mem_"
            + stable_fingerprint(batch["id"], "relationship_phase_summary", content)[
                :20
            ],
            memory_type="relationship_phase_summary",
            subject=EntityRef(
                kind="user", id=batch["user_id"], name=batch.get("user_name") or ""
            ),
            object=EntityRef.bot_self(
                bot_id=batch["bot_id"], bot_name=batch.get("bot_name") or "Bot"
            ),
            scope="private",
            session_id=batch["session_id"],
            platform=batch.get("platform") or "",
            visibility="private_pair",
            sayability="indirect",
            reality_level="imported_summary",
            lifecycle="stable_memory",
            content=content,
            confidence=0.7,
            importance=0.72,
            tags=["relationship_phase", "historical_chat", "long_term"],
            metadata={
                "start_at": start_at,
                "end_at": end_at,
                "segment_ids": [item.get("segment_id") for item in summaries],
                "import_batch_id": batch["id"],
                "owner_bot_id": batch["bot_id"],
                "timezone": "Asia/Shanghai",
            },
            occurred_at=start_at or utc_now(),
            source_plugin="historical_chat_import",
            import_batch_id=batch["id"],
        )
        await self.store.insert_memory(record)
