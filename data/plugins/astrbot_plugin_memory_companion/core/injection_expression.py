from __future__ import annotations

import json
import re
from typing import Any

from .models import SearchResult, clean_text


class InjectionComposerExpression:
    """Expression; shared runtime resources belong to the facade."""

    @staticmethod
    def _redact_sensitive_text(value: Any) -> str:
        text = clean_text(value, 4000)
        if not text:
            return ""
        labeled_value = re.compile(
            r"(?i)((?:密码|口令|暗号|验证码|pin|passcode|password|token|api[_ -]?key|密钥|秘钥)\s*(?:是|为|[:：=]|is)\s*)([^；，。！？!?\n]{1,80})"
        )
        adjacent_code = re.compile(
            r"(?i)((?:密码|口令|暗号|验证码|pin|passcode|password|token|api[_ -]?key|密钥|秘钥)\s*)(\d{4,}|[a-z0-9_-]{12,})"
        )
        text = labeled_value.sub(lambda match: f"{match.group(1)}[已隐藏]", text)
        return adjacent_code.sub(lambda match: f"{match.group(1)}[已隐藏]", text)

    @staticmethod
    def _metadata_dict(memory: Any) -> dict[str, Any]:
        metadata = getattr(memory, "metadata", {})
        if isinstance(metadata, dict):
            return metadata
        if isinstance(metadata, str):
            try:
                loaded = json.loads(metadata)
                return loaded if isinstance(loaded, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _expression_label(item: SearchResult) -> str:
        value = InjectionComposerExpression._expression_value(item)
        labels = {
            "mention": "明说",
            "candidate": "条件候选",
            "tone": "语气底色",
            "uncertain": "谨慎不确定",
        }
        return labels.get(value, "明说")

    @staticmethod
    def _expression_usage(item: SearchResult) -> str:
        value = InjectionComposerExpression._expression_value(item)
        metadata = (
            item.memory.metadata if isinstance(item.memory.metadata, dict) else {}
        )
        policy = clean_text(metadata.get("mention_policy"), 60)
        if policy == "avoid_unless_asked":
            return "除非用户明确问起，否则不要主动提"
        if policy == "tone_only":
            return "只影响语气，禁止复述"
        if value == "candidate":
            if policy == "avoid_unless_asked":
                return "仅当当前发言者的核心意图明确问起其本人相关事实时使用，否则忽略"
            return "仅当当前发言者的核心意图需要其本人相关事实时自然使用；普通陈述或意图不清时忽略，不主动公开"
        if value == "tone":
            return "只影响语气，禁止复述"
        if value == "uncertain":
            return "只能模糊提及，不能当事实"
        if policy == "soft_echo":
            return "轻轻呼应，不要直白翻旧账"
        if policy == "direct":
            return "可在需要时自然明说"
        return "需要时自然提及"

    @staticmethod
    def _hesitation_hint(item: SearchResult, bot_energy: float = 0.0) -> str:
        """Generate hesitation hint for soft_echo memories when bot energy is low."""
        metadata = (
            item.memory.metadata if isinstance(item.memory.metadata, dict) else {}
        )
        policy = clean_text(metadata.get("mention_policy"), 60)
        try:
            scar_w = float(metadata.get("scar_weight") or 0.0)
        except Exception:
            scar_w = 0.0
        if policy == "soft_echo" and bot_energy > 0 and bot_energy < 50:
            return "这条记忆你有点犹豫要不要提，如果提请用试探性语气（'不知道你还记不记得…'）"
        if scar_w >= 0.55 and bot_energy > 0 and bot_energy < 40:
            return "这条旧事你此刻不想主动翻，等对方更稳的时候再说"
        return ""

    @staticmethod
    def _expression_value(item: SearchResult) -> str:
        reason = clean_text(getattr(item, "reason", ""), 1000)
        match = re.search(r"(?:^|;)expression=([^;]+)", reason)
        return clean_text(match.group(1), 40) if match else "mention"

    @staticmethod
    def _persona_section(item: SearchResult) -> str:
        memory = item.memory
        metadata = InjectionComposerExpression._metadata_dict(memory)

        memory_type = clean_text(memory.memory_type, 80).lower()
        # Group summaries use an observer voice while covering many participants.
        # Keep them available as context without turning them into the Bot's own
        # continuity or an unresolved personal promise.
        if memory_type == "conversation_summary" and (
            memory.scope == "group" or memory.visibility == "group_public"
        ):
            return "group_context"

        def weight(key: str) -> float:
            try:
                return max(0.0, min(1.0, float(metadata.get(key) or 0.0)))
            except Exception:
                return 0.0

        candidates = [
            (
                "open_loops",
                max(
                    weight("open_loop_weight"),
                    weight("promise_weight"),
                    weight("emotional_debt_weight"),
                ),
            ),
            (
                "relationship_memory",
                max(weight("relationship_weight"), weight("intimacy_weight")),
            ),
            (
                "emotional_context",
                max(weight("emotional_weight"), weight("vulnerability_weight")),
            ),
            ("creative_threads", weight("creative_weight")),
            ("self_continuity", weight("self_continuity_weight")),
            (
                "stable_facts",
                max(
                    weight("preference_weight"),
                    float(getattr(memory, "importance", 0.0) or 0.0) * 0.45,
                ),
            ),
        ]
        section, score = max(candidates, key=lambda item_score: item_score[1])
        if score >= 0.35:
            return section
        if memory.visibility == "bot_self" or memory.memory_type in {
            "persona_life",
            "schedule_fragment",
            "proactive_message",
        }:
            return "self_continuity"
        if memory.memory_type in {
            "user_profile",
            "user_preference",
            "user_habit",
            "manual_memory",
            "tool_memory",
        }:
            return "stable_facts"
        return "other_memory"

    def _source_label(self, memory) -> str:
        if memory.scope == "group":
            return f"群聊:{memory.group_id or memory.session_id or 'unknown'}"
        if memory.scope == "private":
            if getattr(memory.subject, "kind", "") == "user" and getattr(
                memory.subject, "id", ""
            ) not in {"", "self"}:
                target = memory.subject.name or memory.subject.id
            else:
                target = (
                    memory.object.name
                    or memory.object.id
                    or memory.session_id
                    or "unknown"
                )
            return f"私聊:{target}"
        if memory.visibility == "bot_self":
            return "Bot自我时间线"
        return memory.source_plugin or "unknown"

    @staticmethod
    def _ownership_hint(memory) -> str:
        memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
        is_group_summary = memory_type == "conversation_summary" and (
            getattr(memory, "scope", "") == "group"
            or getattr(memory, "visibility", "") == "group_public"
        )
        if is_group_summary:
            return "归属：多人群聊摘要；发言、计划和经历只属于正文中明确点名的人，不代表 Bot 或当前对象"
        if getattr(memory, "visibility", "") == "bot_self":
            return "归属：Bot自身记录"
        subject = getattr(memory, "subject", None)
        if getattr(subject, "kind", "") == "bot" and memory_type in {
            "self_action",
            "persona_life",
            "schedule_fragment",
            "proactive_message",
            "companion_note",
        }:
            return "归属：Bot自身记录"
        if (
            memory_type == "conversation_summary"
            and getattr(memory, "scope", "") == "private"
        ):
            return "归属：当前私聊共同记录；正文中的我/你仍需按当前会话区分"
        return ""

    @staticmethod
    def _compact_ownership_hint(memory) -> str:
        memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
        if memory_type == "conversation_summary" and (
            getattr(memory, "scope", "") == "group"
            or getattr(memory, "visibility", "") == "group_public"
        ):
            return "归属：多人群聊背景"
        if getattr(memory, "visibility", "") == "bot_self":
            return "归属：Bot自身"
        return ""

    @staticmethod
    def _time_label(memory) -> str:
        value = clean_text(
            getattr(memory, "occurred_at", "")
            or getattr(memory, "updated_at", "")
            or getattr(memory, "created_at", ""),
            40,
        )
        if not value:
            return "未知"
        return clean_text(value[:16].replace("T", " "), 20)

    @staticmethod
    def _confidence_label(value: Any) -> str:
        try:
            confidence = float(value)
        except Exception:
            confidence = 0.0
        if confidence >= 0.82:
            return "高"
        if confidence >= 0.58:
            return "中"
        return "低"

    @staticmethod
    def _atmosphere_hint(
        emotional_tone: str,
        intimacy_level: float,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        *,
        time_of_day: str = "",
    ) -> str:
        """Generate persona-aware atmosphere hint based on detected emotional tone, companion bot state, and time of day."""
        hints: list[str] = []
        # Time-of-day atmosphere
        if time_of_day == "late_night":
            hints.append(
                "此刻夜深，记忆里柔软和脆弱的部分更容易浮上来；沉重旧事除非用户主动提起，否则只做语气底色。"
            )
        elif time_of_day == "dawn":
            hints.append("此刻凌晨，记忆以轻量、温暖为主；避免翻沉重旧事，给对方空间。")
        elif time_of_day == "early_morning":
            hints.append(
                "新的一天刚开始，记忆以轻量、可期待为主；如果有未完成的话题可以自然接上。"
            )
        elif time_of_day == "evening":
            hints.append("此刻傍晚，可以更自然地提起今天的共同经历或有趣旧事。")
        # User-side emotional tone
        if emotional_tone == "vulnerable":
            hints.append(
                "对方此刻比较脆弱；如果记忆中有安慰、陪伴、被接住的经历，用更温柔的语气自然融入，不要像查档案一样翻旧事。"
            )
        elif emotional_tone == "distressed":
            hints.append(
                "对方此刻情绪激动；记忆只用来理解为什么，不要急着翻旧账或讲道理；先接住情绪再考虑是否提及相关记忆。"
            )
        elif emotional_tone == "nostalgic":
            hints.append(
                "对方此刻在回忆过去；如果有相关记忆，可以自然接上，用“我也记得”的语气而不是“根据记录”的语气。"
            )
        elif emotional_tone == "warm":
            hints.append(
                "对方此刻在表达关心；如果有对方在意你、照顾你的记忆，可以自然回应这份温暖，让记忆变成“我也一直记得你对我好”。"
            )
        elif emotional_tone == "playful":
            hints.append(
                "对方此刻氛围轻松；可以更自然地提起有趣的旧事，但不要在玩笑气氛中突然插入沉重记忆。"
            )
        elif emotional_tone == "serious":
            hints.append(
                "对方此刻在认真讨论；记忆中如果有相关事实、约定或承诺，可以作为认真回应的依据，但不要跑题。"
            )
        elif intimacy_level >= 0.55:
            hints.append(
                "对方此刻展现了一定亲密和信任；可以更自然地用记忆中共同的经历来回应，让对方感受到“你一直记得”。"
            )
        # Bot-side emotional state from companion plugin
        if companion_bot_mood:
            mood_lower = companion_bot_mood.strip().lower()
            if any(kw in mood_lower for kw in ("累", "疲惫", "低落", "疲", "倦")):
                hints.append(
                    "你此刻心理状态偏疲态；记忆注入以轻量、温暖为主，避免大量翻旧账加重负担。"
                )
            elif any(
                kw in mood_lower for kw in ("开心", "愉快", "兴奋", "高涨", "好心情")
            ):
                hints.append(
                    "你此刻心情不错；记忆可以更活泼地融入，用轻松的方式提起共同经历。"
                )
            elif any(
                kw in mood_lower for kw in ("难过", "伤心", "低气压", "emo", "郁")
            ):
                hints.append(
                    "你此刻情绪偏低；如果记忆中有温暖、被关心的经历，可以自然用它来安惑自己，但不要强行翻沉重旧事。"
                )
            elif any(kw in mood_lower for kw in ("生气", "愤怒", "不爽", "烦")):
                hints.append(
                    "你此刻情绪不太稳定；记忆只用来理解关系脉络，不要在情绪上头时翻敏感旧事。"
                )
            elif any(kw in mood_lower for kw in ("平静", "平稳", " neutral", "淡定")):
                pass  # 平稳状态不需要额外提示
        if companion_bot_energy > 0 and companion_bot_energy < 30:
            hints.append(
                "你此刻心理能量很低；记忆注入以最少必要为主，优先用语气底色而非明说来减轻认知负担。"
            )
        elif 0 < companion_bot_energy < 50:
            hints.append(
                "你此刻心理能量偏低；记忆可以参与但以轻量提及为主，避免一次引入太多线索。"
            )
        return " ".join(hints) if hints else ""

    @staticmethod
    def _short_rest_check_hint(
        message_text: str, time_of_day: str = "", companion_bot_mood: str = ""
    ) -> str:
        text = clean_text(message_text, 80)
        if not text or len(text) > 20:
            return ""
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", text)
        if not compact:
            return ""
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
            return ""
        mood = clean_text(companion_bot_mood, 80).lower()
        rest_like = time_of_day in {"late_night", "dawn"} or any(
            word in mood for word in ("睡", "困", "倦", "疲", "累", "迷糊", "休息")
        )
        if not rest_like:
            return ""
        return (
            "当前像是睡眠/休息中的短检查或查岗；先简短回应人在、不必展开。"
            "召回到的旧“查岗/梦境/穿着”等记忆只能影响亲近感和语气，"
            "不要复述旧细节，不要把旧记录当作此刻正在发生，也不要新编具体梦境或继续追问。"
        )

    @staticmethod
    def _persona_hint(metadata: dict[str, Any]) -> str:
        reason = clean_text(metadata.get("memory_reason"), 140)
        dimensions = metadata.get("persona_dimensions")
        if isinstance(dimensions, list):
            labels = {
                "preference": "偏好",
                "relationship": "关系",
                "promise": "承诺",
                "open_loop": "未完成",
                "creative": "创作",
                "emotional": "情绪",
                "self_continuity": "自我连续",
            }
            names = [
                labels.get(clean_text(item, 40), clean_text(item, 40))
                for item in dimensions[:3]
            ]
            names = [name for name in names if name]
            if names:
                return f"拟人线索：{','.join(names)}" + (
                    f"（{reason}）" if reason else ""
                )
        if reason:
            return f"拟人线索：{reason}"
        return ""

    @staticmethod
    def _dynamics_hint(metadata: dict[str, Any]) -> str:
        phase = clean_text(metadata.get("relationship_phase"), 40)
        decay = clean_text(metadata.get("decay_mode"), 50)
        last_touch = clean_text(metadata.get("last_emotional_touch_at"), 40)
        try:
            scar = float(metadata.get("scar_weight") or 0.0)
        except Exception:
            scar = 0.0
        hints: list[str] = []
        if phase and phase != "neutral":
            hints.append(f"当时关系情境={phase}")
        if scar >= 0.45:
            hints.append("伤痕感=高")
        try:
            vulnerability = float(metadata.get("vulnerability_weight") or 0.0)
        except Exception:
            vulnerability = 0.0
        try:
            intimacy = float(metadata.get("intimacy_weight") or 0.0)
        except Exception:
            intimacy = 0.0
        if vulnerability >= 0.50:
            hints.append("脆弱感=高")
        if intimacy >= 0.50:
            hints.append("亲密感=高")
        if decay in {"no_decay", "scar_slow_decay", "creative_milestone"}:
            hints.append(f"衰减={decay}")
        policy = clean_text(metadata.get("mention_policy"), 50)
        if policy:
            hints.append(f"提及边界={policy}")
        if last_touch:
            hints.append(f"最近触动={last_touch[:10]}")
        return f"记忆动态：{','.join(hints)}" if hints else ""

    @staticmethod
    def _continuation_hint(metadata: dict[str, Any], item: SearchResult) -> str:
        reason = clean_text(getattr(item, "reason", ""), 1000)
        try:
            open_loop = float(metadata.get("open_loop_weight") or 0.0)
            promise = float(metadata.get("promise_weight") or 0.0)
            scar = float(metadata.get("scar_weight") or 0.0)
            emotional_debt = float(metadata.get("emotional_debt_weight") or 0.0)
        except Exception:
            open_loop = promise = scar = emotional_debt = 0.0
        if "slot=open_loop" in reason or max(open_loop, promise) >= 0.35:
            return "接续方式：优先自然接上未完成事项或兑现承诺，不要像清单一样罗列。"
        if emotional_debt >= 0.35:
            return "接续方式：这里可能有没展开的情绪或被打断的话题，语气要轻，给对方继续说的空间。"
        if scar >= 0.55:
            return (
                "接续方式：这是敏感旧事，只在当前话题需要时轻轻照顾，不要突然翻旧账。"
            )
        return ""
