from __future__ import annotations

import re
from typing import Any

from .models import (
    clean_text,
)


class HistoricalChatImporterReconciliation:
    """Reconciliation; shared runtime resources belong to the facade."""

    @staticmethod
    def _dedupe_relationship_observations(
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        selected: dict[str, dict[str, Any]] = {}
        for raw in observations:
            if not isinstance(raw, dict):
                continue
            content = clean_text(raw.get("content"), 500)
            evidence = [
                clean_text(item, 120)
                for item in (raw.get("source_message_ids") or [])
                if clean_text(item, 120)
            ][:16]
            if not content or not evidence:
                continue
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.6)))
            except Exception:
                confidence = 0.6
            key = re.sub(r"[\s，。！？、,.!?;；:：]+", "", content).lower()
            candidate = {
                "title": clean_text(raw.get("title"), 80) or "历史关系观察",
                "content": content,
                "observed_at": clean_text(raw.get("observed_at"), 80),
                "confidence": confidence,
                "source_message_ids": list(dict.fromkeys(evidence)),
            }
            previous = selected.get(key)
            if previous is None or confidence > float(
                previous.get("confidence") or 0.0
            ):
                selected[key] = candidate
        return sorted(
            selected.values(),
            key=lambda item: (
                -float(item.get("confidence") or 0.0),
                str(item.get("observed_at") or ""),
            ),
        )[:24]

    @staticmethod
    def _normalize_reconcile_output(
        output: dict[str, Any],
        chunk: list[dict[str, Any]],
        segment_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        allowed_ids = {clean_text(item.get("segment_id"), 120) for item in chunk}
        allowed_dates = {clean_text(item.get("date"), 20) for item in chunk}

        def segment_ids(raw: Any) -> list[str]:
            if not isinstance(raw, list):
                return []
            return list(
                dict.fromkeys(
                    clean_text(item, 120)
                    for item in raw
                    if clean_text(item, 120) in allowed_ids
                )
            )[:16]

        def evidence(ids: list[str]) -> list[str]:
            return list(
                dict.fromkeys(
                    clean_text(message_id, 120)
                    for segment_id in ids
                    for message_id in (
                        segment_by_id.get(segment_id, {}).get("message_ids") or []
                    )
                    if clean_text(message_id, 120)
                )
            )[:32]

        daily: list[dict[str, Any]] = []
        for raw in output.get("daily_digests") or []:
            if not isinstance(raw, dict):
                continue
            date = clean_text(raw.get("date"), 20)
            summary = clean_text(raw.get("summary"), 1800)
            if date not in allowed_dates or not summary:
                continue
            ids = [item["segment_id"] for item in chunk if item.get("date") == date]
            try:
                importance = max(0.0, min(1.0, float(raw.get("importance") or 0.58)))
            except Exception:
                importance = 0.58
            daily.append(
                {
                    "date": date,
                    "summary": summary,
                    "importance": importance,
                    "segment_ids": ids,
                    "source_message_ids": evidence(ids),
                }
            )

        facts: list[dict[str, Any]] = []
        for raw in output.get("stable_facts") or []:
            if not isinstance(raw, dict):
                continue
            ids = segment_ids(raw.get("segment_ids"))
            content = clean_text(raw.get("content"), 700)
            source_ids = evidence(ids)
            if not content or not ids or not source_ids:
                continue
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.68)))
            except Exception:
                confidence = 0.68
            known_times = [
                clean_text(segment_by_id.get(item, {}).get("start_at"), 80)
                for item in ids
                if clean_text(segment_by_id.get(item, {}).get("start_at"), 80)
            ]
            fallback_time = min(known_times, default="")
            facts.append(
                {
                    "content": content,
                    "valid_from": clean_text(raw.get("valid_from"), 80)
                    or fallback_time,
                    "confidence": confidence,
                    "segment_ids": ids,
                    "source_message_ids": source_ids,
                }
            )

        relationships: list[dict[str, Any]] = []
        for raw in output.get("relationship_observations") or []:
            if not isinstance(raw, dict):
                continue
            ids = segment_ids(raw.get("segment_ids"))
            content = clean_text(raw.get("content"), 500)
            source_ids = evidence(ids)
            if not content or not ids or not source_ids:
                continue
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.6)))
            except Exception:
                confidence = 0.6
            known_times = [
                clean_text(segment_by_id.get(item, {}).get("start_at"), 80)
                for item in ids
                if clean_text(segment_by_id.get(item, {}).get("start_at"), 80)
            ]
            fallback_time = min(known_times, default="")
            relationships.append(
                {
                    "title": clean_text(raw.get("title"), 80) or "历史关系观察",
                    "content": content,
                    "observed_at": clean_text(raw.get("observed_at"), 80)
                    or fallback_time,
                    "confidence": confidence,
                    "segment_ids": ids,
                    "source_message_ids": source_ids,
                }
            )
        daily_by_date: dict[str, dict[str, Any]] = {}
        for item in daily:
            previous = daily_by_date.get(item["date"])
            if previous is None or len(item["summary"]) > len(previous["summary"]):
                daily_by_date[item["date"]] = item
        return {
            "daily_digests": list(daily_by_date.values()),
            "stable_facts": facts,
            "relationship_observations": relationships,
            "phase_summary": clean_text(output.get("phase_summary"), 1200),
        }
