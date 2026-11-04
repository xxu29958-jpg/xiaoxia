from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from .astrbot_compat import (
    detect_private_companion_request,
)
from .context_orchestrator import RetrievalIntent
from .models import (
    EntityRef,
    MemoryRecord,
    SearchResult,
    SessionContext,
    clean_text,
    json_loads,
)
from .service_recall_routing import MemoryCompanionServiceRecallRouting
from .time_intent import TimeIntent, parse_time_intent
from .turn_signal import analyze_turn_signal

if TYPE_CHECKING:
    from .service import MemoryCompanionService

# The facade is the runtime host of this mixin. These local casts bind shared
# members to its real definitions; they do not suppress member or call checks.


class MemoryCompanionServiceInjectionContext:
    """service injection context; the facade owns all shared resources."""

    async def _compose_memory_injection(
        self,
        ctx: SessionContext,
        *,
        req: Any = None,
        event: Any = None,
        explicit_query: str = "",
        top_k: int | None = None,
        max_chars: int | None = None,
        note: str = "composed",
        write_log: bool = True,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
    ) -> str:
        self = cast("MemoryCompanionService", self)
        ctx = self._normalized_session_context(ctx)
        core_memories = await self.core_memories_for_context(ctx)
        core_memory_max_chars = self.config.int("core_memory.max_chars", 800)
        recent_fact_context = await self._recent_fact_guard_context(ctx)
        recent_cross_window_context = await self._recent_cross_window_context(ctx)
        turn_signal = analyze_turn_signal(explicit_query or ctx.message_text)
        time_intent = parse_time_intent(explicit_query or ctx.message_text)
        route_text = explicit_query or ctx.message_text
        decision = self._memory_route_decision(
            ctx, turn_signal, time_intent, route_text=route_text
        )
        query_mode = decision.query_mode
        intent = self.intent_builder.build(
            ctx,
            req=req,
            event=event,
            explicit_query=explicit_query,
            query_mode=query_mode,
        )
        if decision.allow_contextual_expansion:
            intent = await self._expand_contextual_retrieval_intent(
                ctx, intent, turn_signal, req=req
            )
        retrieval_query = self._query_for_time_intent(intent.query, time_intent)

        static_included_memory_ids: list[str] = []

        def compose_recent_guard(intent_context: str) -> str:
            if (
                not core_memories
                and not recent_fact_context
                and not recent_cross_window_context
            ):
                return ""
            return self.injection.compose(
                ctx,
                [],
                max_chars or self.config.int("memory_injection.max_chars", 4000),
                intent_context=intent_context,
                emotional_tone=getattr(turn_signal, "emotional_tone", "neutral"),
                intimacy_level=getattr(turn_signal, "intimacy_level", 0.0),
                companion_bot_mood=companion_bot_mood,
                companion_bot_energy=companion_bot_energy,
                time_of_day=self._compute_time_of_day(),
                recent_fact_context=recent_fact_context,
                recent_cross_window_context=recent_cross_window_context,
                core_memories=core_memories,
                core_memory_max_chars=core_memory_max_chars,
                included_memory_ids=static_included_memory_ids,
                max_item_chars=self.config.int("memory_injection.max_item_chars", 320),
            )

        if decision.suppress_long_memory:
            retrieval_path_info = {
                "mode": clean_text(self.config.get("retrieval.mode", "auto"), 40),
                "path": "skipped",
                "reason": decision.suppress_reason or decision.layer,
                "route_layer": decision.layer,
            }
            blocked = [
                {
                    "id": "",
                    "reason": decision.suppress_reason or decision.layer,
                    "content": clean_text(ctx.message_text, 180),
                }
            ]
            intent_context = "\n".join(decision.guard_lines)
            injection = compose_recent_guard(intent_context)
            self._log_injection_debug(
                ctx=ctx,
                intent=intent,
                results=[],
                slot_map={},
                blocked=blocked,
                conversation_memory=self._conversation_memory_injection_note(
                    {},
                    recent_fact_context=(
                        recent_fact_context
                        if "<recent_fact_context>" in injection
                        else ""
                    ),
                ),
                intent_context=intent_context,
                injection=injection,
                note=f"{note}:route_suppressed",
                retrieval_info=retrieval_path_info,
            )
            if write_log and self.config.bool(
                "memory_injection.enable_injection_logs", True
            ):
                await self.store.add_injection_log(
                    session_id=ctx.session_id,
                    scope=ctx.scope,
                    query=intent.query,
                    selected_memory_ids=static_included_memory_ids,
                    blocked_reasons=blocked,
                    injection_chars=len(injection),
                )
            return injection
        if not retrieval_query and not (
            time_intent.active and time_intent.summary_like
        ):
            retrieval_path_info = {
                "mode": clean_text(self.config.get("retrieval.mode", "auto"), 40),
                "path": "skipped",
                "reason": "empty_retrieval_query",
                "route_layer": decision.layer,
            }
            blocked = [{"id": "", "reason": "empty_retrieval_query", "content": ""}]
            injection = compose_recent_guard("")
            self._log_injection_debug(
                ctx=ctx,
                intent=intent,
                results=[],
                slot_map={},
                blocked=blocked,
                conversation_memory=self._conversation_memory_injection_note(
                    {},
                    recent_fact_context=(
                        recent_fact_context
                        if "<recent_fact_context>" in injection
                        else ""
                    ),
                ),
                intent_context="",
                injection=injection,
                note="empty_retrieval_query",
                retrieval_info=retrieval_path_info,
            )
            if write_log and self.config.bool(
                "memory_injection.enable_injection_logs", True
            ):
                await self.store.add_injection_log(
                    session_id=ctx.session_id,
                    scope=ctx.scope,
                    query="",
                    selected_memory_ids=static_included_memory_ids,
                    blocked_reasons=blocked,
                    injection_chars=len(injection),
                )
            return injection

        results, blocked, slot_map = await self.search_context_slots(
            retrieval_query,
            ctx,
            top_k
            or self._retrieval_top_k_for_query(
                ctx, retrieval_query, time_intent=time_intent
            ),
            time_intent=time_intent if time_intent.active else None,
        )
        retrieval_path_info = dict(self._last_retrieval_path_info or {})
        retrieval_path_info["route_layer"] = decision.layer
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
            query_text=intent.query or route_text,
        )
        if group_actor_reasons:
            blocked.extend(group_actor_reasons)
            results = self._flatten_slot_map(slot_map)

        companion_state = (
            detect_private_companion_request(req) if req is not None else {}
        )
        companion_deferred = self._companion_deferred_sections(event, req)
        companion_memory_present = self._companion_memory_context_present(
            companion_state, companion_deferred
        )
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
            query_text=intent.query or route_text,
        )
        short_reply_anchor, slot_map = self._apply_short_reply_chain_policy(
            ctx,
            event,
            slot_map,
            blocked,
            retrieval_path_info,
        )
        results = self._flatten_slot_map(slot_map)
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
        # Merge companion emotional state: explicit params take priority, fall back to intent-extracted values
        merged_bot_mood = (
            companion_bot_mood or getattr(intent, "companion_bot_mood", "") or ""
        )
        merged_bot_energy = (
            companion_bot_energy or getattr(intent, "companion_bot_energy", 0.0) or 0.0
        )
        scene_time_of_day = self._compute_time_of_day()
        slot_map = self._apply_scar_scene_gate(
            ctx,
            slot_map,
            companion_bot_energy=merged_bot_energy,
            time_of_day=scene_time_of_day,
        )
        results = self._flatten_slot_map(slot_map)
        injection_max_chars = max_chars or self._injection_max_chars_for_query(
            ctx,
            retrieval_query,
            time_intent=time_intent,
        )
        if short_reply_anchor:
            injection_max_chars = min(injection_max_chars, 1050)
        included_memory_ids: list[str] = []
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
            companion_bot_mood=merged_bot_mood,
            companion_bot_energy=merged_bot_energy,
            time_of_day=scene_time_of_day,
            cross_window_emotional_hint=self._get_cross_window_emotional_hint(ctx),
            address_hint=self._address_hint_for_injection(ctx),
            recent_fact_context=recent_fact_context,
            recent_cross_window_context=recent_cross_window_context,
            included_memory_ids=included_memory_ids,
            core_memories=core_memories,
            core_memory_max_chars=core_memory_max_chars,
            max_item_chars=self.config.int("memory_injection.max_item_chars", 320),
        )
        injection_omissions, _diagnostic_included_memory_ids = (
            self.injection.diagnostic_snapshot()
        )
        blocked.extend(injection_omissions)
        conversation_memory_note = self._conversation_memory_injection_note(
            slot_map,
            recent_fact_context=(
                recent_fact_context if "<recent_fact_context>" in injection else ""
            ),
        )
        await self._detect_and_queue_emotional_events(
            ctx,
            results,
            companion_bot_mood=merged_bot_mood,
            companion_bot_energy=merged_bot_energy,
            emotional_tone=getattr(turn_signal, "emotional_tone", "neutral"),
        )
        self._maybe_record_persona_touch(
            ctx,
            results,
            emotional_tone=getattr(turn_signal, "emotional_tone", "neutral"),
        )
        self._log_injection_debug(
            ctx=ctx,
            intent=intent,
            results=results,
            slot_map=slot_map,
            blocked=blocked,
            conversation_memory=conversation_memory_note,
            intent_context=intent_context,
            injection=injection,
            note=note if injection else f"{note}:no_injection_body",
            retrieval_info=retrieval_path_info,
        )
        if write_log and self.config.bool(
            "memory_injection.enable_injection_logs", True
        ):
            await self.store.add_injection_log(
                session_id=ctx.session_id,
                scope=ctx.scope,
                query=intent.query,
                selected_memory_ids=included_memory_ids,
                blocked_reasons=blocked[:30],
                injection_chars=len(injection),
            )
        return injection

    def _mark_memory_companion_injection_state(
        self,
        event: Any,
        req: Any,
        *,
        injected: bool,
        conversation_memory: bool,
        slot_map: dict[str, list[Any]],
        memory_refs: list[dict[str, str]] | None = None,
        continuity_snapshot: dict[str, Any] | None = None,
        reason_code: str = "",
    ) -> None:
        self = cast("MemoryCompanionService", self)
        selected_ids: list[str] = []
        seen: set[str] = set()
        for _slot, items in (slot_map or {}).items():
            for item in items or []:
                memory = getattr(item, "memory", None)
                memory_id = clean_text(getattr(memory, "id", ""), 120)
                if not memory_id:
                    continue
                if memory_id not in seen:
                    selected_ids.append(memory_id)
                    seen.add(memory_id)
        injected_ids = {ref["id"] for ref in memory_refs or []} if injected else set()
        payload = {
            "active": True,
            "injected": bool(injected),
            "conversation_memory": bool(conversation_memory),
            "slots": [slot for slot, items in slot_map.items() if items],
            "selected_memory_ids": selected_ids,
            # Compatibility field: injection alone establishes no feedback target.
            "feedback_target_memory_ids": [],
            "injected_memory_ids": sorted(injected_ids),
            "memory_refs": list(memory_refs or []) if injected else [],
            "snapshot_id": continuity_snapshot.get("snapshot_id")
            if injected and continuity_snapshot
            else None,
            "reason_code": reason_code,
        }
        self._remember_observed_refs(event, payload["memory_refs"])
        for target in (event, req):
            if target is None:
                continue
            try:
                setattr(target, "memory_companion_injection_state", deepcopy(payload))
                setattr(
                    target,
                    "memory_companion_continuity_snapshot",
                    deepcopy(continuity_snapshot) if injected else None,
                )
            except Exception:
                pass

    @staticmethod
    def _memory_companion_injection_payload(target: Any) -> dict[str, Any]:
        payload = getattr(target, "memory_companion_injection_state", None)
        if isinstance(payload, dict):
            return payload
        return {}

    def _query_for_time_intent(self, query: str, time_intent: TimeIntent) -> str:
        if time_intent.active and time_intent.summary_like:
            return ""
        return clean_text(query, 1400)

    def _retrieval_top_k_for_query(
        self, ctx: SessionContext, query: str, *, time_intent: TimeIntent | None = None
    ) -> int:
        self = cast("MemoryCompanionService", self)
        base = self.config.int("memory_injection.top_k", 4)
        if (
            time_intent is not None and time_intent.active
        ) or self._message_requests_temporal_aggregate(ctx.message_text or query):
            if time_intent is not None and time_intent.summary_like:
                return max(base, 12)
            return max(base, 10)
        return base

    def _injection_max_chars_for_query(
        self, ctx: SessionContext, query: str, *, time_intent: TimeIntent | None = None
    ) -> int:
        self = cast("MemoryCompanionService", self)
        base = self.config.int("memory_injection.max_chars", 4000)
        if (
            time_intent is not None and time_intent.active
        ) or self._message_requests_temporal_aggregate(ctx.message_text or query):
            return max(
                base,
                self.config.int("memory_injection.temporal_aggregate_max_chars", 3600),
            )
        return base

    def _slot_limits(
        self, top_k: int, *, query: str = "", time_intent: TimeIntent | None = None
    ) -> dict[str, int]:
        self = cast("MemoryCompanionService", self)
        total = max(1, int(top_k or 1))
        conversation_limit = self.config.int(
            "context_orchestration.conversation_summary_limit", 2
        )
        open_loop_limit = 1
        if self._message_is_vague_recent_followup(query):
            open_loop_limit = min(total, 3)
        if (
            time_intent is not None and time_intent.active
        ) or self._message_requests_temporal_aggregate(query):
            conversation_limit = max(conversation_limit, min(total, 8))
            open_loop_limit = max(open_loop_limit, min(total, 2))
        return {
            "open_loop": min(total, open_loop_limit),
            "self_timeline": min(
                total, self.config.int("context_orchestration.self_timeline_limit", 2)
            ),
            "user_profile": min(
                total, self.config.int("context_orchestration.user_profile_limit", 2)
            ),
            "current_window": min(
                total, self.config.int("context_orchestration.current_window_limit", 3)
            ),
            "conversation_summary": min(total, conversation_limit),
            "stable_memory": min(
                total, self.config.int("context_orchestration.stable_memory_limit", 3)
            ),
        }

    def _slot_capped_slots(
        self,
        query: str,
        *,
        time_intent: TimeIntent | None = None,
        bot_id: str = "",
    ) -> set[str]:
        # Both mutable personal timelines and unresolved commitments can be
        # numerous.  Keep them inside their diversity budgets for ordinary
        # questions; an explicit owner/focus may opt one of them into overflow.
        self = cast("MemoryCompanionService", self)
        capped = {"self_timeline", "open_loop"}
        if self._query_focuses_self_timeline(
            query, time_intent=time_intent, bot_id=bot_id
        ):
            capped.discard("self_timeline")
        if self._query_focuses_open_loop(query):
            capped.discard("open_loop")
        return capped

    @staticmethod
    def _query_focuses_open_loop(query: str) -> bool:
        """Recognize an explicit unresolved-workflow request.

        These are positive workflow forms, so ordinary schedule words such as
        "明天" or "提醒" in a stored record cannot grant open-loop overflow by
        themselves.
        """
        compact = re.sub(r"\s+", "", clean_text(query, 1000)).lower()
        if not compact:
            return False
        if MemoryCompanionServiceRecallRouting._message_is_vague_recent_followup(
            compact
        ):
            return True
        patterns = (
            r"(?:未完成|没完成|没做完|没写完|待办|待续|未了事项|没聊完|还欠着)",
            r"(?:承诺|答应|约定).{0,12}(?:还记得|完成|兑现|做到|没做)",
            r"(?:提醒|下次|回头).{0,12}(?:什么|哪些|还有|没)",
            r"(?:open[ _-]?loop|unfinished|todo|promise|commitment)",
        )
        return any(re.search(pattern, compact) for pattern in patterns)

    def _parse_iso(self, value: str) -> datetime | None:
        value = clean_text(value, 80)
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            return None

    def _config_percent(self, dotted: str, default: int) -> float:
        self = cast("MemoryCompanionService", self)
        value = max(0, min(100, self.config.int(dotted, default)))
        return value / 100.0

    def _context_value(self, ctx: SessionContext, key: str, default: Any) -> Any:
        self = cast("MemoryCompanionService", self)
        marker = object()
        scope = clean_text(ctx.scope, 40)
        if scope in {"private", "group"}:
            value = self.config.get(f"conversation_memory.{scope}.{key}", marker)
            if value is not marker:
                return value
        return self.config.get(f"conversation_memory.{key}", default)

    def _context_bool(self, ctx: SessionContext, key: str, default: bool) -> bool:
        value = self._context_value(ctx, key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "开", "开启"}
        return bool(value)

    def _context_int(self, ctx: SessionContext, key: str, default: int) -> int:
        try:
            return int(self._context_value(ctx, key, default))
        except Exception:
            return default

    def _context_float(self, ctx: SessionContext, key: str, default: float) -> float:
        try:
            return float(self._context_value(ctx, key, default))
        except Exception:
            return default

    def _context_str(self, ctx: SessionContext, key: str, default: str = "") -> str:
        return str(self._context_value(ctx, key, default) or "")

    def _intent_context_for_injection(
        self, intent: RetrievalIntent, *, time_intent: TimeIntent | None = None
    ) -> str:
        self = cast("MemoryCompanionService", self)
        if not self.config.bool("context_orchestration.include_intent_context", True):
            base = ""
        else:
            base = intent.format_for_injection(
                self.config.int("context_orchestration.intent_max_chars", 520)
            )
        if time_intent is not None and time_intent.active:
            line = f"- 时间窗口：{time_intent.display_range}（{time_intent.label or time_intent.source}）"
            if time_intent.summary_like:
                line += "；本轮按时间聚合摘要优先。"
            return f"{line}\n{base}" if base else line
        return base

    async def _add_time_window_timeline_slot(
        self,
        ctx: SessionContext,
        slot_map: dict[str, list[Any]],
        time_intent: TimeIntent,
    ) -> None:
        self = cast("MemoryCompanionService", self)
        if not time_intent.active:
            return
        limit = self.config.int("conversation_memory.time_window_timeline_limit", 12)
        if limit <= 0:
            return
        rows = await self.store.timeline_window(
            start_at=time_intent.start_at,
            end_at=time_intent.end_at,
            limit=limit,
            scope=ctx.scope,
            session_id=ctx.session_id,
            entity_id=ctx.current_target_id,
        )
        items: list[SearchResult] = []
        for row in reversed(rows):
            record = self._timeline_row_as_memory(ctx, row)
            if record is not None:
                items.append(
                    SearchResult(
                        memory=record, score=1.0, reason="time_window_timeline"
                    )
                )
        if not items:
            return
        existing = list(slot_map.get("time_window_timeline") or [])
        slot_map["time_window_timeline"] = [*existing, *items]

    def _timeline_row_as_memory(
        self, ctx: SessionContext, row: dict[str, Any]
    ) -> MemoryRecord | None:
        self = cast("MemoryCompanionService", self)
        content = clean_text(row.get("content"), 600)
        if not content or self._timeline_content_is_internal_placeholder(content):
            return None
        event_type = clean_text(row.get("event_type"), 60)
        subject_id = clean_text(row.get("subject_id"), 120)
        metadata = json_loads(row.get("metadata"), {})
        sender_name = clean_text(metadata.get("sender_name"), 120)
        if event_type == "bot_response" or subject_id == "self":
            subject = self._bot_entity(ctx)
            prefix = "Bot"
        else:
            subject = EntityRef(
                kind="user", id=subject_id, name=sender_name, role="timeline_speaker"
            )
            prefix = sender_name or subject_id or "用户"
        occurred = clean_text(
            str(row.get("occurred_at") or row.get("created_at") or ""), 80
        )
        return MemoryRecord(
            id=f"timeline_{clean_text(row.get('id'), 100)}",
            memory_type="timeline_event",
            subject=subject,
            object=EntityRef(
                kind="group" if ctx.scope == "group" else "user",
                id=ctx.current_target_id,
                name=ctx.group_name or ctx.user_name,
                role="current_window",
            ),
            scope=ctx.scope,
            session_id=ctx.session_id,
            platform=clean_text(metadata.get("platform"), 80) or ctx.platform,
            group_id=ctx.group_id,
            visibility="group_public" if ctx.scope == "group" else "private_pair",
            sayability="indirect",
            reality_level="observed_utterance",
            lifecycle="raw_event",
            content=f"{prefix}：{content}",
            evidence=content,
            confidence=0.74,
            importance=0.42,
            review_status="auto",
            tags=["timeline", ctx.scope, event_type],
            metadata={
                "source": "time_window_timeline",
                "event_type": event_type,
                "source_ref": f"timeline:{clean_text(row.get('id'), 100)}",
                "owner_bot_id": clean_text(metadata.get("owner_bot_id"), 120),
                "persona_id": clean_text(metadata.get("persona_id"), 120),
            },
            created_at=clean_text(str(row.get("created_at") or occurred), 80),
            updated_at=clean_text(str(row.get("created_at") or occurred), 80),
            occurred_at=occurred,
            source_plugin="memory_companion",
        )

    def _slot_sections(
        self, slot_map: dict[str, list[Any]]
    ) -> list[tuple[str, list[Any]]]:
        labels = {
            "time_window_timeline": "time_window_timeline",
            "open_loop": "open_loop",
            "self_timeline": "bot_self_timeline",
            "user_profile": "current_user_profile",
            "current_window": "current_window_memory",
            "conversation_summary": "conversation_continuity",
            "stable_memory": "stable_memory",
        }
        sections: list[tuple[str, list[Any]]] = []
        for key in [
            "time_window_timeline",
            "open_loop",
            "self_timeline",
            "user_profile",
            "current_window",
            "conversation_summary",
            "stable_memory",
        ]:
            items = slot_map.get(key) or []
            if items:
                sections.append((labels[key], items))
        return sections

    @staticmethod
    def _conversation_memory_injection_note(
        slot_map: dict[str, list[Any]],
        *,
        recent_fact_context: str = "",
    ) -> str:
        count = len(slot_map.get("conversation_summary") or [])
        fact_guard = 1 if clean_text(recent_fact_context, 20) else 0
        return f"conversation_summary_hits={count}; raw_window_injection=0; recent_fact_guard={fact_guard}"

    def _filter_recent_context_rows(
        self, ctx: SessionContext, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for row in rows:
            content = clean_text(row.get("content"), 2000)
            if self._timeline_content_is_internal_placeholder(content):
                continue
            kept.append(row)
        return kept

    def _timeline_content_is_internal_placeholder(self, content: str) -> bool:
        compact = clean_text(content, 500)
        if not compact:
            return False
        lowered = compact.lower()
        if compact.startswith("[SYSTEM:") or compact.startswith("【SYSTEM:"):
            return True
        return (
            "user actively interrupted" in lowered
            or "partial output before interruption" in lowered
        )
