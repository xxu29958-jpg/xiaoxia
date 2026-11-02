from __future__ import annotations

import re
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    SessionContext,
    clean_text,
    json_loads,
)


class MemoryCompanionServiceReactionFeedback:
    """Apply user reactions only to validated reply-memory targets. Shared resources remain owned by the root service/store."""

    async def _apply_user_reaction_feedback(self, ctx: SessionContext) -> None:
        if not self.config.bool("memory_capture.enabled", True):
            return
        reaction = self._classify_memory_reaction(ctx.message_text)
        if not reaction:
            return
        rows = await self.store.recent_timeline(
            limit=8,
            scope=ctx.scope,
            session_id=ctx.session_id,
            entity_id=ctx.current_target_id,
        )
        target_ids: list[str] = []
        source_timeline_id = ""
        for row in rows:
            if clean_text(row.get("event_type"), 80) != "bot_response":
                continue
            metadata = json_loads(row.get("metadata"), {})
            state = (
                metadata.get("memory_companion_injection_state")
                if isinstance(metadata, dict)
                else {}
            )
            if not isinstance(state, dict):
                state = {}
            target_ids = [
                clean_text(memory_id, 120)
                for memory_id in (state.get("feedback_target_memory_ids") or [])
                if clean_text(memory_id, 120)
            ]
            if target_ids:
                source_timeline_id = clean_text(row.get("id"), 120)
                break
        if not target_ids:
            return
        if reaction in {"corrected", "denied"}:
            target_ids = await self._filter_reaction_feedback_targets(
                ctx.message_text, target_ids
            )
            if not target_ids:
                logger.info(
                    "[MemoryCompanion] 用户纠正未命中具体记忆，跳过批量纠正反馈: session=%s reaction=%s",
                    ctx.session_id,
                    reaction,
                )
                return
        deltas = self._reaction_feedback_deltas(reaction)
        updated = 0
        for memory_id in target_ids[:8]:
            ok = await self.store.update_memory_reaction_feedback(
                memory_id,
                reaction=reaction,
                evidence=ctx.message_text,
                source_id=source_timeline_id,
                mention_delta=deltas["mention"],
                confidence_delta=deltas["confidence"],
                emotional_delta=deltas["emotional"],
            )
            updated += int(bool(ok))
        if updated:
            logger.info(
                "[MemoryCompanion] 已根据用户反应更新记忆提及反馈: session=%s reaction=%s memories=%s",
                ctx.session_id,
                reaction,
                updated,
            )

    async def _filter_reaction_feedback_targets(
        self, evidence: str, target_ids: list[str]
    ) -> list[str]:
        ids = [
            clean_text(memory_id, 120)
            for memory_id in target_ids
            if clean_text(memory_id, 120)
        ]
        if not ids:
            return []
        memories = await self.store.get_memories_by_ids(ids[:8])
        evidence_terms = set(self._anchor_terms_for_text(evidence))
        evidence_compact = re.sub(r"\s+", "", clean_text(evidence, 800)).lower()
        filtered: list[str] = []
        fallback: list[tuple[str, float]] = []
        for memory_id in ids[:8]:
            memory = memories.get(memory_id)
            if memory is None:
                continue
            metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
            haystack = " ".join(
                clean_text(part, 1000)
                for part in (
                    memory.content,
                    memory.evidence,
                    metadata.get("canonical_summary"),
                    metadata.get("persona_summary"),
                    " ".join(str(item) for item in metadata.get("key_facts", [])[:6])
                    if isinstance(metadata.get("key_facts"), list)
                    else "",
                )
                if clean_text(part, 1000)
            )
            memory_terms = set(self._anchor_terms_for_text(haystack))
            overlap = evidence_terms & memory_terms
            score = 0.0
            if overlap:
                score += sum(max(1.0, len(term) / 2.0) for term in overlap)
            compact_memory = re.sub(r"\s+", "", clean_text(haystack, 1800)).lower()
            for term in evidence_terms:
                if len(term) >= 3 and term in compact_memory:
                    score += 1.5
            if (
                evidence_compact
                and len(evidence_compact) >= 6
                and evidence_compact in compact_memory
            ):
                score += 4.0
            if score >= 3.0:
                filtered.append(memory_id)
            elif score > 0:
                fallback.append((memory_id, score))
        if filtered:
            return filtered[:3]
        fallback.sort(key=lambda item: item[1], reverse=True)
        return [memory_id for memory_id, score in fallback[:1] if score >= 1.5]

    @staticmethod
    def _classify_memory_reaction(text: str) -> str:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return ""
        correction_markers = (
            "不是",
            "不对",
            "错了",
            "记错",
            "不是这样",
            "应该是",
            "其实是",
            "我说的是",
            "你搞错了",
            "你理解错",
            "弄错了",
            "搞混了",
            "说反了",
            "正好相反",
        )
        denied_markers = (
            "没有这回事",
            "我没说过",
            "别乱记",
            "你记错了",
            "不是这个",
            "不要这么说",
            "没发生过",
            "不存在",
            "瞎说",
            "乱讲",
            "怎么可能",
            "才没有",
            "根本没有",
            "别瞎编",
            "别乱说",
            "你听谁说的",
        )
        awkward_markers = (
            "别提",
            "别说了",
            "尴尬",
            "不想提",
            "算了",
            "别翻",
            "别回忆",
            "有点怪",
            "别聊这个",
            "换个话题",
            "不想聊",
            "别问了",
            "过去了",
            "别提了",
            "别旧事重提",
            "好尴尬",
            "太尴尬",
            "脚趾抠地",
            "社死",
        )
        comforted_markers = (
            "被安慰到",
            "安心了",
            "好多了",
            "谢谢你记得",
            "你还记得",
            "有被接住",
            "舒服多了",
            "暖到了",
            "好暖心",
            "心里暖暖",
            "谢谢你",
            "有你在真好",
            "被治愈",
            "好感动",
            "谢谢你懂我",
            "被理解了",
            "安心了好多",
            "没那么难过了",
        )
        touched_markers = (
            "感动",
            "好感动",
            "泪目",
            "哭了",
            "暖到了",
            "戳中",
            "破防了",
            "眼眶湿了",
            "好想哭",
            "太感人了",
            "心化了",
            "你真的",
            "好珍惜",
            "谢谢你一直记得",
            "没想到你还记得",
            "你居然记得",
            "好幸福",
            "被在乎的感觉",
            "被惦记",
            "心里好暖",
        )
        nostalgic_markers = (
            "怀念",
            "好怀念",
            "想念",
            "好想念",
            "那时候",
            "从前",
            "以前真好",
            "回忆好美",
            "好回忆",
            "想起来就",
            "忆当年",
            "好想回到",
            "那时候的",
            "曾经的",
            "好感慨",
            "时光啊",
            "好感慨",
        )
        accepted_markers = (
            "对",
            "是的",
            "没错",
            "嗯嗯",
            "就是这个",
            "你记得",
            "你还记得",
            "确实",
            "对的",
            "是呀",
            "是哦",
            "没错呀",
            "对啊",
            "嗯对",
            "是的呢",
            "就是这样",
            "你说得对",
            "可不是嘛",
            "确实如此",
            "真的是",
        )
        if any(marker in compact for marker in correction_markers):
            return "corrected"
        if any(marker in compact for marker in denied_markers):
            return "denied"
        if any(marker in compact for marker in awkward_markers):
            return "awkward"
        if any(marker in compact for marker in touched_markers):
            return "touched"
        if any(marker in compact for marker in nostalgic_markers):
            return "nostalgic"
        if any(marker in compact for marker in comforted_markers):
            return "comforted"
        if any(marker in compact for marker in accepted_markers) and len(compact) <= 40:
            return "accepted"
        return ""

    @staticmethod
    def _reaction_feedback_deltas(reaction: str) -> dict[str, float]:
        if reaction == "accepted":
            return {"mention": 0.08, "confidence": 0.03, "emotional": 0.02}
        if reaction == "comforted":
            return {"mention": 0.11, "confidence": 0.02, "emotional": 0.08}
        if reaction == "touched":
            return {"mention": 0.14, "confidence": 0.04, "emotional": 0.12}
        if reaction == "nostalgic":
            return {"mention": 0.10, "confidence": 0.03, "emotional": 0.06}
        if reaction == "awkward":
            return {"mention": -0.12, "confidence": 0.0, "emotional": 0.02}
        if reaction == "denied":
            return {"mention": -0.18, "confidence": -0.12, "emotional": 0.0}
        if reaction == "corrected":
            return {"mention": -0.15, "confidence": -0.08, "emotional": 0.0}
        return {"mention": 0.0, "confidence": 0.0, "emotional": 0.0}

    def _flatten_slot_map(self, slot_map: dict[str, list[Any]]) -> list[Any]:
        items: list[Any] = []
        seen: set[str] = set()
        for slot in [
            "time_window_timeline",
            "open_loop",
            "self_timeline",
            "user_profile",
            "current_window",
            "conversation_summary",
            "stable_memory",
        ]:
            for item in slot_map.get(slot) or []:
                memory_id = clean_text(
                    getattr(getattr(item, "memory", None), "id", ""), 120
                )
                if memory_id and memory_id in seen:
                    continue
                if memory_id:
                    seen.add(memory_id)
                items.append(item)
        return items
