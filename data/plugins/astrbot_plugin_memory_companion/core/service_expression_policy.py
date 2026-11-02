from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .models import (
    MemoryRecord,
    SearchResult,
    SessionContext,
    clean_text,
)
from .service_support import (
    LOCAL_TZ,
    MemoryRouteDecision,
)
from .time_intent import TimeIntent


class MemoryCompanionServiceExpressionPolicy:
    """service expression policy; the facade owns all shared resources."""

    def _apply_memory_expression_policy(
        self,
        ctx: SessionContext,
        slot_map: dict[str, list[Any]],
        decision: MemoryRouteDecision,
        time_intent: TimeIntent,
        *,
        query_text: str = "",
    ) -> dict[str, list[Any]]:
        if not slot_map:
            return slot_map
        result: dict[str, list[Any]] = {}
        for slot, items in slot_map.items():
            annotated: list[Any] = []
            for item in items or []:
                memory = getattr(item, "memory", None)
                if memory is None:
                    annotated.append(item)
                    continue
                expression, reason = self._memory_expression_decision(
                    ctx,
                    memory,
                    item,
                    slot,
                    decision,
                    time_intent,
                    query_text=query_text,
                )
                annotated.append(
                    SearchResult(
                        memory=memory,
                        score=float(getattr(item, "score", 0.0) or 0.0),
                        reason=self._append_expression_reason(
                            getattr(item, "reason", ""), expression, reason
                        ),
                    )
                )
            result[slot] = annotated
        return result

    def _memory_expression_decision(
        self,
        ctx: SessionContext,
        memory: MemoryRecord,
        item: Any,
        slot: str,
        decision: MemoryRouteDecision,
        time_intent: TimeIntent,
        *,
        query_text: str = "",
    ) -> tuple[str, str]:
        memory_type = clean_text(memory.memory_type, 80).lower()
        reality = clean_text(memory.reality_level, 80).lower()
        tags = {clean_text(tag, 80).lower() for tag in (memory.tags or [])}
        confidence = float(getattr(memory, "confidence", 0.0) or 0.0)
        score = float(getattr(item, "score", 0.0) or 0.0)
        age_days = self._memory_age_days(memory)

        if confidence < 0.5:
            return "uncertain", "low_confidence"
        # 总闸：关闭 tone 表达抽象后，跳过后续全部判定，所有记忆直接以正文注入
        # （candidate）。默认开启保持作者逻辑；关闭后 conversation_summary 等
        # 记忆主力不再被折叠成「语气提示」占位，模型可直接引用正文。
        if not self.config.bool("memory_injection.enable_tone_abstraction", True):
            return ("candidate", "tone_abstraction_disabled")
        text = clean_text(query_text or ctx.message_text, 800)
        explicit_memory = self._message_is_contextual_memory_request(
            text
        ) or self._message_requests_temporal_aggregate(text)
        companion_cap_reason = self._companion_memory_mention_cap(
            ctx, explicit_memory=explicit_memory
        )
        if companion_cap_reason:
            return "tone", companion_cap_reason
        acl_authorized_for_sender = self._acl_authorized_private_group_result(
            ctx,
            memory,
            item,
        )
        short_rest_check = self._message_is_short_rest_check(text)
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        mention_policy = clean_text(metadata.get("mention_policy"), 60)
        try:
            mentionability = float(metadata.get("mentionability_score", 0.5) or 0.5)
        except Exception:
            mentionability = 0.5
        if decision.layer == "future_arrangement_chat":
            if self._memory_is_authoritative_bot_arrangement(memory, slot, age_days):
                return "mention", "future_arrangement:bot_self_evidence"
            if memory.scope == "group" or memory.visibility == "group_public":
                return "uncertain", "future_arrangement:group_background"
            if (
                memory_type in {"conversation_summary", "timeline_event"}
                or "summary" in tags
            ):
                return (
                    "uncertain",
                    "future_arrangement:summary_requires_direct_evidence",
                )
            return "tone", "future_arrangement:indirect_background"
        if time_intent.active or decision.layer == "time_window":
            return "mention", "time_window_requested"
        if acl_authorized_for_sender and not explicit_memory:
            if mention_policy == "tone_only":
                return "tone", "mention_policy:tone_only"
            return "candidate", "acl_authorized_private_candidate"
        if not explicit_memory and mention_policy == "avoid_unless_asked":
            return "tone", "mention_policy:avoid_unless_asked"
        if not explicit_memory and mention_policy == "tone_only":
            return "tone", "mention_policy:tone_only"
        if not explicit_memory and mentionability <= 0.25:
            return "tone", "user_reaction_boundary:low_mentionability"
        if decision.layer == "shared_routine":
            if self._memory_supports_shared_routine(memory, text):
                return "mention", "shared_routine:direct_evidence"
            return "tone", "shared_routine:indirect_background"
        if slot == "open_loop":
            if short_rest_check and not explicit_memory:
                return "tone", "short_rest_check:open_loop_tone_only"
            if confidence < 0.58:
                return "uncertain", "open_loop_low_confidence"
            if mention_policy == "avoid_unless_asked" and not explicit_memory:
                return "tone", "open_loop_avoid_unless_asked"
            if mention_policy == "tone_only" and not explicit_memory:
                return "tone", "open_loop_tone_only"
            return "mention", "open_loop_or_emotional_debt"
        if decision.layer in {
            "recent_context",
            "current_correction",
            "low_information",
            "short_context_followup",
        }:
            return "tone", f"route_layer:{decision.layer}"
        if decision.layer == "current_state_chat":
            return "mention", "current_state_recent_context"

        if confidence < 0.58 or (
            age_days is not None and age_days >= 45 and score < 1.15
        ):
            return "uncertain", "low_confidence_or_old"
        if (
            ctx.scope == "group"
            and slot in {"self_timeline", "user_profile"}
            and not explicit_memory
            and not acl_authorized_for_sender
        ):
            return "tone", "group_boundary"
        if (
            memory.visibility == "bot_self"
            or slot == "self_timeline"
            or reality in {"bot_action", "persona_life", "fictional_content"}
        ):
            if self._memory_has_long_term_explanatory_value(memory, metadata) and (
                explicit_memory or score >= 1.2
            ):
                return "mention", "self_memory_with_long_term_explanation"
            return (
                ("mention", "explicit_self_memory_requested")
                if explicit_memory
                else ("tone", "self_timeline_background")
            )
        if (
            memory_type in {"conversation_summary", "timeline_event"}
            or "summary" in tags
        ):
            if short_rest_check and not explicit_memory:
                return "tone", "short_rest_check:summary_tone_only"
            if (
                explicit_memory
                or acl_authorized_for_sender
                or slot == "time_window_timeline"
            ):
                return "mention", "summary_requested"
            return "tone", "conversation_continuity_background"
        if memory_type in {
            "user_profile",
            "user_preference",
            "relationship_claim",
            "explicit_memory",
            "manual_memory",
        }:
            if mention_policy == "tone_only" and not explicit_memory:
                return "tone", "mention_policy:tone_only"
            if mention_policy == "soft_echo" and not explicit_memory and score < 1.0:
                return "tone", "mention_policy:soft_echo_background"
            if explicit_memory or acl_authorized_for_sender or score >= 1.0:
                return "mention", "stable_user_fact"
            return "tone", "profile_background"
        if (
            memory.sayability == "indirect"
            and not explicit_memory
            and not acl_authorized_for_sender
        ):
            return "tone", "indirect_memory"
        return "mention", "direct_relevance"

    @staticmethod
    def _companion_memory_mention_cap(
        ctx: SessionContext, *, explicit_memory: bool
    ) -> str:
        """Return a reason when Companion limits unsolicited memory expression."""

        if (
            explicit_memory
            or ctx.scope != "private"
            or not ctx.companion_expression_contract
        ):
            return ""
        if ctx.companion_expression_blocker:
            return "companion_expression:blocker"
        if ctx.companion_expression_safety_mode != "normal":
            return f"companion_expression:safety_{ctx.companion_expression_safety_mode}"
        if ctx.companion_expression_band in {"avoidant", "hurt"}:
            return f"companion_expression:band_{ctx.companion_expression_band}"
        mention_capable = {"support", "followup", "shared_ritual"}
        if not mention_capable.intersection(ctx.companion_expression_allowed_behaviors):
            return "companion_expression:behavior_cap"
        return ""

    def _memory_age_days(self, memory: MemoryRecord) -> float | None:
        timestamp = clean_text(
            memory.occurred_at or memory.updated_at or memory.created_at, 80
        )
        parsed = self._parse_utc_datetime(timestamp)
        if parsed is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)

    @staticmethod
    def _memory_is_authoritative_bot_arrangement(
        memory: MemoryRecord, slot: str, age_days: float | None
    ) -> bool:
        memory_type = clean_text(memory.memory_type, 80).lower()
        reality = clean_text(memory.reality_level, 80).lower()
        subject_kind = clean_text(getattr(memory.subject, "kind", ""), 40).lower()
        if memory.visibility == "bot_self":
            if memory_type in {
                "schedule_fragment",
                "self_action",
                "proactive_message",
                "companion_note",
            }:
                return age_days is None or age_days <= 3.0
            return reality in {"persona_life", "bot_action", "fictional_content"}
        if subject_kind != "bot":
            return False
        if slot == "self_timeline" and memory_type in {
            "schedule_fragment",
            "persona_life",
            "self_action",
            "companion_note",
        }:
            return age_days is None or age_days <= 3.0 or memory_type == "persona_life"
        return reality in {"persona_life", "bot_action"} and memory_type in {
            "persona_life",
            "schedule_fragment",
            "self_action",
            "companion_note",
        }

    @staticmethod
    def _message_is_short_rest_check(text: str) -> bool:
        text = clean_text(text, 80)
        if not text or len(text) > 20:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", text)
        if not compact:
            return False
        check_like = compact in {
            "查岗",
            "查岗了",
            "在吗",
            "在不在",
            "还在吗",
            "睡了吗",
            "睡没",
            "醒着吗",
        } or any(word in compact for word in ("查岗", "在不在", "还在吗", "醒着吗"))
        if not check_like:
            return False
        try:
            hour = datetime.now(LOCAL_TZ).hour
        except Exception:
            return True
        return hour >= 23 or hour < 7

    @staticmethod
    def _message_is_shared_routine_invocation(text: str) -> bool:
        text = clean_text(text, 80)
        if not text or len(text) > 24:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", text)
        if not compact:
            return False
        markers = ("例行检查", "日常检查", "每日检查", "晚间检查", "夜间检查")
        prefixes = (
            "开始",
            "来",
            "继续",
            "进行",
            "该",
            "那",
            "那么",
            "那就",
            "嗯",
            "嗯那",
            "嗯那就",
            "好",
            "好吧",
            "好那就",
        )
        suffixes = ("啦", "咯", "了", "开始", "时间", "时间到", "一下")
        variants = set(markers)
        for marker in markers:
            variants.update(f"{prefix}{marker}" for prefix in prefixes)
            variants.update(f"{marker}{suffix}" for suffix in suffixes)
            variants.update(
                f"{prefix}{marker}{suffix}"
                for prefix in prefixes
                for suffix in suffixes
            )
        return compact in variants

    @staticmethod
    def _memory_supports_shared_routine(memory: MemoryRecord, query_text: str) -> bool:
        if not MemoryCompanionServiceExpressionPolicy._message_is_shared_routine_invocation(
            query_text
        ):
            return False
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        metadata_parts: list[str] = []
        for key in ("canonical_summary", "memory_reason"):
            value = clean_text(metadata.get(key), 1000)
            if value:
                metadata_parts.append(value)
        for key in ("key_facts", "routine_check_notes"):
            values = metadata.get(key)
            if isinstance(values, list):
                metadata_parts.extend(
                    clean_text(value, 500) for value in values if clean_text(value, 500)
                )
        evidence = " ".join(
            [
                clean_text(memory.content, 3000),
                clean_text(memory.evidence, 1500),
                *metadata_parts,
            ]
        )
        return any(
            marker in evidence
            for marker in ("例行检查", "日常检查", "每日检查", "晚间检查", "夜间检查")
        )

    @staticmethod
    def _append_expression_reason(
        reason: Any, expression: str, expression_reason: str
    ) -> str:
        base = clean_text(reason, 1200)
        cleaned_expression = clean_text(expression, 40) or "mention"
        cleaned_reason = clean_text(expression_reason, 120)
        parts = [
            part
            for part in base.split(";")
            if part
            and not part.startswith("expression=")
            and not part.startswith("expression_reason=")
        ]
        parts.append(f"expression={cleaned_expression}")
        if cleaned_reason:
            parts.append(f"expression_reason={cleaned_reason}")
        return ";".join(parts)

    @staticmethod
    def _expression_from_reason(reason: Any) -> str:
        match = re.search(r"(?:^|;)expression=([^;]+)", clean_text(reason, 1200))
        return clean_text(match.group(1), 40) if match else "mention"
