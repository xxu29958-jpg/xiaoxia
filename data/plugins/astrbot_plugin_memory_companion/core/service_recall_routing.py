from __future__ import annotations

import re
from typing import Any

from .astrbot_compat import (
    retrieval_history_text,
)
from .context_orchestrator import RetrievalIntent
from .models import (
    SessionContext,
    clean_text,
)
from .retrieval import RetrievalEngine
from .service_support import (
    MemoryRouteDecision,
)
from .time_intent import TimeIntent, parse_time_intent
from .turn_signal import message_terms


class MemoryCompanionServiceRecallRouting:
    """service recall routing; the facade owns all shared resources."""

    def _topic_shift_guard_reason(
        self, turn_signal: Any, rows: list[dict[str, Any]]
    ) -> str:
        if (
            turn_signal.low_information
            or turn_signal.context_dependent
            or not turn_signal.standalone_request
        ):
            return ""
        current_terms = set(turn_signal.terms or [])
        if len(current_terms) < 2 or not rows:
            return ""
        recent_text = " ".join(
            clean_text(row.get("content"), 500) for row in rows if row.get("content")
        )
        recent_terms = set(message_terms(recent_text, limit=120))
        if not recent_terms:
            return ""
        overlap = current_terms & recent_terms
        if overlap:
            return ""
        preview_terms = "、".join(list(current_terms)[:8])
        return f"standalone_request_no_recent_overlap terms={preview_terms}"

    def _memory_route_decision(
        self,
        ctx: SessionContext,
        turn_signal: Any,
        time_intent: TimeIntent,
        *,
        route_text: str = "",
        isolate_request_context: bool = False,
        topic_shift_reason: str = "",
    ) -> MemoryRouteDecision:
        text = clean_text(route_text or ctx.message_text, 1200)
        vague_recent_followup = self._message_is_vague_recent_followup(text)
        recent_context_request = self._message_requests_recent_context(text)
        temporal_request = self._message_requests_temporal_aggregate(text) or (
            time_intent.active and time_intent.summary_like
        )
        explicit_memory = (
            self._message_is_contextual_memory_request(text)
            and not vague_recent_followup
            and not recent_context_request
        ) or temporal_request
        personalized_context = self._message_requests_personalized_context(text)
        lightweight_generation = self._message_is_lightweight_generation_request(text)
        personalized_generation = personalized_context and (
            lightweight_generation
            or self._message_is_personalized_generation_request(text)
        )
        correction = clean_text(
            getattr(turn_signal, "kind", ""), 40
        ) == "correction" or self._looks_like_user_correction_text(text)
        if self._message_is_future_arrangement_question(text, bot_id=ctx.bot_id):
            return MemoryRouteDecision(
                layer="future_arrangement_chat",
                query_mode="current_message",
                allow_contextual_expansion=False,
                guard_lines=[
                    "- 当前消息在确认未来安排；优先使用 Bot 自身日程、明确承诺或当前对话里已经说清的安排。",
                    "- 群聊多人内容和旧摘要可以帮助理解气氛，但不能证明 Bot 或当前对象明天要做什么。",
                    "- 证据不足时可以自然推测或反问，但要保留不确定感；不要把推测说成“你又忘了”式共同历史。",
                ],
            )
        if time_intent.active:
            return MemoryRouteDecision(
                layer="time_window",
                query_mode="current_message",
                allow_contextual_expansion=False,
                guard_lines=[
                    f"- 当前消息包含时间窗口：{time_intent.display_range}；只围绕该时间范围组织记忆。"
                ],
            )
        if recent_context_request and not explicit_memory:
            return MemoryRouteDecision(
                layer="recent_context",
                query_mode="current_message",
                allow_contextual_expansion=False,
                suppress_long_memory=True,
                suppress_reason="recent_context_request",
                guard_lines=[
                    "- 当前消息询问或承接近端原始上下文；优先依据 AstrBot 原始上下文，不主动召回长期记忆。"
                ],
            )
        if self._message_is_shared_routine_invocation(text):
            return MemoryRouteDecision(
                layer="shared_routine",
                query_mode="current_message",
                allow_contextual_expansion=False,
                guard_lines=[
                    "- 当前消息可能是双方约定俗成的例行互动简称，也可能只是普通状态确认；结合当前上下文和直接相关记忆判断。",
                    "- 如果多条可靠记忆对简称含义给出一致线索，可以自然承接；不要仅因处于夜间或休息状态就忽略这些线索。",
                    "- 证据不足、只有单条弱线索或彼此矛盾时，简短自然地确认含义；只使用必要信息，不复述密码、无关私密细节或旧检查结果。",
                ],
            )
        if personalized_generation and not explicit_memory:
            return MemoryRouteDecision(
                layer="personalized_generation",
                query_mode="current_message",
                allow_contextual_expansion=False,
                guard_lines=[
                    "- 当前消息需要个性化创作；只使用与当前发言者或明确对象直接相关的画像和记忆，不要顺带展开无关旧事。"
                ],
            )
        if lightweight_generation and not explicit_memory:
            return MemoryRouteDecision(
                layer="standalone_generation",
                query_mode="current_message",
                allow_contextual_expansion=False,
                suppress_long_memory=True,
                suppress_reason="standalone_generation_request",
                guard_lines=[
                    "- 当前消息是独立的轻量创作请求；直接完成当前请求，不主动翻出旧聊天、用户画像或跨窗口记忆。"
                ],
            )
        if correction and not explicit_memory:
            return MemoryRouteDecision(
                layer="current_correction",
                query_mode="current_message",
                allow_contextual_expansion=False,
                suppress_long_memory=True,
                suppress_reason=f"current_correction:{getattr(turn_signal, 'reason', '') or 'correction'}",
                guard_lines=[
                    "- 当前消息是低信息纠错/否定；只回应纠正本身，不主动召回或延续旧长期记忆。"
                ],
            )
        if self._message_is_casual_current_state_question(text) and not explicit_memory:
            return MemoryRouteDecision(
                layer="current_state_chat",
                query_mode="current_message",
                allow_contextual_expansion=False,
                guard_lines=[
                    "- 当前消息是当前状态寒暄/询问；只能使用近期且直接命中当前状态的记忆，不要用旧长期记忆回答今天、现在或此刻的状态。"
                ],
            )
        if bool(getattr(turn_signal, "low_information", False)) and not explicit_memory:
            reason = f"low_information_turn:{getattr(turn_signal, 'kind', 'unknown')}:{getattr(turn_signal, 'reason', '')}"
            return MemoryRouteDecision(
                layer="low_information",
                query_mode="current_message",
                allow_contextual_expansion=False,
                suppress_long_memory=self._context_bool(
                    ctx, "suppress_memory_on_low_information", True
                ),
                suppress_reason=reason,
                guard_lines=[
                    "- 当前消息信息量较低；优先贴着当前消息与原始短上下文，不主动展开长期记忆。"
                ],
            )
        if (
            bool(getattr(turn_signal, "context_dependent", False))
            and not explicit_memory
        ):
            return MemoryRouteDecision(
                layer="short_context_followup",
                query_mode="current_message",
                allow_contextual_expansion=False,
                suppress_long_memory=True,
                suppress_reason="context_dependent_short_followup",
                guard_lines=[
                    "- 当前消息依赖最近原始对话承接；不要把旧长期记忆当成新检索目标。"
                ],
            )
        if isolate_request_context and not explicit_memory:
            reason = (
                "topic_shift_guard:long_term_memory_suppressed"
                if topic_shift_reason
                else "isolated_current_request"
            )
            return MemoryRouteDecision(
                layer="isolated_current_request",
                query_mode="current_message",
                allow_contextual_expansion=False,
                suppress_long_memory=bool(topic_shift_reason)
                and self._context_bool(ctx, "suppress_memory_on_topic_shift", True),
                suppress_reason=reason,
                guard_lines=[
                    "- 当前消息被判定为新的独立请求；不要让原始历史或联动插件中的旧话题主导当前任务。",
                    "- 仍要留意同一会话近期明确事实，尽量别重复询问已经回答的问题，也别和刚发生的事情打架。",
                ],
            )
        return MemoryRouteDecision(
            layer="memory_retrieval",
            query_mode=str(
                self.config.get("context_orchestration.query_mode", "")
                or "current_message"
            ),
            allow_contextual_expansion=True,
        )

    async def _expand_contextual_retrieval_intent(
        self,
        ctx: SessionContext,
        intent: RetrievalIntent,
        turn_signal: Any,
        *,
        req: Any = None,
    ) -> RetrievalIntent:
        if not self.config.bool(
            "context_orchestration.contextual_query_expansion_enabled", True
        ):
            return intent
        query = clean_text(intent.query or ctx.message_text, 1400)
        if parse_time_intent(query).active:
            return intent
        if not query or not self._should_expand_contextual_query(query, turn_signal):
            return intent
        limit = self.config.int(
            "conversation_memory.recent_events_for_followup",
            self.config.int("context_orchestration.contextual_query_recent_events", 12),
        )
        rows = []
        for item in reversed((getattr(req, "contexts", None) or [])[-max(1, limit) :]):
            role = (
                item.get("role")
                if isinstance(item, dict)
                else getattr(item, "role", "")
            )
            content = (
                item.get("content")
                if isinstance(item, dict)
                else getattr(item, "content", None)
            )
            if role not in {"user", "assistant"}:
                continue
            text = clean_text(retrieval_history_text(content), 2000)
            if text:
                rows.append(
                    {
                        "event_type": "bot_response"
                        if role == "assistant"
                        else "user_message",
                        "content": text,
                    }
                )
        if not rows:
            rows = await self.store.recent_timeline(
                limit=limit,
                scope=ctx.scope,
                session_id=ctx.session_id,
                entity_id=ctx.current_target_id,
            )
            rows = [
                {**row, "content": text}
                for row in rows
                if (
                    text := clean_text(retrieval_history_text(row.get("content")), 2000)
                )
            ]
        if not self._recent_context_supports_memory_expansion(query, rows, turn_signal):
            return intent
        anchors = self._contextual_query_anchors(
            query,
            rows,
            limit=self.config.int(
                "context_orchestration.contextual_query_anchor_limit", 8
            ),
        )
        if not anchors:
            return intent
        current_anchors = self._anchor_terms_for_text(query)
        replace_current = self._should_replace_current_query_with_anchors(
            query, turn_signal, current_anchors
        )
        query_parts = (
            [*current_anchors, *anchors] if replace_current else [query, *anchors]
        )
        expanded_query = clean_text(
            " ".join(
                dict.fromkeys(part for part in query_parts if clean_text(part, 80))
            ),
            1400,
        )
        if not expanded_query or expanded_query == query:
            return intent
        intent.query = expanded_query
        intent.source = (
            "contextual"
            if intent.source == "message"
            else f"{intent.source}+contextual"
        )
        intent.query_mode = "contextual_expanded"
        intent.keywords = list(dict.fromkeys([*intent.keywords, *anchors]))[:12]
        note = "连续对话补全：" + "、".join(anchors[:6])
        intent.notes = list(dict.fromkeys([*intent.notes, note]))[:6]
        return intent

    def _message_requests_memory_context(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        markers = (
            "记得",
            "记忆",
            "回忆",
            "当时",
            "之前",
            "以前",
            "上次",
            "刚才",
            "刚刚",
            "那次",
            "继续刚才",
            "接着刚才",
            "沿用",
            "老样子",
            "按我喜欢",
            "按我的",
            "我的偏好",
            "我喜欢的",
            "我常用",
            "我的设定",
            "我的人设",
            "我叫什么",
            "你知道我",
            "我们说过",
            "还记不记得",
            "remember",
            "previous",
            "lasttime",
            "asbefore",
        )
        return any(marker in compact for marker in markers)

    @staticmethod
    def _message_is_lightweight_generation_request(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        return bool(
            re.search(
                r"(?:给我|请|能不能|可以|来)?(?:讲|说|编|写|来)(?:一个|个|一段|段|一则|则|一篇|篇)?"
                r"(?:冷笑话|笑话|故事|段子|谜语|脑筋急转弯)",
                compact,
            )
        )

    @staticmethod
    def _message_is_personalized_generation_request(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        return bool(
            re.search(
                r"^(?:你)?(?:(?:(?:能不能|可以|请)?(?:给我)?(?:讲|编|写))|来)"
                r"(?:一个|个|一段|段|一则|则|一篇|篇)?[^。！？!?；;，,\n]{0,36}"
                r"(?:冷笑话|笑话|故事|段子|谜语|脑筋急转弯)",
                compact,
            )
        )

    @staticmethod
    def _message_requests_personalized_context(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        markers = (
            "适合我",
            "符合我",
            "结合我",
            "根据我",
            "照着我",
            "我会喜欢",
            "我可能喜欢",
            "我的性格",
            "我的口味",
            "我的习惯",
            "我的风格",
            "专属于我",
            "像平时那样",
            "平时那种",
            "咱们平时",
            "按你的风格",
            "按照你的风格",
            "符合你的人设",
            "结合你的经历",
            "你会喜欢",
        )
        return any(marker in compact for marker in markers)

    def _message_is_contextual_memory_request(self, text: str) -> bool:
        return self._message_requests_memory_context(
            text
        ) or RetrievalEngine._looks_like_contextual_recall_query(text)

    def _message_has_explicit_memory_target(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact or not self._message_requests_memory_context(compact):
            return False
        if self._message_is_vague_recent_followup(compact):
            return False
        if self._message_requests_temporal_aggregate(compact):
            return True
        anchors = self._anchor_terms_for_text(compact)
        return len(anchors) >= 3 and len(compact) >= 10

    @staticmethod
    def _message_is_vague_recent_followup(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        vague_followups = (
            "还有吗",
            "还有的吧",
            "还有呢",
            "还有什么",
            "继续",
            "接着",
            "那个呢",
            "这个呢",
            "刚才那个",
            "刚刚那个",
            "上面那个",
            "前面那个",
            "继续刚才",
            "接着刚才",
            "继续上面",
            "接着上面",
        )
        return (
            any(marker in compact for marker in vague_followups) and len(compact) <= 14
        )

    @staticmethod
    def _message_requests_temporal_aggregate(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        temporal_markers = (
            "最近一周",
            "近一周",
            "这一周",
            "这周",
            "本周",
            "最近几天",
            "这几天",
            "近几天",
            "最近7天",
            "最近七天",
            "过去一周",
            "过去7天",
            "过去七天",
        )
        if any(marker in compact for marker in temporal_markers):
            return True
        return bool(re.search(r"(最近|过去|近)\d{1,2}天", compact))

    def _message_requests_recent_context(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        direct_markers = (
            "刚才",
            "刚刚",
            "上文",
            "上一轮",
            "前面",
            "前几句",
            "我们聊了什么",
            "聊到哪",
            "发生了什么",
            "最近发生",
            "最近聊",
            "最近互动",
            "印象深刻的互动",
            "recent",
            "previous",
            "lastturn",
        )
        if any(marker in compact for marker in direct_markers):
            return True
        return "最近" in compact and any(
            marker in compact
            for marker in ("互动", "对话", "聊天", "聊过", "发生", "印象")
        )

    @staticmethod
    def _message_is_casual_current_state_question(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact or len(compact) > 24:
            return False
        if any(
            marker in compact
            for marker in (
                "记得",
                "记忆",
                "之前",
                "以前",
                "上次",
                "那次",
                "按我",
                "我喜欢",
                "偏好",
            )
        ):
            return False
        patterns = (
            r"(吃|喝).*(了吗|了没|没|没有|吗|呢|呀|嘛)$",
            r"(早餐|午饭|晚饭|夜宵).*(吃|用|解决)?.*(了吗|了没|没|没有|吗|呢|呀|嘛)$",
            r"(在干嘛|在干啥|在做什么|在做啥|忙什么|忙啥|干什么呢|干嘛呢)",
            r"(累不累|困不困|饿不饿|冷不冷|热不热|还好吗|还好嘛)",
            r"(心情|状态|感觉).*(怎么样|如何|好吗|还好吗|呢|吗)",
            r"(今天|现在|此刻|这会儿|这会).*(穿什么|穿了什么|穿的什么|穿啥|穿了啥)",
        )
        return any(re.search(pattern, compact) for pattern in patterns)

    @staticmethod
    def _schedule_query_owner(
        text: str,
        *,
        bot_id: str = "",
        allow_implicit_temporal: bool = False,
    ) -> str:
        """Return the structured owner of a personal-schedule question.

        This is deliberately a positive grammar rather than a list of topics to
        reject.  A schedule overflow is only useful when the query identifies a
        Bot-owned plan, or is the short conversational form of a personal status
        question (for example, ``明天有空吗``).  Generic schedule nouns remain
        ordinary retrieval candidates.
        """
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact or len(compact) > 80:
            return ""

        normalized_bot_id = clean_text(bot_id, 120).lower()
        bot_tokens = ["你", "您", "bot"]
        if normalized_bot_id and normalized_bot_id not in {"self", "bot"}:
            bot_tokens.append(normalized_bot_id)
        bot_pattern = (
            "(?:"
            + "|".join(
                re.escape(token)
                for token in sorted(set(bot_tokens), key=len, reverse=True)
                if token
            )
            + ")"
        )
        temporal = (
            r"(?:今天|今日|昨天|昨日|前天|明天|后天|今晚|明早|明晚|现在|当前|目前|"
            r"最近|本周|这周|这一周|下周|周末|这周末|本月|下个月)"
        )
        schedule_noun = r"(?:日程|行程|安排|计划|待办|提醒事项|时间表)"
        # Modifiers accepted after an explicit Bot owner are themselves
        # personal/time qualifiers.  An arbitrary domain noun (for example
        # ``项目计划`` or ``会议安排``) must not turn a generic plan into Bot
        # autobiography.
        personal_modifier = r"(?:今天|今日|昨天|昨日|前天|明天|后天|今晚|明早|明晚|"
        personal_modifier += r"现在|当前|目前|最近|本周|这周|这一周|下周|周末|这周末|"
        personal_modifier += r"本月|下个月|工作|生活|休息|学习|任务)"
        personal_action = (
            r"(?:上班|下班|加班|上学|上课|出门|回家|回宿舍|休息|起床|睡觉|睡|工作)"
        )
        personal_status = (
            r"(?:有事|有安排|有计划|忙|有空|有时间|"
            r"(?:要|会|得|准备)?(?:上班|上学|上课|出门|休息|睡觉|睡|起床|赖床|工作))"
        )
        status_suffix = r"(?:吗|呢|呀|嘛|不|了没|没有)?[?？]?$"

        bot_patterns = (
            # Possessive forms: 你的日程、你明天的安排、bot 的待办。
            rf"{bot_pattern}(?:{personal_modifier})?(?:的)?{schedule_noun}",
            rf"{bot_pattern}的(?:{personal_modifier})?{schedule_noun}",
            rf"{bot_pattern}的(?:schedule|calendar|itinerary|todo)",
            # Subject forms: 你今天有什么安排、本周你有空吗、你什么时候上班。
            rf"{bot_pattern}(?:知道|能不能|可以|能|会不会|想问|问(?:一下|下)?)?"
            rf"(?:{temporal})?(?:的)?(?:都)?(?:有|有没有|有什么|有哪些){schedule_noun}",
            rf"{temporal}{bot_pattern}(?:的)?(?:都)?(?:有|有没有|有什么|有哪些){schedule_noun}",
            rf"{bot_pattern}(?:{temporal})?(?:几点|什么时候|何时)(?:要|会|得|准备)?{personal_action}",
            rf"{temporal}{bot_pattern}(?:几点|什么时候|何时)?(?:要|会|得|准备)?{personal_action}",
            rf"{bot_pattern}(?:{temporal})?(?:几点|什么时候|何时){personal_status}{status_suffix}",
            rf"{temporal}{bot_pattern}(?:几点|什么时候|何时)?{personal_status}{status_suffix}",
            rf"{bot_pattern}(?:{temporal})?{personal_status}{status_suffix}",
            rf"{temporal}{bot_pattern}{personal_status}{status_suffix}",
            rf"{bot_pattern}(?:{temporal})?(?:在)?(?:做了什么|做什么|要做什么|准备做什么|"
            rf"去(?:了)?哪(?:里|儿)?|干什么|干嘛)",
            rf"{temporal}{bot_pattern}(?:在)?(?:做了什么|做什么|要做什么|准备做什么|"
            rf"去(?:了)?哪(?:里|儿)?|干什么|干嘛)",
        )

        def last_match_end(patterns: tuple[str, ...]) -> int:
            ends = [
                match.end()
                for pattern in patterns
                for match in re.finditer(pattern, compact)
            ]
            return max(ends, default=-1)

        bot_end = last_match_end(bot_patterns)

        english_owner = r"(?:your|bot(?:'s)?"
        if normalized_bot_id and normalized_bot_id not in {"self", "bot"}:
            english_owner += rf"|{re.escape(normalized_bot_id)}(?:'s)?"
        english_owner += r")"
        english_bot_end = last_match_end(
            (rf"{english_owner}(?:schedule|calendar|itinerary|todo)",)
        )
        bot_end = max(bot_end, english_bot_end)

        # User-owned forms are checked separately so a polite prefix such as
        # "你能查一下" cannot make ``我的日程`` look like a Bot request.
        user_patterns = (
            rf"(?:我|我的|咱们|我们|本人|自己)(?:{personal_modifier})?(?:的)?{schedule_noun}",
            rf"(?:我|咱们|我们|本人|自己)(?:{temporal})?{personal_status}{status_suffix}",
            rf"(?:我|咱们|我们|本人|自己)(?:{temporal})?(?:要|会|得|准备)?{personal_action}",
            r"(?:my)(?:schedule|calendar|itinerary|todo)",
        )
        user_end = last_match_end(user_patterns)

        # A named/group owner is any explicit possessive or subject that is not
        # one of the pronouns above.  The owner is intentionally structural;
        # no domain-specific blacklist is needed for ``小王的日程`` or
        # ``会议的安排``.
        generic_owner = r"(?:他|她|它|他们|她们|对方|群|群聊|会议|活动|课程|[\u4e00-\u9fff]{2,16}|[a-z][a-z0-9_.-]{1,40})"
        other_patterns = (
            rf"{generic_owner}的(?:{personal_modifier})?{schedule_noun}",
            rf"{generic_owner}(?:{temporal})?{personal_status}{status_suffix}",
            rf"{generic_owner}(?:{temporal})?(?:要|会|得|准备)?{personal_action}",
            r"(?:my|project)(?:schedule|calendar|itinerary|todo)",
        )
        other_end = -1
        temporal_words = {
            "今天",
            "今日",
            "昨天",
            "昨日",
            "前天",
            "明天",
            "后天",
            "今晚",
            "明早",
            "明晚",
            "现在",
            "当前",
            "目前",
            "最近",
            "本周",
            "这周",
            "这一周",
            "下周",
            "周末",
            "这周末",
            "本月",
            "下个月",
        }
        for pattern in other_patterns:
            for match in re.finditer(pattern, compact):
                owner = clean_text(match.group(0), 120).lower()
                # The generic pattern may also see an ASCII Bot id.  Do not
                # treat it as a third-party owner when it is the configured Bot.
                if normalized_bot_id and owner.startswith(normalized_bot_id):
                    continue
                if owner.startswith(
                    ("你", "您", "bot", "我", "咱", "我们", "本人", "自己")
                ):
                    continue
                if any(owner.startswith(word) for word in temporal_words):
                    continue
                other_end = max(other_end, match.end())

        # The nearest explicit owner wins when a query mentions both parties
        # (e.g. "我想问你的日程").
        if bot_end >= 0 and bot_end >= max(user_end, other_end):
            return "bot"
        if user_end >= 0 and user_end >= other_end:
            return "user"
        if other_end >= 0:
            return "other"

        if not allow_implicit_temporal:
            return ""
        # Short owner-omitted status questions are conventionally addressed to
        # the Bot in a chat.  Keep this grammar anchored so "明天考试有什么
        # 安排" and other group/event schedules remain ambiguous.
        implicit_state = (
            rf"(?:{personal_status}|(?:要|会|得|准备)?{personal_action}|"
            r"(?:要|会|得|准备)?(?:做什么|干什么|干嘛)|"
            r"(?:几点|什么时候|何时)(?:要|会|得|准备)?(?:上班|下班|加班|上学|上课|出门|回家|回宿舍|休息|睡觉|睡|工作))"
        )
        prefix = r"(?:请问|想问(?:一下|下)?|问(?:一下|下)?)?"
        suffix = r"(?:吗|呢|呀|嘛|不|了没|没有)?[?？]?"
        if re.fullmatch(rf"{prefix}{temporal}{implicit_state}{suffix}", compact):
            return "implicit"
        return ""

    @staticmethod
    def _message_targets_bot_personal_schedule(
        text: str,
        *,
        bot_id: str = "",
        allow_implicit_temporal: bool = False,
    ) -> bool:
        return MemoryCompanionServiceRecallRouting._schedule_query_owner(
            text,
            bot_id=bot_id,
            allow_implicit_temporal=allow_implicit_temporal,
        ) in {"bot", "implicit"}

    @staticmethod
    def _message_is_future_arrangement_question(text: str, *, bot_id: str = "") -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact or len(compact) > 48:
            return False
        future_markers = (
            "明天",
            "后天",
            "明早",
            "明晚",
            "今晚",
            "过会",
            "一会儿",
            "待会",
            "下周",
            "本周",
            "这周",
            "这一周",
            "周末",
            "下个月",
        )
        question_markers = (
            "？",
            "?",
            "吗",
            "呢",
            "呀",
            "嘛",
            "是不是",
            "有没有",
            "会不会",
            "能不能",
            "有空不",
        )
        if not (
            any(marker in compact for marker in future_markers)
            and any(marker in compact for marker in question_markers)
        ):
            return False
        return (
            MemoryCompanionServiceRecallRouting._message_targets_bot_personal_schedule(
                compact,
                bot_id=bot_id,
                allow_implicit_temporal=True,
            )
        )
