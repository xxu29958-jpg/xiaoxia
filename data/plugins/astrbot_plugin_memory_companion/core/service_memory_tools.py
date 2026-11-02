from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .astrbot_compat import (
    append_temp_text,
    logger,
    remove_marked_text,
    remove_temp_text,
)
from .bridge import (
    serialize_memory,
)
from .event_query import query_events
from .event_query import rejected as event_query_rejected
from .memory_proposal import MemoryProposal
from .models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
    clean_text,
)
from .query_session import (
    PROFILE_V3 as QUERY_SESSION_PROFILE_V3,
)
from .query_session import SOURCE_DISCOVERY_PROFILE, query_context, track_query
from .query_session import (
    execute as execute_query,
)
from .query_session import (
    execute_v3 as execute_query_v3,
)
from .query_session_tools import offer_query_tool
from .service_support import (
    _CORE_MEMORY_TOOL_CONTRACT,
    _CORE_MEMORY_TOOL_CONTRACT_FOOTER,
    _CORE_MEMORY_TOOL_CONTRACT_HEADER,
    _RECALL_EVIDENCE_GUIDANCE,
    _RECALL_GUIDANCE_FOOTER,
    _RECALL_GUIDANCE_HEADER,
    _RECONSTRUCTION_CONTRACT,
    _RECONSTRUCTION_CONTRACT_FOOTER,
    _RECONSTRUCTION_CONTRACT_HEADER,
    _RECONSTRUCTION_DYNAMIC_FOOTER,
    _RECONSTRUCTION_DYNAMIC_HEADER,
    _REMEMBER_TOOL_CONTRACT,
    _REMEMBER_TOOL_CONTRACT_FOOTER,
    _REMEMBER_TOOL_CONTRACT_HEADER,
)
from .source_discovery import discover_sources as run_source_discovery
from .source_query import query_sources
from .time_intent import parse_time_intent


class MemoryCompanionServiceMemoryTools:
    """service memory tools; the facade owns all shared resources."""

    @staticmethod
    def _remember_observed_refs(event, refs):
        if event is None:
            return
        previous = getattr(event, "memory_companion_observed_refs", [])
        by_ref = {(item["id"], item["version"]): item for item in [*previous, *refs]}
        setattr(event, "memory_companion_observed_refs", list(by_ref.values()))

    async def tool_remember(
        self,
        event: Any,
        content: str,
        *,
        note_type: str = "memory",
        proposal: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist an AI-selected memory proposal after runtime validation.

        ``proposal`` is optional for backwards compatibility.  The model may
        express durability, validity and evidence references, while scope,
        privacy and storage safety remain runtime responsibilities.
        """
        proposal_data = dict(proposal) if isinstance(proposal, Mapping) else {}
        proposal_data.setdefault("note_type", note_type)
        memory_proposal = MemoryProposal.from_payload(content, proposal_data)
        content = memory_proposal.content
        note_type = clean_text(memory_proposal.memory_type, 40) or "memory"
        if not content:
            return {"ok": False, "error": "empty content"}
        if not memory_proposal.requested_persistence:
            return {
                "ok": False,
                "state": "skipped",
                "reason": "persistence not requested",
                "review_status": "none",
            }
        ctx = None
        try:
            ctx = await self.identity.resolve_event_context(event)
            if not self._scope_feature_enabled(ctx, "capture"):
                return {
                    "ok": False,
                    "error": "scope capture disabled",
                    "scope": clean_text(ctx.scope, 40),
                }
            visibility = "internal"
            if ctx.scope == "private":
                visibility = "private_pair"
            elif ctx.scope == "group":
                visibility = "group_public"
            record = MemoryRecord(
                id=self.stable_id("tool", note_type, ctx.session_id, content),
                memory_type=memory_proposal.memory_type,
                subject=self._bot_entity(ctx),
                object=EntityRef(
                    kind="user",
                    id=ctx.user_id,
                    name=ctx.user_name,
                    role="conversation_partner",
                ),
                scope=ctx.scope,
                session_id=ctx.session_id,
                platform=ctx.platform,
                message_id=ctx.message_id,
                group_id=ctx.group_id,
                visibility=visibility,
                sayability="indirect",
                reality_level="llm_tool_assertion",
                content=content,
                evidence=ctx.message_text,
                confidence=memory_proposal.confidence,
                importance=memory_proposal.importance,
                review_status="auto"
                if memory_proposal.confidence >= 0.55
                else "pending",
                tags=["llm_tool", note_type, ctx.scope],
                valid_from=memory_proposal.valid_from,
                valid_to=memory_proposal.valid_to,
                durability=memory_proposal.durability,
                validity_status=memory_proposal.validity_status,
                lifecycle="stable_memory"
                if memory_proposal.confidence >= 0.55
                else "short_term_candidate",
                metadata={
                    "tool": "memory_companion_remember",
                    "note_type": note_type,
                    "owner_bot_id": self._bot_subject_id(ctx),
                    **memory_proposal.as_metadata(),
                    "persona_id": ctx.persona_id,
                },
            )
            self.importance.calibrate(record, source="tool_memory")
            memory_id = await self.store.insert_memory(record)
            try:
                self._schedule_memory_embedding(memory_id, record)
            except Exception as exc:
                logger.warning(
                    "[MemoryCompanion] 主动记忆已写入，但向量索引任务启动失败: memory=%s error=%s",
                    memory_id,
                    exc,
                    exc_info=True,
                )
            return {
                "ok": True,
                "memory_id": memory_id,
                "review_status": record.review_status,
                "durability": record.durability,
                "validity_status": record.validity_status,
            }
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 主动记忆工具写入失败: session=%s error=%s",
                clean_text(getattr(ctx, "session_id", ""), 120) or "unknown",
                exc,
                exc_info=True,
            )
            return {"ok": False, "error": "memory write failed"}

    @track_query("recall")
    async def tool_recall(
        self,
        event: Any,
        query: str,
        top_k: int = 5,
        *,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> dict[str, Any]:
        ctx = await query_context(self, event)
        query = clean_text(query, 1000)
        if not query:
            return {"ok": False, "error": "empty query", "memories": []}
        if not self._scope_feature_enabled(ctx, "recall"):
            return {
                "ok": False,
                "error": "scope recall disabled",
                "scope": clean_text(ctx.scope, 40),
                "memories": [],
            }
        p5_gate = await self._p5_gate(
            event=event,
            sink="memory_recall",
            attestation=p5_attestation,
            consumer=p5_attestation_consumer,
        )
        if not p5_gate.get("ok"):
            return {
                "ok": False,
                "state": p5_gate.get("state", "degraded"),
                "error": p5_gate.get("error_code", "p5_attestation_required"),
                "memories": [],
            }
        limit = max(1, min(10, int(top_k or 5)))
        results, _blocked, slot_map = await self.search_context_slots(
            query,
            ctx,
            limit,
        )
        if ctx.scope == "group" and results:
            filtered, _blocked = self._filter_group_actor_memory_slots(
                ctx,
                slot_map,
                query_text=query,
            )
            results = self._flatten_slot_map(filtered)
        acl_private_candidate = any(
            "acl_allowed:private:" in clean_text(getattr(item, "reason", ""), 1200)
            for item in results
        )
        snapshot = p5_gate.get("snapshot")
        if snapshot is not None:
            await self._p5_record_observed(
                [item.memory.id for item in results], snapshot
            )
        from .memory_revision import memory_ref

        self._remember_observed_refs(
            event, [memory_ref(item.memory) for item in results]
        )
        return {
            "ok": True,
            "state": p5_gate.get("state", "legacy"),
            "usage": (
                "群聊中的 acl_allowed 私聊结果只是条件候选：仅当当前发言者的核心意图确实需要其本人相关事实时使用；"
                "普通陈述、转述、反问或意图不清时忽略，不主动公开或扩展私聊内容。"
                if acl_private_candidate
                else ""
            ),
            "memories": [
                serialize_memory(item.memory, item.score, item.reason)
                for item in results
            ],
        }

    async def tool_query(
        self, event: Any, operation: str = "status", parameters: Any = None
    ) -> dict[str, Any]:
        return await execute_query(self, event, operation, parameters)

    async def tool_query_v2(
        self,
        event: Any,
        operation: str = "status",
        parameters: Any = None,
        *,
        query_note: Any = None,
    ) -> dict[str, Any]:
        return await execute_query(
            self, event, operation, parameters, _notes=True, query_note=query_note
        )

    async def tool_query_v3(
        self,
        event: Any,
        operation: str = "status",
        parameters: Any = None,
        *,
        query_note: Any = None,
    ) -> dict[str, Any]:
        return await execute_query_v3(
            self,
            event,
            operation,
            parameters,
            _notes=query_note is not None
            or getattr(event, "_memory_query_notes", None) is not None,
            query_note=query_note,
        )

    async def query_for_model(
        self, event: Any, operation: str, *, query_note: Any = None, **parameters: Any
    ) -> dict[str, Any]:
        from .source_query_v2 import is_batch_request

        if (
            operation == "sources"
            and is_batch_request(parameters)
            and getattr(event, "_memory_source_query_v2", False) is True
        ):
            if (
                not self.config.bool("memory_tools.enable_query_progress", True)
                and query_note is None
            ):
                return await self.tool_sources(event, **parameters)
            return await self.tool_query_v3(
                event, operation, parameters, query_note=query_note
            )
        if operation == "discover" or getattr(event, "_memory_query_v3", False) is True:
            raw_ctx = await self.identity.resolve_event_context(event)
            self._normalized_session_context(raw_ctx)
            if getattr(event, "_memory_query_v3", False) is not True:
                return {
                    "profile": QUERY_SESSION_PROFILE_V3,
                    "ok": False,
                    "operation": operation,
                    "operation_id": None,
                    "error": "query_v3_unavailable",
                    "result": None,
                    "progress": {"state": "unavailable"},
                }
            return await self.tool_query_v3(
                event, operation, parameters, query_note=query_note
            )
        if getattr(event, "_memory_query_notes", None) is not None and operation in {
            "sources",
            "status",
        }:
            return await self.tool_query_v2(
                event, operation, parameters, query_note=query_note
            )
        if query_note is not None:
            return {
                "profile": "memory.local-query-session.v1",
                "ok": False,
                "operation": operation,
                "operation_id": None,
                "error": "query_notes_not_offered",
                "result": None,
                "progress": {"state": "unavailable"},
            }
        if operation == "status" or self.config.bool(
            "memory_tools.enable_query_progress", True
        ):
            return await self.tool_query(event, operation, parameters)
        return await getattr(self, "tool_" + operation)(event, **parameters)

    @track_query("sources")
    async def tool_sources(self, event: Any, **parameters: Any) -> dict[str, Any]:
        try:
            result = await query_sources(self, event, **parameters)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] source query failed: error_type=%s",
                type(exc).__name__,
            )
            from .source_query_v2 import is_batch_request, rejected

            if is_batch_request(parameters):
                return rejected("source_query_failed")
            return {
                "profile": "memory.local-source-query.v1",
                "ok": False,
                "status": "rejected",
                "error": "source_query_failed",
                "sources": [],
                "next_cursor": None,
                "context_cursors": {},
                "coverage": {},
                "usage": {},
            }
        logger.info(
            "[MemoryCompanion] source query: status=%s error=%s returned=%s more=%s elapsed_ms=%s",
            result["status"],
            result["error"],
            len(result["sources"]),
            result["coverage"].get("more_available"),
            result["usage"].get("elapsed_ms"),
        )
        return result

    @track_query("discover")
    async def tool_discover_sources(
        self,
        event: Any,
        *,
        query: str,
        terms: Any = None,
        start_at: str = "",
        end_at: str = "",
        limit: int = 0,
    ) -> dict[str, Any]:
        try:
            result = await run_source_discovery(
                self,
                event,
                query=query,
                terms=terms,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] source discovery failed: error_type=%s",
                type(exc).__name__,
            )
            return {
                "profile": SOURCE_DISCOVERY_PROFILE,
                "ok": False,
                "status": "rejected",
                "error": "source_discovery_failed",
                "matches": [],
                "sources": [],
                "coverage": {},
                "usage": {},
            }
        logger.info(
            "[MemoryCompanion] source discovery: status=%s error=%s returned=%s elapsed_ms=%s",
            result.get("status"),
            result.get("error"),
            len(result.get("sources", [])),
            result.get("usage", {}).get("elapsed_ms"),
        )
        return result

    @track_query("events")
    async def tool_events(self, event: Any, plan: Any) -> dict[str, Any]:
        try:
            result = await query_events(self, event, plan)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] event query failed: error_type=%s",
                type(exc).__name__,
            )
            return event_query_rejected("event_query_failed")
        logger.info(
            "[MemoryCompanion] event query: status=%s error=%s events=%s unresolved=%s elapsed_ms=%s",
            result["status"],
            result["error"],
            len(result["events"]),
            len(result["unresolved"]),
            result["usage"].get("elapsed_ms"),
        )
        return result

    @staticmethod
    def _apply_remember_tool_contract(req: Any) -> None:
        current = getattr(req, "system_prompt", "") or ""
        if not isinstance(current, str):
            current = str(current)
        current = remove_marked_text(
            current,
            _REMEMBER_TOOL_CONTRACT_HEADER,
            _REMEMBER_TOOL_CONTRACT_FOOTER,
        )
        req.system_prompt = (
            f"{current}\n\n{_REMEMBER_TOOL_CONTRACT}"
            if current
            else _REMEMBER_TOOL_CONTRACT
        )

    def _apply_core_memory_tool_contract(self, req: Any) -> None:
        current = getattr(req, "system_prompt", "") or ""
        if not isinstance(current, str):
            current = str(current)
        current = remove_marked_text(
            current,
            _CORE_MEMORY_TOOL_CONTRACT_HEADER,
            _CORE_MEMORY_TOOL_CONTRACT_FOOTER,
        )
        if not self.config.bool("core_memory.llm_management_enabled", True):
            req.system_prompt = current
            return
        req.system_prompt = (
            f"{current}\n\n{_CORE_MEMORY_TOOL_CONTRACT}"
            if current
            else _CORE_MEMORY_TOOL_CONTRACT
        )

    def _apply_reconstruction_contract(
        self,
        req: Any,
        ctx: SessionContext,
        *,
        event: Any = None,
    ) -> None:
        current = getattr(req, "system_prompt", "") or ""
        if not isinstance(current, str):
            current = str(current)
        current = remove_marked_text(
            current,
            _RECONSTRUCTION_CONTRACT_HEADER,
            _RECONSTRUCTION_CONTRACT_FOOTER,
        )
        remove_temp_text(
            req, _RECONSTRUCTION_DYNAMIC_HEADER, _RECONSTRUCTION_DYNAMIC_FOOTER
        )
        current = remove_marked_text(
            current, _RECALL_GUIDANCE_HEADER, _RECALL_GUIDANCE_FOOTER
        )
        offer_query_tool(self, req, ctx, event)
        request_tools = getattr(getattr(req, "func_tool", None), "tools", None)
        names = (
            {
                getattr(tool, "name", "")
                for tool in request_tools
                if getattr(tool, "active", True)
            }
            if isinstance(request_tools, (list, tuple))
            else set()
        )
        if not self._should_offer_memory_reconstruction(ctx):
            # Tool availability and authorization, not lexical recall detection,
            # decide whether the model gets this small, general usage reminder.
            if self._memory_reconstruction_enabled(ctx) and names.intersection(
                {
                    "memory_companion_recall",
                    "memory_companion_navigate",
                    "memory_companion_sources",
                    "memory_companion_events",
                    "memory_companion_query",
                }
            ):
                guidance = "\n".join(
                    (
                        _RECALL_GUIDANCE_HEADER,
                        _RECALL_EVIDENCE_GUIDANCE,
                        _RECALL_GUIDANCE_FOOTER,
                    )
                )
                req.system_prompt = f"{current}\n\n{guidance}" if current else guidance
                logger.info(
                    "[MemoryCompanion] recall guidance offered: turn=%s request_tools_at_memory_hook=%s",
                    self._reconstruction_budget_key(event, ctx),
                    ",".join(
                        sorted(
                            names.intersection(
                                {
                                    "memory_companion_recall",
                                    "memory_companion_navigate",
                                    "memory_companion_sources",
                                    "memory_companion_events",
                                    "memory_companion_query",
                                }
                            )
                        )
                    ),
                )
            else:
                req.system_prompt = current
            return

        injection_state = self._memory_companion_injection_payload(req)
        if not injection_state:
            injection_state = self._memory_companion_injection_payload(event)
        selected_ids = (
            injection_state.get("selected_memory_ids")
            if isinstance(injection_state, dict)
            else []
        )
        selected_count = len(selected_ids) if isinstance(selected_ids, list) else 0
        injected_ids = (
            injection_state.get("injected_memory_ids")
            if isinstance(injection_state, dict)
            else None
        )
        injected_label = (
            str(len(injected_ids)) if isinstance(injected_ids, list) else "未确认"
        )
        max_steps = self._reconstruction_max_steps()
        dynamic_line = (
            f"本轮正常检索已选出 {selected_count} 条候选，实际注入条数：{injected_label}；"
            f"导航最多 {max_steps} 步，这是资源上限而不是目标步数。候选数不代表本轮看到了这些证据，注入成功也不表示它们回答了当前问题；无关条目可以忽略。"
        )
        dynamic_text = f"{_RECONSTRUCTION_DYNAMIC_HEADER}{dynamic_line}{_RECONSTRUCTION_DYNAMIC_FOOTER}"
        if append_temp_text(req, dynamic_text):
            contract = _RECONSTRUCTION_CONTRACT
        else:
            contract = _RECONSTRUCTION_CONTRACT.replace(
                _RECONSTRUCTION_CONTRACT_FOOTER,
                f"{dynamic_line}\n{_RECONSTRUCTION_CONTRACT_FOOTER}",
            )
        req.system_prompt = f"{current}\n\n{contract}" if current else contract
        # This observes the request at this hook; later host/provider filters
        # can still change it. Never infer tool availability from global active.
        tool_state = "uninspected"
        if isinstance(request_tools, (list, tuple)):
            tool_state = (
                ",".join(
                    name
                    for name in (
                        "memory_companion_recall",
                        "memory_companion_navigate",
                        "memory_companion_sources",
                        "memory_companion_events",
                        "memory_companion_query",
                    )
                    if name in names
                )
                or "none"
            )
        logger.info(
            "[MemoryCompanion] reconstruction offered: turn=%s request_tools_at_memory_hook=%s selected=%s injected=%s",
            self._reconstruction_budget_key(event, ctx),
            tool_state,
            selected_count,
            injected_label,
        )

    def _memory_reconstruction_enabled(self, ctx: SessionContext) -> bool:
        if not self._scope_feature_enabled(ctx, "recall"):
            return False
        if not self.config.bool("memory_reconstruction.enabled", True):
            return False
        if not self.config.bool("memory_tools.enable_reconstruction_tool", True):
            return False
        return True

    def _should_offer_memory_reconstruction(self, ctx: SessionContext) -> bool:
        if not self._memory_reconstruction_enabled(ctx):
            return False
        text = clean_text(ctx.message_text, 1200)
        if not text:
            return False
        time_intent = parse_time_intent(text)
        return bool(
            time_intent.active
            or self._message_requests_temporal_aggregate(text)
            or self._message_is_contextual_memory_request(text)
            or self._message_requests_personalized_context(text)
            or self._message_is_shared_routine_invocation(text)
        )

    def _reconstruction_max_steps(self) -> int:
        return max(1, min(8, self.config.int("memory_reconstruction.max_steps", 3)))

    def _reconstruction_per_step_limit(self, requested: int = 0) -> int:
        configured = max(
            1, min(12, self.config.int("memory_reconstruction.per_step_limit", 6))
        )
        try:
            requested_value = int(requested or 0)
        except (TypeError, ValueError):
            requested_value = 0
        if requested_value <= 0:
            return configured
        return max(1, min(configured, requested_value))

    def _reconstruction_scan_limit(self) -> int:
        return max(
            12,
            min(200, self.config.int("memory_reconstruction.candidate_scan_limit", 96)),
        )
