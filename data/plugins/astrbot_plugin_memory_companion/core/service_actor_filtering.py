from __future__ import annotations

import re
from typing import Any

from .identity import (
    session_target_id,
)
from .models import (
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceActorFiltering:
    """Actor filtering; state and transaction ownership remain in the facade."""

    def _filter_current_state_memory_slots(
        self,
        ctx: SessionContext,
        slot_map: dict[str, list[Any]],
    ) -> tuple[dict[str, list[Any]], list[str]]:
        if not self.config.bool(
            "context_orchestration.current_state_relevance_guard_enabled", True
        ):
            return slot_map, []
        anchors = self._current_state_query_anchors(ctx.message_text)
        if not anchors:
            return slot_map, []
        cleaned: dict[str, list[Any]] = {}
        reasons: list[str] = []
        dropped = 0
        for slot, items in (slot_map or {}).items():
            kept = []
            for item in items or []:
                memory = getattr(item, "memory", None)
                if memory is not None and self._memory_is_current_state_candidate(
                    memory, anchors
                ):
                    kept.append(item)
                else:
                    dropped += 1
            if kept:
                cleaned[slot] = kept
            else:
                cleaned[slot] = []
        if dropped:
            reasons.append(
                f"current_state_relevance_guard:dropped={dropped}:anchors={','.join(sorted(anchors))}"
            )
        return cleaned, reasons

    def _current_state_query_anchors(self, text: str) -> set[str]:
        compact = clean_text(text, 500)
        compact = re.sub(r"\s+", "", compact).lower()
        if not compact:
            return set()
        current_markers = (
            "今天",
            "现在",
            "此刻",
            "这会",
            "刚刚",
            "目前",
            "现在的",
            "今天的",
        )
        if self._message_is_contextual_memory_request(compact) and not any(
            marker in compact for marker in current_markers
        ):
            return set()
        anchors: set[str] = set()
        if any(
            token in compact
            for token in (
                "穿",
                "衣服",
                "衣着",
                "衣装",
                "制服",
                "裙",
                "外套",
                "裤",
                "胖次",
            )
        ):
            anchors.update(
                {"穿", "衣服", "衣着", "衣装", "制服", "裙", "外套", "裤", "胖次"}
            )
        meal_specific = False
        for meal in ("早餐", "早饭", "午饭", "午餐", "晚饭", "晚餐", "夜宵"):
            if meal in compact:
                anchors.add(meal)
                meal_specific = True
        if meal_specific:
            anchors.update({"吃", "饭", "餐"})
        elif any(
            token in compact for token in ("吃什么", "吃了", "吃饭", "喝什么", "喝了")
        ):
            anchors.update({"吃", "喝", "饭", "餐"})
        if any(
            token in compact
            for token in ("在干嘛", "干什么", "做什么", "在做啥", "在干啥", "忙什么")
        ):
            anchors.update({"做", "忙", "上课", "学习", "写", "画", "玩", "睡", "工作"})
        if any(
            token in compact
            for token in ("心情", "状态", "感觉怎么样", "累不累", "困不困")
        ):
            anchors.update({"心情", "状态", "感觉", "累", "困", "开心", "难过"})
        if not anchors:
            return set()
        question_markers = ("什么", "啥", "怎样", "怎么样", "吗", "呢", "？", "?")
        if any(marker in compact for marker in current_markers) or any(
            marker in compact for marker in question_markers
        ):
            return anchors
        return set()

    def _filter_group_actor_memory_slots(
        self,
        ctx: SessionContext,
        slot_map: dict[str, list[Any]],
        *,
        query_text: str = "",
    ) -> tuple[dict[str, list[Any]], list[dict[str, str]]]:
        """Keep group injections tied to the current speaker without disabling explicit sharing."""
        if ctx.scope != "group" or not slot_map:
            return slot_map, []
        if not self._context_bool(ctx, "group_actor_relevance_guard_enabled", True):
            return slot_map, []

        query = clean_text(query_text or ctx.message_text, 1200)
        explicit_recall = self._message_is_contextual_memory_request(
            query
        ) or self._message_requests_temporal_aggregate(query)
        personalized_context = self._message_requests_personalized_context(query)
        contextual_private_use = explicit_recall or personalized_context
        current_user_id = clean_text(ctx.user_id, 160)
        cleaned = {slot: list(items or []) for slot, items in (slot_map or {}).items()}
        blocked: list[dict[str, str]] = []

        for slot, items in cleaned.items():
            kept: list[Any] = []
            for item in items:
                memory = getattr(item, "memory", None)
                if memory is None:
                    kept.append(item)
                    continue
                owner_id, owner_names = self._memory_user_actor(memory)
                actor_mentioned = self._query_mentions_memory_actor(
                    query, owner_id, owner_names
                )
                memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
                visibility = clean_text(getattr(memory, "visibility", ""), 80).lower()
                is_private = (
                    clean_text(getattr(memory, "scope", ""), 40).lower() == "private"
                    or visibility == "private_pair"
                )
                acl_authorized_for_sender = self._acl_authorized_private_group_result(
                    ctx,
                    memory,
                    item,
                )
                is_user_profile = slot == "user_profile" or memory_type in {
                    "user_profile",
                    "user_preference",
                    "relationship_claim",
                    "explicit_memory",
                    "manual_memory",
                }

                reason = ""
                if is_private:
                    if not contextual_private_use and not acl_authorized_for_sender:
                        reason = (
                            "group_private_memory_requires_recall_or_personalization"
                        )
                    elif not owner_id:
                        reason = "group_private_memory_actor_unknown"
                    # Mentioning another member is not an authorization to
                    # disclose that member's private conversation. Only the
                    # current speaker's own private memory may cross into the
                    # group, after visibility/ACL checks have allowed it.
                    elif current_user_id and owner_id and owner_id != current_user_id:
                        reason = "group_private_memory_actor_not_current_sender"
                    elif not current_user_id:
                        reason = "group_private_memory_sender_unknown"
                elif (
                    is_user_profile
                    and owner_id
                    and current_user_id
                    and owner_id != current_user_id
                    and not actor_mentioned
                ):
                    reason = "group_profile_actor_not_current_sender"

                if reason:
                    blocked.append(
                        {
                            "id": clean_text(getattr(memory, "id", ""), 120),
                            "reason": reason,
                            "content": "",
                        }
                    )
                    continue
                kept.append(item)
            cleaned[slot] = kept
        return cleaned, blocked

    def _acl_authorized_private_group_result(
        self,
        ctx: SessionContext,
        memory: Any,
        item: Any,
    ) -> bool:
        """Recognize a relevant private candidate structurally shared to this speaker's group."""
        if ctx.scope != "group":
            return False
        visibility = clean_text(getattr(memory, "visibility", ""), 80).lower()
        scope = clean_text(getattr(memory, "scope", ""), 40).lower()
        if scope != "private" and visibility != "private_pair":
            return False
        owner_id, _owner_names = self._memory_user_actor(memory)
        current_user_id = clean_text(ctx.user_id, 160)
        reader_id = clean_text(ctx.group_id or ctx.session_id, 160)
        if (
            not owner_id
            or not current_user_id
            or owner_id != current_user_id
            or not reader_id
        ):
            return False
        reason = clean_text(getattr(item, "reason", ""), 2000)
        acl_reason = f"acl_allowed:private:{owner_id}->group:{reader_id}"
        if acl_reason not in reason:
            return False
        if not self._search_result_has_relevance_evidence(reason):
            return False
        return True

    def _search_result_has_relevance_evidence(self, reason: str) -> bool:
        """Require actual lexical or semantic evidence before seamless ACL use."""
        if re.search(r"(?:^|;)exact=1(?:;|$)", reason):
            return True
        hits = re.search(r"(?:^|;)hits=(\d+)(?:;|$)", reason)
        if hits and int(hits.group(1)) > 0:
            return True
        vector = re.search(r"(?:^|;)vector=([0-9]+(?:\.[0-9]+)?)(?:;|$)", reason)
        threshold = max(
            0.0,
            min(1.0, self.config.float("retrieval.embedding_score_threshold", 0.34)),
        )
        return bool(vector and float(vector.group(1)) >= threshold)

    @staticmethod
    def _memory_user_actor(memory: Any) -> tuple[str, list[str]]:
        ids: list[str] = []
        names: list[str] = []
        for entity in (
            getattr(memory, "subject", None),
            getattr(memory, "object", None),
        ):
            if clean_text(getattr(entity, "kind", ""), 40).lower() != "user":
                continue
            entity_id = clean_text(getattr(entity, "id", ""), 160)
            if entity_id and entity_id != "self" and entity_id not in ids:
                ids.append(entity_id)
            name = clean_text(getattr(entity, "name", ""), 160)
            if name and name not in names:
                names.append(name)
        if not ids:
            session_id = clean_text(getattr(memory, "session_id", ""), 200)
            fallback = session_target_id(session_id, "private") if session_id else ""
            if fallback and fallback != "self":
                ids.append(fallback)
        return (ids[0] if ids else ""), names

    @staticmethod
    def _query_mentions_memory_actor(
        query: str, actor_id: str, actor_names: list[str]
    ) -> bool:
        compact = re.sub(r"\s+", "", clean_text(query, 1200)).lower()
        if not compact:
            return False
        if actor_id and actor_id.lower() in compact:
            return True
        ignored_names = {"用户", "对方", "群成员", "本人", "自己", "bot"}
        return any(
            len(name) >= 2
            and name.lower() not in ignored_names
            and name.lower() in compact
            for name in actor_names
        )

    def _memory_matches_current_state_anchors(
        self, memory: Any, anchors: set[str]
    ) -> bool:
        if not anchors:
            return True
        text = " ".join(
            clean_text(value, 1000)
            for value in (
                getattr(memory, "content", ""),
                getattr(memory, "evidence", ""),
                " ".join(getattr(memory, "tags", []) or []),
                getattr(getattr(memory, "subject", None), "name", ""),
                getattr(getattr(memory, "object", None), "name", ""),
            )
            if clean_text(value, 1000)
        )
        return any(anchor in text for anchor in anchors)

    def _memory_is_current_state_candidate(
        self, memory: Any, anchors: set[str]
    ) -> bool:
        if not self._memory_matches_current_state_anchors(memory, anchors):
            return False
        age_days = self._memory_age_days(memory)
        if age_days is None:
            return False
        if age_days > 1.5:
            return False
        lifecycle = clean_text(getattr(memory, "lifecycle", ""), 80).lower()
        memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
        tags = {
            clean_text(tag, 80).lower() for tag in (getattr(memory, "tags", []) or [])
        }
        if lifecycle == "raw_event":
            return True
        return bool(
            memory_type
            in {
                "self_action",
                "persona_life",
                "schedule_fragment",
                "companion_note",
                "conversation_event",
                "timeline_event",
                "explicit_memory",
                "tool_memory",
                "manual_memory",
            }
            or tags & {"current_state", "self_timeline", "today", "recent"}
        )
