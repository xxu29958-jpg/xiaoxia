from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .astrbot_compat import (
    logger,
    sanitize_request_history,
)
from .models import (
    MemoryRecord,
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceCompanionDedupe:
    """Reconcile recalled context with the companion current-state owner. Shared resources remain owned by the root service/store."""

    def _sanitize_request_history_for_companion(
        self, ctx: SessionContext, req: Any
    ) -> None:
        sanitized = sanitize_request_history(
            req,
            clean_proactive_guidance=self.config.bool(
                "private_companion_bridge.clean_proactive_history", True
            ),
        )
        if int(sanitized.get("removed", 0) or 0) or int(
            sanitized.get("cleaned", 0) or 0
        ):
            logger.info(
                "[MemoryCompanion] 已清理陪伴主动消息历史残留: session=%s removed=%s cleaned=%s before=%s after=%s",
                ctx.session_id,
                sanitized.get("removed"),
                sanitized.get("cleaned"),
                sanitized.get("before"),
                sanitized.get("after"),
            )

    def companion_coordination_status(self) -> dict[str, Any]:
        bridge_enabled = self.config.bool("private_companion_bridge.enabled", True)
        return {
            "available": True,
            "state": "ready" if bridge_enabled else "local_only",
            "degraded": False,
            "schedule_fast_context": bridge_enabled
            and self.config.bool(
                "private_companion_bridge.schedule_fast_context_enabled",
                True,
            ),
            "outfit_fast_context": bridge_enabled
            and self.config.bool(
                "private_companion_bridge.outfit_fast_context_enabled",
                True,
            ),
            "bridge_enabled": bridge_enabled,
            "memory_injection_enabled": self.config.bool(
                "memory_injection.enabled", True
            ),
            "dedupe_prompt_context": self.config.bool(
                "private_companion_bridge.dedupe_prompt_context", True
            ),
            "prefer_memory_companion_memory": self.config.bool(
                "private_companion_bridge.prefer_memory_companion_memory", True
            ),
            "clean_proactive_history": self.config.bool(
                "private_companion_bridge.clean_proactive_history", True
            ),
            "suppress_self_timeline_when_companion_seen": self.config.bool(
                "private_companion_bridge.suppress_self_timeline_when_companion_seen",
                True,
            ),
            "suppress_user_context_when_companion_seen": self.config.bool(
                "private_companion_bridge.suppress_user_context_when_companion_seen",
                True,
            ),
        }

    def should_private_companion_defer_section(self, section: str) -> bool:
        if not self.config.bool("memory_injection.enabled", True):
            return False
        if not self.config.bool("private_companion_bridge.dedupe_prompt_context", True):
            return False
        normalized = clean_text(section, 80)
        if normalized in {"self_timeline", "private_context", "livingmemory_guidance"}:
            return self.config.bool(
                "private_companion_bridge.prefer_memory_companion_memory", True
            )
        if normalized in {"companion_memory", "dialogue_history"}:
            return self.config.bool(
                "private_companion_bridge.prefer_memory_companion_memory", True
            )
        return False

    def _companion_deferred_sections(self, event: Any, req: Any) -> set[str]:
        sections: set[str] = set()
        for target in (event, req):
            if target is None:
                continue
            for attr in (
                "memory_companion_companion_deferred_sections",
                "_memory_companion_companion_deferred_sections",
            ):
                raw = getattr(target, attr, None)
                if isinstance(raw, str):
                    sections.update(
                        clean_text(part, 80)
                        for part in raw.split(",")
                        if clean_text(part, 80)
                    )
                elif isinstance(raw, (list, tuple, set)):
                    sections.update(
                        clean_text(part, 80) for part in raw if clean_text(part, 80)
                    )
        return sections

    def _companion_memory_context_present(
        self, state: dict[str, Any], deferred_sections: set[str]
    ) -> bool:
        if not self.config.bool("private_companion_bridge.dedupe_prompt_context", True):
            return False
        if not bool(state.get("has_any")) and not deferred_sections:
            return False
        state_memory_deferred = bool(
            {"private_context", "companion_memory", "dialogue_history"}
            & deferred_sections
        )
        self_timeline_deferred = "self_timeline" in deferred_sections
        return bool(
            (state.get("has_state") and not state_memory_deferred)
            or state.get("has_group_context")
            or (state.get("has_self_timeline") and not self_timeline_deferred)
            or state.get("has_recall_query")
        )

    def _apply_companion_dedupe(
        self,
        slot_map: dict[str, list[Any]],
        companion_state: dict[str, Any],
        companion_memory_present: bool,
        companion_deferred: set[str],
        blocked: list[dict[str, str]],
    ) -> tuple[dict[str, list[Any]], list[Any]]:
        """Apply companion-context dedup in one step, returning updated slot_map and flattened results."""
        results: list[Any] = []
        slot_map, companion_current_reasons = (
            self._filter_companion_current_state_overlap(
                slot_map,
                companion_state,
                companion_memory_present,
            )
        )
        if companion_current_reasons:
            blocked.extend(
                {"id": "", "reason": reason, "content": ""}
                for reason in companion_current_reasons
            )
            results = self._flatten_slot_map(slot_map)
        slot_map, slot_dedupe_reasons = self._dedupe_slots_for_companion(
            slot_map,
            companion_state,
            companion_memory_present,
            companion_deferred,
        )
        if slot_dedupe_reasons:
            blocked.extend(
                {"id": "", "reason": reason, "content": ""}
                for reason in slot_dedupe_reasons
            )
            results = self._flatten_slot_map(slot_map)
        return slot_map, results

    def _dedupe_slots_for_companion(
        self,
        slot_map: dict[str, list[Any]],
        companion_state: dict[str, Any],
        companion_memory_present: bool,
        deferred_sections: set[str] | None = None,
    ) -> tuple[dict[str, list[Any]], list[str]]:
        if not companion_memory_present:
            return slot_map, []
        deferred_sections = set(deferred_sections or set())
        cleaned = {key: list(value or []) for key, value in slot_map.items()}
        reasons: list[str] = []

        def drop(slot: str, reason: str) -> None:
            items = cleaned.get(slot) or []
            if not items:
                return
            cleaned[slot] = []
            reasons.append(reason)

        if self.config.bool(
            "private_companion_bridge.suppress_self_timeline_when_companion_seen", True
        ):
            if companion_state.get("has_self_timeline") or companion_state.get(
                "has_state"
            ):
                drop(
                    "self_timeline",
                    "companion_context_detected:self_timeline_slot_suppressed",
                )
        if self.config.bool(
            "private_companion_bridge.suppress_user_context_when_companion_seen", True
        ):
            private_context_deferred = bool(
                {
                    "private_context",
                    "companion_memory",
                    "dialogue_history",
                    "livingmemory_guidance",
                }
                & deferred_sections
            )
            if (
                companion_state.get("has_state")
                and not private_context_deferred
                and not self._memory_companion_state_is_passive_only(companion_state)
            ):
                drop(
                    "user_profile",
                    "companion_context_detected:user_profile_slot_suppressed",
                )
        return cleaned, reasons

    def _filter_companion_current_state_overlap(
        self,
        slot_map: dict[str, list[Any]],
        companion_state: dict[str, Any],
        companion_memory_present: bool,
    ) -> tuple[dict[str, list[Any]], list[str]]:
        if not companion_memory_present or not bool(companion_state.get("has_state")):
            return slot_map, []
        cleaned: dict[str, list[Any]] = {}
        dropped_by_slot: Counter[str] = Counter()
        for slot, items in (slot_map or {}).items():
            kept = []
            for item in items or []:
                memory = getattr(item, "memory", None)
                if memory is not None and self._memory_repeats_companion_current_state(
                    memory
                ):
                    dropped_by_slot[slot] += 1
                    continue
                kept.append(item)
            cleaned[slot] = kept
        if not dropped_by_slot:
            return slot_map, []
        reasons = [
            f"companion_current_state_overlap:{slot}:dropped={count}"
            for slot, count in sorted(dropped_by_slot.items())
        ]
        return cleaned, reasons

    def _memory_repeats_companion_current_state(self, memory: MemoryRecord) -> bool:
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        if self._memory_has_long_term_explanatory_value(memory, metadata):
            return False
        memory_type = clean_text(memory.memory_type, 80).lower()
        lifecycle = clean_text(memory.lifecycle, 80).lower()
        reality = clean_text(memory.reality_level, 80).lower()
        tags = {clean_text(tag, 80).lower() for tag in (memory.tags or [])}
        content = re.sub(
            r"\s+",
            "",
            " ".join(
                clean_text(value, 1000)
                for value in (
                    memory.content,
                    memory.evidence,
                    " ".join(memory.tags or []),
                    clean_text(metadata.get("canonical_summary"), 1000),
                    clean_text(metadata.get("persona_summary"), 1000),
                )
                if clean_text(value, 1000)
            ),
        ).lower()
        current_markers = (
            "当前状态",
            "情绪底色",
            "当前情绪",
            "当前日程",
            "今日状态",
            "今天的日程",
            "这会儿",
            "正在",
            "睡着",
            "休息",
            "起床",
            "吃饭",
            "天气",
        )
        current_type = (
            memory_type
            in {
                "schedule_fragment",
                "persona_life",
                "proactive_message",
                "self_action",
                "timeline_event",
            }
            or reality in {"bot_action", "persona_life"}
            or bool(tags & {"current_state", "self_timeline", "today", "recent"})
            or lifecycle == "raw_event"
        )
        if not current_type:
            return False
        age_days = self._memory_age_days(memory)
        recent = age_days is None or age_days <= 2.0
        return recent and any(marker in content for marker in current_markers)

    def _memory_has_long_term_explanatory_value(
        self, memory: MemoryRecord, metadata: dict[str, Any]
    ) -> bool:
        durable = max(
            self._metadata_weight(metadata, "relationship_weight"),
            self._metadata_weight(metadata, "promise_weight"),
            self._metadata_weight(metadata, "open_loop_weight"),
            self._metadata_weight(metadata, "creative_weight"),
            self._metadata_weight(metadata, "scar_weight"),
            self._metadata_weight(metadata, "emotional_debt_weight"),
        )
        phase = clean_text(metadata.get("relationship_phase"), 80).lower()
        decay_mode = clean_text(metadata.get("decay_mode"), 80).lower()
        if durable >= 0.45:
            return True
        if phase in {"conflict", "repair", "comfort", "closeness", "promise"}:
            return True
        if decay_mode in {"no_decay", "scar_slow_decay", "creative_milestone"}:
            return True
        return clean_text(memory.memory_type, 80).lower() in {
            "manual_memory",
            "explicit_memory",
            "user_profile",
            "user_preference",
            "relationship_claim",
            "creative_work",
        }

    def _memory_companion_state_is_passive_only(self, state: dict[str, Any]) -> bool:
        return bool(state.get("has_state")) and not any(
            bool(state.get(key))
            for key in (
                "has_private_context",
                "has_group_context",
                "has_self_timeline",
                "has_companion_memory",
                "has_dialogue_history",
                "has_recall_query",
            )
        )
