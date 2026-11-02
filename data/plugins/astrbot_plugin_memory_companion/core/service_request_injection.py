from __future__ import annotations

from typing import Any

from .astrbot_compat import (
    detect_private_companion_request,
    logger,
    remove_temp_text,
)
from .injection import (
    MEMORY_COMPANION_INJECTION_FOOTER,
    MEMORY_COMPANION_INJECTION_HEADER,
)
from .models import (
    SessionContext,
    clean_text,
)
from .time_intent import parse_time_intent
from .turn_signal import analyze_turn_signal


class MemoryCompanionServiceRequestInjection:
    """service request injection; the facade owns all shared resources."""

    async def inject_memories(
        self, ctx: SessionContext, req: Any, *, event: Any = None
    ) -> None:
        stage_timer = getattr(self, "_hook_stage_timer", None)
        self._mark_memory_companion_injection_state(
            event, req, injected=False, conversation_memory=False, slot_map={}
        )
        removed = remove_temp_text(
            req, MEMORY_COMPANION_INJECTION_HEADER, MEMORY_COMPANION_INJECTION_FOOTER
        )
        if removed:
            logger.info(
                "[MemoryCompanion] 已清理历史记忆包注入片段: session=%s count=%s",
                ctx.session_id,
                removed,
            )
        if not self.config.bool("memory_injection.enabled", True):
            self._mark_memory_companion_injection_state(
                event, req, injected=False, conversation_memory=False, slot_map={}
            )
            if self.config.bool("memory_injection.debug_log_injection_enabled", False):
                logger.info(
                    "[MemoryCompanion] 记忆注入已关闭: session=%s", ctx.session_id
                )
            return
        if not self._scope_feature_enabled(ctx, "recall"):
            self._mark_memory_companion_injection_state(
                event, req, injected=False, conversation_memory=False, slot_map={}
            )
            return
        # Rendered text contains the current turn. Only the retrieval layer may
        # reuse cached candidates; it keys and revalidates them independently.
        cache = getattr(self, "_injection_cache", None)
        if isinstance(cache, dict):
            cache.pop(ctx.session_id, None)
        p5_gate = await self._p5_gate(event=event, sink="memory_recall")
        if stage_timer:
            stage_timer.mark("p5_gate")
        if not p5_gate.get("ok"):
            self._mark_memory_companion_injection_state(
                event,
                req,
                injected=False,
                conversation_memory=False,
                slot_map={},
            )
            logger.info(
                "[MemoryCompanion] P5 召回闸门未放行本轮记忆注入: session=%s state=%s error=%s",
                ctx.session_id,
                p5_gate.get("state"),
                p5_gate.get("error_code"),
            )
            return
        self._sanitize_request_history_for_companion(ctx, req)
        turn_signal = analyze_turn_signal(ctx.message_text)
        low_guard_enabled = self._context_bool(
            ctx, "low_information_guard_enabled", True
        )
        skip_long_memory = bool(
            low_guard_enabled
            and turn_signal.low_information
            and turn_signal.kind == "affection"
            and self._context_bool(ctx, "suppress_memory_on_low_information", True)
        )
        if skip_long_memory:
            # Low-information turns discard old memory below, so avoid loading
            # large windows that cannot affect this reply.
            core_memories = []
            recent_fact_context = ""
            recent_cross_window_context = ""
        else:
            core_memories = await self.core_memories_for_context(ctx)
            recent_fact_context = await self._recent_fact_guard_context(ctx)
            recent_cross_window_context = await self._recent_cross_window_context(ctx)
        if stage_timer:
            stage_timer.mark("core_memories")
            stage_timer.mark("recent_fact")
            stage_timer.mark("cross_window")
        (
            core_memory_max_chars,
            isolate_request_context,
            isolate_topic_shift,
            topic_shift_reason,
        ) = await self._isolate_low_information_context(
            ctx, low_guard_enabled, turn_signal
        )

        companion_state = detect_private_companion_request(req)
        if stage_timer:
            stage_timer.mark("guards")
        companion_deferred = self._companion_deferred_sections(event, req)
        companion_memory_present = self._companion_memory_context_present(
            companion_state, companion_deferred
        )
        time_intent = parse_time_intent(ctx.message_text)
        decision = self._memory_route_decision(
            ctx,
            turn_signal,
            time_intent,
            isolate_request_context=isolate_request_context,
            topic_shift_reason=topic_shift_reason,
        )
        query_mode = decision.query_mode
        intent = self.intent_builder.build(
            ctx,
            req=req,
            event=event,
            query_mode=query_mode,
        )
        if decision.allow_contextual_expansion:
            intent = await self._expand_contextual_retrieval_intent(
                ctx, intent, turn_signal, req=req
            )
        retrieval_query = self._query_for_time_intent(intent.query, time_intent)
        if stage_timer:
            stage_timer.mark("intent")
        if not retrieval_query and not (
            time_intent.active and time_intent.summary_like
        ):
            return await self._inject_core_without_retrieval(
                core_memories=core_memories,
                core_memory_max_chars=core_memory_max_chars,
                ctx=ctx,
                decision=decision,
                event=event,
                intent=intent,
                recent_cross_window_context=recent_cross_window_context,
                recent_fact_context=recent_fact_context,
                req=req,
                turn_signal=turn_signal,
            )
        blocked: list[dict[str, Any]] = []
        retrieval_path_info: dict[str, Any] = {
            "mode": clean_text(self.config.get("retrieval.mode", "auto"), 40),
            "path": "skipped",
            "reason": "not_started",
        }
        suppress_topic_shift_memory = (
            isolate_topic_shift
            and self._context_bool(ctx, "suppress_memory_on_topic_shift", True)
            and not self._message_requests_memory_context(ctx.message_text)
            and not self._message_requests_personalized_context(ctx.message_text)
        )
        if decision.suppress_long_memory:
            results = []
            slot_map = {}
            blocked.append(
                {
                    "id": "",
                    "reason": decision.suppress_reason or decision.layer,
                    "content": clean_text(ctx.message_text, 180),
                }
            )
            retrieval_path_info = {
                "mode": clean_text(self.config.get("retrieval.mode", "auto"), 40),
                "path": "skipped",
                "reason": decision.suppress_reason or decision.layer,
                "route_layer": decision.layer,
            }
        elif suppress_topic_shift_memory:
            results = []
            slot_map = {}
            blocked.append(
                {
                    "id": "",
                    "reason": "topic_shift_guard:long_term_memory_suppressed",
                    "content": topic_shift_reason,
                }
            )
            retrieval_path_info = {
                "mode": clean_text(self.config.get("retrieval.mode", "auto"), 40),
                "path": "skipped",
                "reason": "topic_shift_guard:long_term_memory_suppressed",
                "route_layer": decision.layer,
            }
        else:
            retrieval_top_k = self._retrieval_top_k_for_query(
                ctx, retrieval_query, time_intent=time_intent
            )
            results, blocked, slot_map = await self.search_context_slots(
                retrieval_query,
                ctx,
                retrieval_top_k,
                time_intent=time_intent if time_intent.active else None,
            )
            retrieval_path_info = dict(self._last_retrieval_path_info or {})
            retrieval_path_info["route_layer"] = decision.layer
        if stage_timer:
            stage_timer.mark("retrieve")
        (
            slot_map,
            results,
            intent_context,
            short_reply_anchor,
            _bot_mood,
            _bot_energy,
            scene_time_of_day,
        ) = await self._shape_recalled_memory_context(
            slot_map=slot_map,
            blocked=blocked,
            companion_deferred=companion_deferred,
            companion_memory_present=companion_memory_present,
            companion_state=companion_state,
            ctx=ctx,
            decision=decision,
            event=event,
            intent=intent,
            retrieval_path_info=retrieval_path_info,
            stage_timer=stage_timer,
            time_intent=time_intent,
        )
        injection_max_chars = self._injection_max_chars_for_query(
            ctx, retrieval_query, time_intent=time_intent
        )
        if short_reply_anchor:
            injection_max_chars = min(injection_max_chars, 1050)
        return await self._publish_selected_memory_context(
            _bot_energy=_bot_energy,
            _bot_mood=_bot_mood,
            blocked=blocked,
            core_memories=core_memories,
            core_memory_max_chars=core_memory_max_chars,
            ctx=ctx,
            event=event,
            injection_max_chars=injection_max_chars,
            intent=intent,
            intent_context=intent_context,
            recent_cross_window_context=recent_cross_window_context,
            recent_fact_context=recent_fact_context,
            req=req,
            results=results,
            retrieval_path_info=retrieval_path_info,
            scene_time_of_day=scene_time_of_day,
            short_reply_anchor=short_reply_anchor,
            slot_map=slot_map,
            stage_timer=stage_timer,
            time_intent=time_intent,
            turn_signal=turn_signal,
        )

    async def _isolate_low_information_context(
        self, ctx, low_guard_enabled, turn_signal
    ):
        """Isolate low information context."""
        core_memory_max_chars = self.config.int("core_memory.max_chars", 800)
        isolate_low_information = False
        isolate_topic_shift = False
        topic_shift_reason = ""
        previous_gap = None
        if low_guard_enabled and turn_signal.low_information:
            if turn_signal.kind == "affection":
                isolate_low_information = True
            else:
                previous_gap = await self._previous_context_gap_minutes(ctx)
                gap_limit = max(
                    0, self._context_int(ctx, "low_information_gap_minutes", 20)
                )
                isolate_low_information = previous_gap is None
                if not isolate_low_information and gap_limit > 0:
                    isolate_low_information = previous_gap >= gap_limit
        elif self._context_bool(ctx, "topic_shift_guard_enabled", True):
            recent_rows = await self.store.recent_timeline(
                limit=self._context_int(ctx, "topic_shift_guard_recent_events", 6),
                scope=ctx.scope,
                session_id=ctx.session_id,
                entity_id=ctx.current_target_id,
            )
            if not self._message_requests_personalized_context(ctx.message_text):
                topic_shift_reason = self._topic_shift_guard_reason(
                    turn_signal, recent_rows
                )
            isolate_topic_shift = bool(topic_shift_reason)

        isolate_request_context = isolate_low_information or isolate_topic_shift
        if isolate_low_information:
            logger.info(
                "[MemoryCompanion] 低信息输入已抑制旧长期记忆并保留原始对话: session=%s kind=%s reason=%s previous_gap=%s",
                ctx.session_id,
                turn_signal.kind,
                turn_signal.reason,
                f"{previous_gap:.1f}m" if previous_gap is not None else "none",
            )
        if isolate_topic_shift:
            logger.info(
                "[MemoryCompanion] 新话题请求已抑制旧长期记忆并保留原始对话: session=%s reason=%s",
                ctx.session_id,
                topic_shift_reason,
            )

        return (
            core_memory_max_chars,
            isolate_request_context,
            isolate_topic_shift,
            topic_shift_reason,
        )
