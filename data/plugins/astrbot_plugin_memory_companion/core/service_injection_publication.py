from __future__ import annotations

from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceInjectionPublication:
    """Shape selected memory and publish bounded request context. Shared resources stay with the root owner."""

    def _log_injection_debug(
        self,
        *,
        ctx: SessionContext,
        intent: Any,
        results: list[Any],
        slot_map: dict[str, list[Any]],
        blocked: list[dict[str, Any]],
        conversation_memory: str,
        intent_context: str,
        injection: str,
        note: str,
        retrieval_info: dict[str, Any] | None = None,
    ) -> None:
        if not self.config.bool("memory_injection.debug_log_injection_enabled", False):
            return
        max_chars = max(
            1000, self.config.int("memory_injection.debug_log_max_chars", 12000)
        )

        def clip(value: Any, limit: int = max_chars) -> str:
            text = self.injection._redact_sensitive_text(value)
            # Keep multiline diagnostics readable across Windows and Unix logs.
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if len(text) > limit:
                return text[: max(0, limit - 1)].rstrip() + "…"
            return text

        slot_lines: list[str] = []
        for slot, items in (slot_map or {}).items():
            if not items:
                continue
            slot_lines.append(f"[{slot}] {len(items)}")
            for item in items[:10]:
                memory = getattr(item, "memory", None)
                if memory is None:
                    continue
                slot_lines.append(
                    "  - "
                    + " | ".join(
                        [
                            f"id={clean_text(memory.id, 120)}",
                            f"type={clean_text(memory.memory_type, 60)}",
                            f"score={float(getattr(item, 'score', 0.0) or 0.0):.2f}",
                            f"scope={clean_text(memory.scope, 40)}",
                            f"visibility={clean_text(memory.visibility, 40)}",
                            f"reason={clean_text(getattr(item, 'reason', ''), 180)}",
                            f"expression={self._expression_from_reason(getattr(item, 'reason', ''))}",
                            f"content={clean_text(memory.content, 360)}",
                        ]
                    )
                )
        if not slot_lines and results:
            slot_lines.append("[selected] no_slot_map")
            for item in results[:10]:
                memory = getattr(item, "memory", None)
                if memory is None:
                    continue
                slot_lines.append(
                    "  - "
                    + " | ".join(
                        [
                            f"id={clean_text(memory.id, 120)}",
                            f"type={clean_text(memory.memory_type, 60)}",
                            f"score={float(getattr(item, 'score', 0.0) or 0.0):.2f}",
                            f"expression={self._expression_from_reason(getattr(item, 'reason', ''))}",
                            f"content={clean_text(memory.content, 360)}",
                        ]
                    )
                )
        blocked_lines = []
        for item in (blocked or [])[:20]:
            parts = [
                f"id={clean_text(item.get('id') or item.get('memory_id'), 120)}",
                f"reason={clean_text(item.get('reason'), 220)}",
            ]
            content = clean_text(item.get("content"), 220)
            if content:
                parts.append(f"content={content}")
            blocked_lines.append("  - " + " | ".join(parts))
        retrieval_parts = [
            f"mode={retrieval_info.get('mode')}" if retrieval_info else "mode=",
            f"path={retrieval_info.get('path')}" if retrieval_info else "path=",
            f"layer={retrieval_info.get('route_layer')}"
            if retrieval_info and retrieval_info.get("route_layer")
            else "",
            f"provider={retrieval_info.get('provider_id')}"
            if retrieval_info
            else "provider=",
            f"reason={retrieval_info.get('reason')}" if retrieval_info else "reason=",
            f"candidates={retrieval_info.get('candidate_count')}"
            if retrieval_info and retrieval_info.get("candidate_count") is not None
            else "",
            f"pool={retrieval_info.get('rerank_pool')}"
            if retrieval_info and retrieval_info.get("rerank_pool") is not None
            else "",
            f"filtered={retrieval_info.get('rerank_filtered')}"
            if retrieval_info and retrieval_info.get("rerank_filtered") is not None
            else "",
            f"reranked={retrieval_info.get('reranked_count')}"
            if retrieval_info and retrieval_info.get("reranked_count") is not None
            else "",
            f"query_chars={retrieval_info.get('rerank_query_chars')}"
            if retrieval_info and retrieval_info.get("rerank_query_chars") is not None
            else "",
            f"docs={retrieval_info.get('rerank_document_count')}"
            if retrieval_info
            and retrieval_info.get("rerank_document_count") is not None
            else "",
            f"doc_min={retrieval_info.get('rerank_document_min_chars')}"
            if retrieval_info
            and retrieval_info.get("rerank_document_min_chars") is not None
            else "",
            f"doc_max={retrieval_info.get('rerank_document_max_chars')}"
            if retrieval_info
            and retrieval_info.get("rerank_document_max_chars") is not None
            else "",
            f"model_chars={retrieval_info.get('rerank_model_chars')}"
            if retrieval_info and retrieval_info.get("rerank_model_chars") is not None
            else "",
            f"anchors={retrieval_info.get('lexical_anchors')}"
            if retrieval_info and retrieval_info.get("lexical_anchors") is not None
            else "",
            f"embedding={retrieval_info.get('embedding_reason')}"
            if retrieval_info and retrieval_info.get("embedding_reason") is not None
            else "",
            f"emb_provider={retrieval_info.get('embedding_provider_id')}"
            if retrieval_info
            and retrieval_info.get("embedding_provider_id") is not None
            else "",
            f"emb_candidates={retrieval_info.get('embedding_candidates')}"
            if retrieval_info and retrieval_info.get("embedding_candidates") is not None
            else "",
            f"emb_hits={retrieval_info.get('embedding_hits')}"
            if retrieval_info and retrieval_info.get("embedding_hits") is not None
            else "",
        ]
        summary = "\n".join(
            [
                "========== MemoryCompanion 注入调试 ==========",
                f"note: {clean_text(note, 80)}",
                f"session: {clean_text(ctx.session_id, 200)}",
                f"scope: {clean_text(ctx.scope, 40)}",
                f"target: {clean_text(ctx.label, 240)}",
                f"bot_id: {clean_text(ctx.bot_id, 120) or '<missing>'}",
                f"query_source: {clean_text(getattr(intent, 'source', ''), 80)}",
                f"query: {clean_text(getattr(intent, 'query', ''), 1000)}",
                f"current_user_message: {clean_text(ctx.message_text, 1000)}",
                "retrieval_path: "
                + clean_text(
                    " | ".join(part for part in retrieval_parts if part),
                    800,
                ),
                f"selected_count: {len(results or [])}",
                f"blocked_count: {len(blocked or [])}",
                f"conversation_memory_note: {clean_text(conversation_memory, 300)}",
                f"intent_context_chars: {len(intent_context or '')}",
                f"injection_chars: {len(injection or '')}",
                "",
                "[slot_memories]",
                "\n".join(slot_lines) if slot_lines else "  - none",
                "",
                "[blocked_examples]",
                "\n".join(blocked_lines) if blocked_lines else "  - none",
                "",
                "[intent_context]",
                clip(intent_context),
                "",
                "[conversation_memory]",
                clip(conversation_memory),
                "",
                "[actual_injection]",
                clip(injection),
                "========== MemoryCompanion 注入调试结束 ==========",
            ]
        )
        logger.info("%s", clip(summary))

    async def _publish_selected_memory_context(
        self,
        *,
        _bot_energy,
        _bot_mood,
        blocked,
        core_memories,
        core_memory_max_chars,
        ctx,
        event,
        injection_max_chars,
        intent,
        intent_context,
        recent_cross_window_context,
        recent_fact_context,
        req,
        results,
        retrieval_path_info,
        scene_time_of_day,
        short_reply_anchor,
        slot_map,
        stage_timer,
        time_intent,
        turn_signal,
    ):
        """Publish selected memory context."""
        actual_injected_memory_ids: list[str] = []
        injection = self.injection.compose(
            ctx,
            results,
            injection_max_chars,
            intent_context=intent_context,
            slot_sections=self._slot_sections(slot_map),
            compact_memory=bool(short_reply_anchor)
            or time_intent.active
            or self._message_requests_temporal_aggregate(
                ctx.message_text or intent.query
            ),
            time_context=time_intent.display_range if time_intent.active else "",
            emotional_tone=getattr(turn_signal, "emotional_tone", "neutral"),
            intimacy_level=getattr(turn_signal, "intimacy_level", 0.0),
            companion_bot_mood=_bot_mood,
            companion_bot_energy=_bot_energy,
            time_of_day=scene_time_of_day,
            cross_window_emotional_hint=self._get_cross_window_emotional_hint(ctx),
            address_hint=self._address_hint_for_injection(ctx),
            recent_fact_context=recent_fact_context,
            recent_cross_window_context=recent_cross_window_context,
            included_memory_ids=actual_injected_memory_ids,
            core_memories=core_memories,
            core_memory_max_chars=core_memory_max_chars,
            max_item_chars=self.config.int("memory_injection.max_item_chars", 320),
        )
        injection_omissions, _diagnostic_included_memory_ids = (
            self.injection.diagnostic_snapshot()
        )
        blocked.extend(injection_omissions)
        if stage_timer:
            stage_timer.mark("compose")
        conversation_memory_note = self._conversation_memory_injection_note(
            slot_map,
            recent_fact_context=(
                recent_fact_context if "<recent_fact_context>" in injection else ""
            ),
        )
        await self._detect_and_queue_emotional_events(
            ctx,
            results,
            companion_bot_mood=_bot_mood,
            companion_bot_energy=_bot_energy,
            emotional_tone=getattr(turn_signal, "emotional_tone", "neutral"),
        )
        self._maybe_record_persona_touch(
            ctx,
            results,
            emotional_tone=getattr(turn_signal, "emotional_tone", "neutral"),
        )
        if stage_timer:
            stage_timer.mark("emotion")
        self._log_injection_debug(
            ctx=ctx,
            intent=intent,
            results=results,
            slot_map=slot_map,
            blocked=blocked,
            conversation_memory=conversation_memory_note,
            intent_context=intent_context,
            injection=injection,
            note="composed" if injection else "no_injection_body",
            retrieval_info=retrieval_path_info,
        )
        published = False
        if injection:
            published = await self._publish_reply_memory_context(
                ctx,
                req,
                event=event,
                injection=injection,
                slot_map=slot_map,
                strategy_id="existing.search_context_slots",
            )
        else:
            self._mark_memory_companion_injection_state(
                event, req, injected=False, conversation_memory=False, slot_map=slot_map
            )
        if not published:
            reason = self._memory_companion_injection_payload(req).get("reason_code")
            if reason:
                blocked.append({"id": "", "reason": reason, "content": ""})
        if self.config.bool("memory_injection.enable_injection_logs", True):
            await self.store.add_injection_log(
                session_id=ctx.session_id,
                scope=ctx.scope,
                query=intent.query,
                selected_memory_ids=actual_injected_memory_ids if published else [],
                blocked_reasons=blocked[:30],
                injection_chars=len(injection) if published else 0,
            )
        if stage_timer:
            stage_timer.mark("compose_log")

    async def _shape_recalled_memory_context(
        self,
        *,
        slot_map,
        blocked,
        companion_deferred,
        companion_memory_present,
        companion_state,
        ctx,
        decision,
        event,
        intent,
        retrieval_path_info,
        stage_timer,
        time_intent,
    ):
        """Shape recalled memory context."""
        slot_map, current_state_reasons = self._filter_current_state_memory_slots(
            ctx, slot_map
        )
        if current_state_reasons:
            blocked.extend(
                {
                    "id": "",
                    "reason": reason,
                    "content": clean_text(ctx.message_text, 180),
                }
                for reason in current_state_reasons
            )
            results = self._flatten_slot_map(slot_map)
        slot_map, group_actor_reasons = self._filter_group_actor_memory_slots(
            ctx,
            slot_map,
            query_text=intent.query or ctx.message_text,
        )
        if group_actor_reasons:
            blocked.extend(group_actor_reasons)
            results = self._flatten_slot_map(slot_map)
        slot_map, _ = self._apply_companion_dedupe(
            slot_map,
            companion_state,
            companion_memory_present,
            companion_deferred,
            blocked,
        )
        results = self._flatten_slot_map(slot_map)

        await self._add_time_window_timeline_slot(ctx, slot_map, time_intent)
        slot_map = self._apply_memory_expression_policy(
            ctx,
            slot_map,
            decision,
            time_intent,
            query_text=intent.query or ctx.message_text,
        )
        short_reply_anchor, slot_map = self._apply_short_reply_chain_policy(
            ctx,
            event,
            slot_map,
            blocked,
            retrieval_path_info,
        )
        results = self._flatten_slot_map(slot_map)
        if stage_timer:
            stage_timer.mark("filter_policy")
        intent_context = self._intent_context_for_injection(
            intent, time_intent=time_intent
        )
        if decision.guard_lines:
            guard_text = "\n".join(decision.guard_lines)
            intent_context = (
                f"{guard_text}\n{intent_context}" if intent_context else guard_text
            )
        if short_reply_anchor:
            short_reply_guard = self._short_reply_chain_guard()
            intent_context = (
                f"{short_reply_guard}\n{intent_context}"
                if intent_context
                else short_reply_guard
            )

        _bot_mood = getattr(intent, "companion_bot_mood", "") or ""
        _bot_energy = getattr(intent, "companion_bot_energy", 0.0) or 0.0
        scene_time_of_day = self._compute_time_of_day()
        slot_map = self._apply_scar_scene_gate(
            ctx,
            slot_map,
            companion_bot_energy=_bot_energy,
            time_of_day=scene_time_of_day,
        )
        results = self._flatten_slot_map(slot_map)

        return (
            slot_map,
            results,
            intent_context,
            short_reply_anchor,
            _bot_mood,
            _bot_energy,
            scene_time_of_day,
        )

    async def _inject_core_without_retrieval(
        self,
        *,
        core_memories,
        core_memory_max_chars,
        ctx,
        decision,
        event,
        intent,
        recent_cross_window_context,
        recent_fact_context,
        req,
        turn_signal,
    ):
        """Inject core without retrieval."""
        retrieval_path_info = {
            "mode": clean_text(self.config.get("retrieval.mode", "auto"), 40),
            "path": "skipped",
            "reason": "empty_retrieval_query",
            "route_layer": decision.layer,
        }
        blocked = [{"id": "", "reason": "empty_retrieval_query", "content": ""}]
        intent_context = "\n".join(decision.guard_lines)
        injection = ""
        actual_injected_memory_ids: list[str] = []
        if core_memories or recent_fact_context or recent_cross_window_context:
            injection = self.injection.compose(
                ctx,
                [],
                self.config.int("memory_injection.max_chars", 4000),
                intent_context=intent_context,
                emotional_tone=getattr(turn_signal, "emotional_tone", "neutral"),
                intimacy_level=getattr(turn_signal, "intimacy_level", 0.0),
                companion_bot_mood=getattr(intent, "companion_bot_mood", "") or "",
                companion_bot_energy=getattr(intent, "companion_bot_energy", 0.0)
                or 0.0,
                time_of_day=self._compute_time_of_day(),
                recent_fact_context=recent_fact_context,
                recent_cross_window_context=recent_cross_window_context,
                core_memories=core_memories,
                core_memory_max_chars=core_memory_max_chars,
                included_memory_ids=actual_injected_memory_ids,
                max_item_chars=self.config.int("memory_injection.max_item_chars", 320),
            )
        self._log_injection_debug(
            ctx=ctx,
            intent=intent,
            results=[],
            slot_map={},
            blocked=blocked,
            conversation_memory=self._conversation_memory_injection_note(
                {},
                recent_fact_context=(
                    recent_fact_context if "<recent_fact_context>" in injection else ""
                ),
            ),
            intent_context=intent_context,
            injection=injection,
            note="empty_retrieval_query",
            retrieval_info=retrieval_path_info,
        )
        published = False
        if injection:
            published = await self._publish_reply_memory_context(
                ctx,
                req,
                event=event,
                injection=injection,
                slot_map={},
                strategy_id="existing.core_and_recent_context",
            )
        if self.config.bool("memory_injection.enable_injection_logs", True):
            await self.store.add_injection_log(
                session_id=ctx.session_id,
                scope=ctx.scope,
                query="",
                selected_memory_ids=actual_injected_memory_ids if published else [],
                blocked_reasons=blocked,
                injection_chars=len(injection) if published else 0,
            )
        return
