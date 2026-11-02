from __future__ import annotations

import re
from typing import Any

from .models import clean_text
from .turn_signal import message_terms


class MemorySummarizerRelations:
    """Relations; shared runtime resources belong to the facade."""

    def _normalize_associations(
        self,
        value: Any,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []

        row_by_id = {
            clean_text(row.get("id"), 160): row
            for row in rows
            if clean_text(row.get("id"), 160)
        }
        associations: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            raw_cue = item.get("cue")
            raw_tag = item.get("tag")
            raw_content = item.get("content")
            raw_layer = item.get("layer")
            raw_refs = (
                item.get("refs")
                or item.get("event_ids")
                or item.get("source_event_ids")
                or []
            )
            if not all(
                isinstance(field, str)
                for field in (raw_cue, raw_tag, raw_content, raw_layer)
            ):
                continue
            if any(
                self._looks_like_prompt_injection(field)
                for field in (raw_cue, raw_tag, raw_content)
            ):
                continue

            cue = clean_text(raw_cue, self.ASSOCIATION_FIELD_LIMITS["cue"])
            tag = clean_text(raw_tag, self.ASSOCIATION_FIELD_LIMITS["tag"])
            content = clean_text(raw_content, self.ASSOCIATION_FIELD_LIMITS["content"])
            layer = clean_text(
                raw_layer, self.ASSOCIATION_FIELD_LIMITS["layer"]
            ).casefold()
            if (
                not cue
                or not tag
                or not content
                or layer not in self.ASSOCIATION_LAYERS
            ):
                continue
            if isinstance(raw_refs, str):
                raw_refs = [raw_refs]
            if not isinstance(raw_refs, list):
                continue
            refs = list(
                dict.fromkeys(
                    clean_text(ref, 160)
                    for ref in raw_refs
                    if clean_text(ref, 160) in row_by_id
                )
            )[:6]
            if not refs or not self.fact_supported_by_rows(
                content, [row_by_id[ref] for ref in refs]
            ):
                continue

            cue = clean_text(
                self._normalize_relative_time_mentions(cue, rows),
                self.ASSOCIATION_FIELD_LIMITS["cue"],
            )
            content = clean_text(
                self._normalize_relative_time_mentions(content, rows),
                self.ASSOCIATION_FIELD_LIMITS["content"],
            )
            key = (cue.casefold(), tag.casefold(), content.casefold(), layer)
            if key in seen:
                continue
            seen.add(key)
            associations.append(
                {
                    "cue": cue,
                    "tag": tag,
                    "content": content,
                    "refs": refs,
                    "layer": layer,
                }
            )
            if len(associations) >= self.MAX_ASSOCIATIONS:
                break
        return associations

    def _normalize_bot_self_facts(
        self, value: Any, rows: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        bot_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            event_id = clean_text(row.get("id"), 160)
            event_type = clean_text(row.get("event_type"), 40).lower()
            subject_id = clean_text(row.get("subject_id"), 120).lower()
            if event_id and (event_type == "bot_response" or subject_id == "self"):
                bot_rows[event_id] = row

        facts: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            event_id = clean_text(
                item.get("event_id") or item.get("source_event_id"), 160
            )
            source_row = bot_rows.get(event_id)
            if source_row is None:
                continue
            raw_fact = clean_text(item.get("fact") or item.get("content"), 220)
            if len(raw_fact) < 4 or self._looks_like_prompt_injection(raw_fact):
                continue
            fact = self._normalize_relative_time_mentions(
                self._sanitize_generated_memory_text(raw_fact, 220),
                [source_row],
            )
            if not fact or not self._bot_self_fact_supported_by_evidence(
                fact, source_row.get("content")
            ):
                continue
            kind = clean_text(item.get("kind"), 24).lower()
            if kind not in {"schedule", "commitment", "action"}:
                kind = "schedule"
            key = (event_id, fact)
            if key in seen:
                continue
            seen.add(key)
            facts.append({"event_id": event_id, "fact": fact, "kind": kind})
            if len(facts) >= 4:
                break
        return facts

    @staticmethod
    def _bot_self_fact_supported_by_evidence(fact: str, evidence: Any) -> bool:
        source = re.sub(r"\s+", "", clean_text(evidence, 800)).lower()
        if not source:
            return False
        temporal_or_generic = {
            "今天",
            "明天",
            "后天",
            "今晚",
            "明早",
            "明晚",
            "上午",
            "下午",
            "晚上",
            "下周",
            "周末",
            "有事",
            "有空",
            "安排",
            "计划",
        }
        terms = [
            term
            for term in message_terms(fact, limit=60)
            if term not in temporal_or_generic
        ]
        return any(term in source for term in terms)
