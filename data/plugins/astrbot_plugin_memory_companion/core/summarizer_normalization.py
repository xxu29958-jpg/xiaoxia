from __future__ import annotations

import json
import re
from typing import Any

from .models import clean_text


class MemorySummarizerNormalization:
    """Normalization; shared runtime resources belong to the facade."""

    def _parse_response(self, text: str) -> dict[str, Any] | None:
        text = clean_text(text, self._provider_response_limit())
        if not text:
            return None
        # Providers occasionally wrap an otherwise valid response in a
        # markdown fence or a short preamble.  Decode each JSON object start
        # instead of taking the first '{' and last '}', which breaks when the
        # preamble or a trailing note contains braces.
        candidates: list[str] = []
        fenced = re.findall(
            r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL
        )
        candidates.extend(fenced)
        decoder = json.JSONDecoder()
        for start, char in enumerate(text):
            if char != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[start:])
                if isinstance(payload, dict):
                    candidates.append(json.dumps(payload, ensure_ascii=False))
            except Exception:
                continue
        for raw in candidates:
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _normalize_payload(
        self, payload: dict[str, Any], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        summary = self._sanitize_generated_memory_text(
            clean_text(payload.get("summary"), self.max_summary_chars),
            self.max_summary_chars,
        )
        summary = self._normalize_relative_time_mentions(summary, rows)
        dropped_facts: list[str] = []
        key_facts_with_refs, self_fact_warnings, self_fact_errors = (
            self._normalize_key_facts_with_validation(
                payload.get("key_facts") or payload.get("facts"),
                rows,
                drop_reasons=dropped_facts,
            )
        )
        key_facts = [item["fact"] for item in key_facts_with_refs]
        topics = self._clean_list(payload.get("topics"), 6, 80)
        associations = self._normalize_associations(payload.get("associations"), rows)
        participants = self._clean_list(payload.get("participants"), 10, 80)
        routine_check_notes = self._clean_list(
            payload.get("routine_check_notes"), 8, 180
        )
        routine_check_notes = [
            self._normalize_relative_time_mentions(
                self._sanitize_generated_memory_text(item, 180),
                rows,
            )
            for item in routine_check_notes
        ]
        bot_self_facts = self._normalize_bot_self_facts(
            payload.get("bot_self_facts"), rows
        )
        assertions, assertion_warnings = self._normalize_assertions(
            payload.get("assertions"), rows
        )
        if not participants:
            participants = self._participants_from_rows(rows)
        sentiment = clean_text(payload.get("sentiment") or "neutral", 20).lower()
        if sentiment not in {"positive", "neutral", "negative"}:
            sentiment = "neutral"
        try:
            importance = max(0.0, min(1.0, float(payload.get("importance", 0.5))))
        except Exception:
            importance = 0.5
        canonical = self._sanitize_generated_memory_text(
            clean_text(payload.get("canonical_summary"), self.max_summary_chars),
            self.max_summary_chars,
        )
        canonical = self._normalize_relative_time_mentions(canonical, rows)
        if not canonical:
            parts = [summary] if summary else []
            if key_facts:
                parts.append("；".join(key_facts))
            if routine_check_notes:
                parts.append("；".join(routine_check_notes))
            canonical = clean_text(" | ".join(parts), self.max_summary_chars)
        valid_ids = {clean_text(row.get("id"), 160) for row in rows}
        raw_refs = payload.get("summary_refs") or []
        raw_refs = [raw_refs] if isinstance(raw_refs, str) else raw_refs
        raw_refs = raw_refs if isinstance(raw_refs, list) else []
        # Normalize once, with the same helper the key_facts and associations
        # refs use. Comparing the raw string here rejected a ref that only
        # differed by whitespace even though the identical id matched in
        # key_facts, so the same batch reported "summary_refs 含本批次不存在
        # 的 event_id" for a reference that was in fact valid.
        normalized_refs = [clean_text(ref, 160) for ref in raw_refs]
        refs = list(dict.fromkeys(ref for ref in normalized_refs if ref in valid_ids))
        # ``_validation_errors`` is the hard contract: a payload that violates it
        # is retried and finally frozen, so it may only carry failures the batch
        # genuinely cannot be saved from.  Citation softness lives in
        # ``_validation_warnings`` and downgrades the memory to an evidence
        # candidate instead of discarding the conversation.
        errors: list[str] = list(self_fact_errors)
        warnings: list[str] = list(self_fact_warnings) + list(assertion_warnings)
        raw_facts = payload.get("key_facts") or payload.get("facts") or []
        raw_facts = raw_facts if isinstance(raw_facts, list) else [raw_facts]
        if len(raw_facts) > len(key_facts_with_refs):
            detail = "；".join(dict.fromkeys(dropped_facts))[:200]
            warnings.append(
                "部分关键事实没有有效引用或不受原文支持，已从本批事实中剔除"
                + (f"（{detail}）" if detail else "")
            )
        if any(ref not in valid_ids for ref in normalized_refs):
            errors.append("summary_refs 含本批次不存在的 event_id")
        # A body with no grounding in the window at all is a fabrication and
        # stays a contract failure, which is what the repair round exists for.
        # Covering more messages than it happened to cite is only an attribution
        # weakness: grounding the body against the cited rows alone rejected
        # every summary wider than its own references.
        if summary and self.citation_check(summary, rows)[0] == "unsupported":
            errors.append("摘要正文与本批原文缺乏对应，请贴近原文纠正")
            warnings.append("摘要正文与所引用消息的词面对应较弱")
        elif refs and summary:
            cited = [row for row in rows if clean_text(row.get("id"), 160) in refs]
            if cited and self.citation_check(summary, cited)[0] == "unsupported":
                warnings.append("摘要正文与所引用消息缺乏对应")
        if any(
            term in summary
            for term in (
                "某用户",
                "某人",
                "有人",
                "用户说",
                "对方说",
                "群成员",
                "某群成员",
            )
        ):
            warnings.append("摘要正文含泛指人物，建议改用原文昵称或稳定 ID")
        if payload.get("outcome") == "no_memory" and set(refs) != valid_ids:
            errors.append("no_memory 需要确认本次所有已阅读消息均无新增记忆价值")
        payload["_validation_errors"] = errors
        payload["_quality_warnings"] = list(dict.fromkeys(warnings))
        payload["_validation_warnings"] = list(dict.fromkeys(warnings))
        payload["summary_refs"] = refs
        payload["no_memory_reason"] = clean_text(payload.get("no_memory_reason"), 500)
        payload.update(
            {
                "summary": summary,
                "persona_summary": self._normalize_relative_time_mentions(
                    self._sanitize_generated_memory_text(
                        clean_text(
                            payload.get("persona_summary") or summary,
                            self.max_summary_chars,
                        ),
                        self.max_summary_chars,
                    ),
                    rows,
                ),
                "canonical_summary": canonical,
                "topics": topics,
                "key_facts": key_facts,
                "key_facts_with_refs": key_facts_with_refs,
                "associations": associations,
                "routine_check_notes": routine_check_notes,
                "bot_self_facts": bot_self_facts,
                "assertions": assertions,
                "participants": participants,
                "sentiment": sentiment,
                "importance": importance,
            }
        )
        return payload

    def _normalize_key_facts(
        self,
        value: Any,
        rows: list[dict[str, Any]],
        *,
        drop_reasons: list[str] | None = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Keep the legacy audit API while preserving quote-backed facts."""
        items = [value] if isinstance(value, (str, dict)) else value
        if not isinstance(items, list):
            return [], []
        traced, _warnings, _errors = self._normalize_key_facts_with_validation(
            [item for item in items if isinstance(item, dict)],
            rows,
            drop_reasons=drop_reasons,
        )
        supported = {
            item["fact"]: item
            for item in traced
            if self.trace_supported_by_rows(item, rows)
        }
        facts: list[str] = []
        kept: list[dict[str, Any]] = []
        for item in items:
            raw = (
                (item.get("fact") or item.get("text") or item.get("content"))
                if isinstance(item, dict)
                else item
            )
            fact = self._normalize_relative_time_mentions(
                self._sanitize_generated_memory_text(clean_text(raw, 160), 160),
                rows,
            )
            if (
                len(fact) < 2
                or self._looks_like_prompt_injection(fact)
                or fact in facts
            ):
                continue
            if isinstance(item, dict):
                if fact not in supported:
                    continue
                kept.append(supported[fact])
            facts.append(fact)
            if len(facts) >= 8:
                break
        return facts, kept
