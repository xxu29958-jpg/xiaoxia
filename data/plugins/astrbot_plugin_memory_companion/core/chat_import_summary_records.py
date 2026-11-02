from __future__ import annotations

import json
from typing import Any

from .models import (
    EntityRef,
    MemoryRecord,
    clean_text,
    stable_fingerprint,
)


class HistoricalChatImporterSummaryRecords:
    """Summary records; shared runtime resources belong to the facade."""

    def _normalize_segment_result(
        self, result: dict[str, Any], segment: dict[str, Any]
    ) -> dict[str, Any]:
        valid_ids = {str(item) for item in segment.get("message_ids") or []}

        def source_ids(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [
                clean_text(item, 120)
                for item in value
                if clean_text(item, 120) in valid_ids
            ][:16]

        def clamp(value: Any, default: float) -> float:
            try:
                return max(0.0, min(1.0, float(value)))
            except Exception:
                return default

        events: list[dict[str, Any]] = []
        for raw in result.get("important_events") or []:
            if not isinstance(raw, dict):
                continue
            evidence = source_ids(raw.get("source_message_ids"))
            content = clean_text(raw.get("content"), 700)
            if not content or not evidence:
                continue
            status = clean_text(raw.get("status"), 30).lower()
            if status not in {
                "planned",
                "confirmed",
                "ongoing",
                "completed",
                "cancelled",
                "corrected",
            }:
                status = "completed"
            events.append(
                {
                    "title": clean_text(raw.get("title"), 100) or "重要事件",
                    "event_type": clean_text(raw.get("event_type"), 40) or "action",
                    "actor": clean_text(raw.get("actor"), 80),
                    "object": clean_text(raw.get("object"), 80),
                    "content": content,
                    "start_at": clean_text(raw.get("start_at"), 80)
                    or segment["start_at"],
                    "end_at": clean_text(raw.get("end_at"), 80),
                    "status": status,
                    "thread_key": clean_text(raw.get("thread_key"), 120),
                    "importance": clamp(raw.get("importance"), 0.75),
                    "confidence": clamp(raw.get("confidence"), 0.72),
                    "source_message_ids": evidence,
                }
            )
        observations: list[dict[str, Any]] = []
        for raw in result.get("relationship_observations") or []:
            if not isinstance(raw, dict):
                continue
            evidence = source_ids(raw.get("source_message_ids"))
            content = clean_text(raw.get("content"), 500)
            if content and evidence:
                observations.append(
                    {
                        "title": clean_text(raw.get("title"), 80) or "历史关系观察",
                        "content": content,
                        "observed_at": clean_text(raw.get("observed_at"), 80)
                        or segment["start_at"],
                        "confidence": clamp(raw.get("confidence"), 0.6),
                        "source_message_ids": evidence,
                    }
                )
        facts: list[dict[str, Any]] = []
        for raw in result.get("stable_facts") or []:
            if not isinstance(raw, dict):
                continue
            evidence = source_ids(raw.get("source_message_ids"))
            content = clean_text(raw.get("content"), 500)
            if content and evidence:
                facts.append(
                    {
                        "content": content,
                        "valid_from": clean_text(raw.get("valid_from"), 80)
                        or segment["start_at"],
                        "confidence": clamp(raw.get("confidence"), 0.68),
                        "source_message_ids": evidence,
                    }
                )
        return {
            "worth_long_term": bool(result.get("worth_long_term", True)),
            "summary": clean_text(result.get("summary"), 1200),
            "canonical_summary": clean_text(result.get("canonical_summary"), 1600),
            "archive_note": clean_text(result.get("archive_note"), 500),
            "topics": [
                clean_text(item, 80)
                for item in (result.get("topics") or [])
                if clean_text(item, 80)
            ][:8],
            "importance": clamp(result.get("importance"), 0.62),
            "confidence": clamp(result.get("confidence"), 0.72),
            "important_events": events[:8],
            "stable_facts": facts[:8],
            "relationship_observations": observations[:8],
        }

    def _summary_record(
        self,
        batch: dict[str, Any],
        segment: dict[str, Any],
        result: dict[str, Any],
    ) -> MemoryRecord:
        evidence_lines = []
        for line in str(segment.get("transcript") or "").splitlines()[:12]:
            try:
                row = json.loads(line)
                evidence_lines.append(
                    f"{clean_text(row.get('time'), 40)} {clean_text(row.get('speaker'), 40)}: {clean_text(row.get('text'), 220)}"
                )
            except Exception:
                continue
        narrative_summary = clean_text(result.get("summary"), 1200)
        canonical_summary = clean_text(result.get("canonical_summary"), 1600)
        neutral_content = self._neutral_detailed_summary(
            narrative_summary, canonical_summary
        )
        return MemoryRecord(
            id="mem_"
            + stable_fingerprint(batch["id"], segment["id"], neutral_content)[:20],
            memory_type="conversation_summary",
            subject=EntityRef(
                kind="user",
                id=batch["user_id"],
                name=batch.get("user_name") or "",
                role="conversation_partner",
            ),
            object=EntityRef.bot_self(
                bot_id=batch["bot_id"], bot_name=batch.get("bot_name") or "Bot"
            ),
            scope="private",
            session_id=batch["session_id"],
            platform=batch.get("platform") or "",
            visibility="private_pair",
            sayability="direct",
            reality_level="imported_summary",
            lifecycle="stable_memory",
            content=neutral_content,
            evidence="\n".join(evidence_lines),
            confidence=result["confidence"],
            importance=result["importance"],
            review_status="auto",
            tags=["summary", "historical_chat", "long_term", *result["topics"][:5]],
            metadata={
                "canonical_summary": canonical_summary,
                "source_narrative_summary": narrative_summary
                if narrative_summary != neutral_content
                else "",
                "summary_perspective": "neutral_third_person",
                "detail_schema_version": self.DETAIL_SCHEMA_VERSION,
                "start_at": segment["start_at"],
                "end_at": segment["end_at"],
                "timezone": "Asia/Shanghai",
                "source_message_ids": segment.get("message_ids") or [],
                "summary_event_count": len(segment.get("message_ids") or []),
                "segment_id": segment["id"],
                "import_batch_id": batch["id"],
                "owner_bot_id": batch["bot_id"],
                "historical_archive": True,
            },
            occurred_at=segment["start_at"],
            source_plugin="historical_chat_import",
            import_batch_id=batch["id"],
        )
