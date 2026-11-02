from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .astrbot_compat import (
    clean_private_companion_history_text,
    logger,
    remove_temp_text,
)
from .identity import (
    looks_like_command,
)
from .injection import (
    MEMORY_COMPANION_INJECTION_FOOTER,
    MEMORY_COMPANION_INJECTION_HEADER,
)
from .models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .service_support import (
    _HookStageTimer,
)


class MemoryCompanionServiceRequestCapture:
    """service request capture; the facade owns all shared resources."""

    async def handle_llm_request(self, event: Any, req: Any) -> None:
        remove_temp_text(
            req, MEMORY_COMPANION_INJECTION_HEADER, MEMORY_COMPANION_INJECTION_FOOTER
        )
        self._mark_memory_companion_injection_state(
            event, req, injected=False, conversation_memory=False, slot_map={}
        )
        if bool(getattr(event, "private_companion_req036_denied", False)):
            # Companion owns the fixed-reply branch. Do not resolve identity,
            # read memory, write evidence, or enter any LLM path here.
            return
        stage_timer = _HookStageTimer(
            self.config.bool("memory_injection.debug_stage_timing_enabled", False)
        )
        self._ensure_lifecycle_maintenance_dispatcher()
        self._ensure_wal_checkpoint_loop()
        self._ensure_acl_projection_reconcile_loop()
        ctx = await self.identity.resolve_event_context(event)
        stage_timer.mark("identity")
        capture_runtime = getattr(self, "capture", None)
        if capture_runtime is not None:
            source_receipt = await capture_runtime.incoming(event, ctx)
            if (
                source_receipt
                and source_receipt.get("status") == "committed"
                and source_receipt.get("source_state") == "current"
            ):
                from .source_evidence import remember_source_reads

                remember_source_reads(event, ctx, [source_receipt])
        self._sanitize_session_context_message_text(ctx)
        self._apply_companion_relationship_projection(ctx, event=event, req=req)
        self._apply_companion_expression_decision(ctx, event=event, req=req)
        self._ensure_reconstruction_turn_token(event, ctx)
        self._apply_private_companion_preferred_address(ctx, req=req, event=event)
        if self._private_companion_internal_generation_event(event):
            self._emit_hook_stage_timing(
                stage_timer, ctx, note="internal_generation_skip"
            )
            return
        if looks_like_command(ctx.message_text):
            remove_temp_text(
                req,
                MEMORY_COMPANION_INJECTION_HEADER,
                MEMORY_COMPANION_INJECTION_FOOTER,
            )
            self._mark_memory_companion_injection_state(
                event, req, injected=False, conversation_memory=False, slot_map={}
            )
            if self.config.bool("memory_injection.debug_log_injection_enabled", False):
                logger.info(
                    "[MemoryCompanion] 当前消息为指令，跳过记忆注入和采集: session=%s message=%s",
                    ctx.session_id,
                    clean_text(ctx.message_text, 160),
                )
            self._emit_hook_stage_timing(stage_timer, ctx, note="command_skip")
            return

        self._apply_remember_tool_contract(req)
        self._apply_core_memory_tool_contract(req)
        stage_timer.mark("decorate")

        capture_enabled = self._scope_feature_enabled(ctx, "capture")
        # note_identity（身份双写）已移入 _capture_async 后台执行，不阻塞请求关键路径。
        stage_timer.mark("note_identity")
        reply_chain = await self._reply_chain_for_event(event)
        stage_timer.mark("reply_chain")

        if capture_enabled:
            await self._apply_user_reaction_feedback(ctx)
            self._update_address_evolution(ctx, ctx.message_text or "")
        stage_timer.mark("feedback")

        self._hook_stage_timer = stage_timer
        try:
            await self.inject_memories(ctx, req, event=event)
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 记忆注入失败，已放行本轮 LLM 请求: session=%s error=%s",
                ctx.session_id,
                exc,
                exc_info=True,
            )
        finally:
            self._hook_stage_timer = None
        stage_timer.mark("inject")

        self._apply_reconstruction_contract(req, ctx, event=event)

        if capture_enabled:
            # 关于本轮回复的采集写入（写时间线、写关系、抽取事实、调度向量化等）
            # 对"本轮要不要出回复"毫无贡献，移入后台执行，不阻塞请求关键路径。
            # 对应 optimization_plan.md §3.2「把写操作移出请求关键路径」。
            # Register capture work so shutdown can cancel it before closing the
            # SQLite store.  An untracked task may otherwise continue writing
            # while the store is being torn down.
            self._spawn_background(
                self._capture_async(ctx, event, req, reply_chain),
                label="capture-async",
                defer_during_grace=True,
            )
        stage_timer.mark("finalize")
        self._emit_hook_stage_timing(stage_timer, ctx, note="ok")

    def _emit_hook_stage_timing(
        self,
        stage_timer: _HookStageTimer,
        ctx: Any,
        *,
        note: str = "",
    ) -> None:
        if not stage_timer.enabled:
            return
        retrieval_info = dict(getattr(self, "_last_retrieval_path_info", None) or {})
        retrieval_brief = {
            key: retrieval_info.get(key)
            for key in (
                "path",
                "mode",
                "candidate_count",
                "mmr_wall_ms",
                "cache",
                "reason",
                "ranking_cpu_ms",
                "candidate_load_ms",
                "candidate_bundle",
                "fts_candidates",
                "current_window_candidates",
                "keyword_candidates",
                "keyword_fallback_used",
                "bundle_lock_wait_ms",
                "bundle_queries_ms",
                "bundle_parse_ms",
                "bundle_inner_ms",
            )
            if key in retrieval_info
        }
        logger.info(
            "[MemoryCompanion] hook stage timing: session=%s note=%s total=%sms | %s | retrieval=%s",
            getattr(ctx, "session_id", ""),
            note,
            stage_timer.total_ms(),
            stage_timer.summary(),
            json_dumps(retrieval_brief) if retrieval_brief else "{}",
        )

    async def _capture_async(
        self,
        ctx: SessionContext,
        event: Any,
        req: Any,
        reply_chain: list[dict[str, Any]] | None,
    ) -> None:
        """后台执行：采集写入链，不阻塞本轮 LLM 请求。

        包含：稳定事实抽取、时间线写入、关系边记录、画像捕获等。
        所有异常在此被捕获并记录日志，不会传播到请求路径。
        """
        try:
            # 身份双写（user+group）合并单事务，随采集链一并移入后台，不阻塞请求关键路径。
            await self.note_identity(ctx)
            if not self.config.bool("memory_capture.enabled", True):
                return
            if not self.config.bool("memory_capture.capture_user_messages", True):
                return
            record = self.classifier.from_user_message(ctx)
            if not record:
                return
            recent_timeline_rows: list[dict[str, Any]] | None = None
            if ctx.scope == "group":
                recent_timeline_rows = await self.store.recent_timeline(
                    limit=20,
                    scope=ctx.scope,
                    session_id=ctx.session_id,
                    entity_id=ctx.current_target_id,
                )
            capture_receipt = getattr(event, "_memory_capture_receipt", None)
            if capture_receipt is not None and (
                capture_receipt.get("status") != "committed"
                or capture_receipt.get("source_state") != "current"
            ):
                return
            memory_id = (
                capture_receipt.get("source_ref", "").removeprefix("timeline:")
                if capture_receipt
                else await self._existing_timeline_message_id(
                    ctx, "user_message", recent_rows=recent_timeline_rows
                )
            )
            if not memory_id:
                event_metadata = {
                    "memory_id": memory_id,
                    "sender_name": ctx.user_name,
                    "message_id": ctx.message_id,
                    "source": "llm_request",
                    "conversation_memory": ctx.scope == "group",
                }
                event_metadata.update(self._cross_window_event_metadata(ctx))
                event_metadata.update(self._reply_chain_metadata(reply_chain))
                if ctx.scope == "group":
                    event_metadata.update(
                        await self._conversation_memory_metadata(
                            ctx,
                            source="llm_request",
                            recent_rows=recent_timeline_rows,
                        )
                    )
                memory_id = await self.store.add_timeline_event(
                    event_type="user_message",
                    session_id=ctx.session_id,
                    scope=ctx.scope,
                    subject_id=ctx.user_id,
                    object_id=ctx.current_target_id,
                    content=self._timeline_content_with_reply_chain(
                        ctx.message_text, reply_chain
                    ),
                    metadata=event_metadata,
                )
            if not memory_id:
                return  # The original source was suppressed; no derived capture.
            self._schedule_source_semantic_maintenance(ctx, memory_id)
            # 采集写链收集为批量操作，合并单事务一次 commit，减少独立事务/fsync 次数。
            write_ops: list[dict[str, Any]] = []
            derived_jobs: list[tuple[int, MemoryRecord, str]] = []
            if self.config.bool("memory_capture.record_relationship_edges", True):
                if ctx.scope == "group" and ctx.user_id and ctx.group_id:
                    write_ops.append(
                        {
                            "kind": "relationship",
                            "source_memory_id": memory_id,
                            "params": {
                                "subject": EntityRef(
                                    kind="user",
                                    id=ctx.user_id,
                                    name=ctx.user_name,
                                    role="group_member",
                                ),
                                "object": EntityRef(
                                    kind="group",
                                    id=ctx.group_id,
                                    name=ctx.group_name,
                                    role="group",
                                ),
                                "relation_type": "member_of_group",
                                "scope": "group",
                                "session_id": ctx.session_id,
                                "group_id": ctx.group_id,
                                "visibility": "group_public",
                                "evidence": clean_text(ctx.message_text, 500),
                                "confidence": 0.8,
                                "review_status": "auto",
                                "metadata": {"observed_from": "group_message"},
                            },
                        }
                    )
            capture_mode = clean_text(
                self.config.get("memory_capture.proposal_mode", "hybrid"), 32
            ).lower()
            if capture_mode not in {"hybrid", "summary_first", "explicit_only"}:
                capture_mode = "hybrid"
            if (
                self.config.bool("memory_capture.extract_stable_facts", True)
                and capture_mode == "hybrid"
            ):
                # Hybrid remains backward compatible. summary_first leaves
                # semantic extraction to the summary provider; explicit_only
                # keeps timeline capture while requiring the remember tool for
                # durable user initiated writes.
                derived_memories = self.classifier.derived_user_memories(
                    ctx,
                    source_memory_id=memory_id,
                )
            else:
                derived_memories = []
            if not any(
                clean_text((item.metadata or {}).get("profile_dimension"), 80)
                for item in derived_memories
            ):
                rejection_reason = self.classifier.profile_rejection_reason(
                    ctx.message_text
                )
                if rejection_reason:
                    logger.info(
                        "[MemoryCompanion] 画像写入门禁: decision=rejected "
                        "reason=%s type=profile_candidate dimension= subject=%s source=%s",
                        rejection_reason,
                        clean_text(ctx.user_id, 120),
                        clean_text(memory_id, 160),
                    )
            for derived in derived_memories:
                derived.id = self.stable_id(
                    "derived", derived.memory_type, ctx.session_id, derived.content
                )
                self.importance.calibrate(derived, source="stable_fact_extraction")
                write_mode, gate_reason = self._prepare_rule_profile_write(derived)
                if write_mode == "reject":
                    self._log_profile_write_gate(
                        derived,
                        decision="rejected",
                        reason=gate_reason,
                    )
                    continue
                if write_mode == "profile":
                    write_ops.append({"kind": "profile", "record": derived})
                else:
                    write_ops.append({"kind": "memory", "record": derived})
                op_index = len(write_ops) - 1
                derived_jobs.append((op_index, derived, write_mode))
                relation_type = str(derived.metadata.get("relation_type") or "")
                if relation_type and self.config.bool(
                    "memory_capture.record_relationship_edges", True
                ):
                    write_ops.append(
                        {
                            "kind": "relationship",
                            "requires_ok": [op_index],
                            "source_memory_id_from": op_index,
                            "params": {
                                "subject": derived.subject,
                                "object": self._bot_entity(ctx),
                                "relation_type": relation_type,
                                "scope": ctx.scope,
                                "session_id": ctx.session_id,
                                "group_id": ctx.group_id,
                                "visibility": derived.visibility,
                                "evidence": derived.evidence,
                                "confidence": derived.confidence,
                                "review_status": derived.review_status,
                                "metadata": {"source": "relationship_claim"},
                            },
                        }
                    )
            batch_results = await self.store.capture_write_batch(write_ops)
            # 批量结果后处理：画像门禁日志与 embedding 调度。
            for op_index, derived, write_mode in derived_jobs:
                result = (
                    batch_results[op_index] if op_index < len(batch_results) else {}
                )
                if write_mode == "profile":
                    if not result.get("ok"):
                        self._log_profile_write_gate(
                            derived,
                            decision="rejected",
                            reason=clean_text(result.get("code"), 120),
                        )
                        continue
                    derived_id = clean_text(result.get("memory_id"), 120)
                    gate_reason = clean_text(result.get("profile_status"), 40)
                    self._log_profile_write_gate(
                        derived,
                        decision="written",
                        reason=gate_reason,
                    )
                    if gate_reason == "active":
                        stored_profile = await self.store.get_memory(derived_id)
                        self._schedule_memory_embedding(
                            derived_id, stored_profile or derived
                        )
                else:
                    if not result.get("ok"):
                        logger.warning(
                            "[MemoryCompanion] 派生记忆批量写入失败: type=%s code=%s",
                            clean_text(derived.memory_type, 80),
                            clean_text(result.get("code"), 120),
                        )
                        continue
                    derived_id = clean_text(result.get("memory_id"), 120)
                    self._schedule_memory_embedding(derived_id, derived)
            if capture_mode != "explicit_only":
                await self.portraits.capture_user_message(ctx, event=event, req=req)
                self._ensure_portrait_daily_dispatcher()
            if not self.config.bool("memory_capture.capture_bot_responses", True):
                self._schedule_session_summary(ctx, reason="after_user_message")
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 后台采集写入失败: session=%s error=%s",
                ctx.session_id if hasattr(ctx, "session_id") else "?",
                exc,
                exc_info=True,
            )

    async def handle_group_message(self, event: Any) -> None:
        self._ensure_lifecycle_maintenance_dispatcher()
        self._ensure_wal_checkpoint_loop()
        self._ensure_acl_projection_reconcile_loop()
        if not self.config.bool("memory_capture.enabled", True):
            return
        if not self.config.bool("conversation_memory.enabled", True):
            return
        if not self.config.bool("conversation_memory.capture_group_messages", True):
            return
        if self._private_companion_internal_generation_event(event):
            return
        ctx = await self.identity.resolve_event_context(event)
        self._sanitize_session_context_message_text(ctx)
        if ctx.scope != "group":
            return
        if not self._scope_feature_enabled(ctx, "capture"):
            return
        if looks_like_command(ctx.message_text):
            return
        if ctx.bot_id and ctx.user_id and ctx.bot_id == ctx.user_id:
            return
        await self.note_identity(ctx)
        reply_chain = await self._reply_chain_for_event(event)
        record = self.classifier.from_user_message(ctx)
        if not record:
            return
        if await self._timeline_already_has_message(ctx, "user_message"):
            return
        event_metadata = await self._conversation_memory_metadata(
            ctx, source="group_conversation_observer"
        )
        event_metadata.update(
            {
                "memory_id": "",
                "sender_name": ctx.user_name,
                "message_id": ctx.message_id,
            }
        )
        event_metadata.update(self._cross_window_event_metadata(ctx))
        event_metadata.update(self._reply_chain_metadata(reply_chain))
        captured_id = await self.store.add_timeline_event(
            event_type="user_message",
            session_id=ctx.session_id,
            scope=ctx.scope,
            subject_id=ctx.user_id,
            object_id=ctx.current_target_id,
            content=self._timeline_content_with_reply_chain(
                ctx.message_text, reply_chain
            ),
            metadata=event_metadata,
        )
        if not captured_id:
            return
        self._schedule_source_semantic_maintenance(ctx, captured_id)
        if self.config.bool("memory_capture.record_relationship_edges", True):
            await self.note_relationships(ctx)
        await self.portraits.capture_user_message(ctx, event=event)
        self._ensure_portrait_daily_dispatcher()
        self._schedule_session_summary(ctx, reason="group_conversation")

    async def handle_llm_response(self, event: Any, resp: Any) -> None:
        if getattr(event, "_memory_capture_receipt", None) is not None:
            return  # Outgoing originals are supplied only by the delivery owner.
        if self._private_companion_internal_generation_event(event):
            return
        if not self.config.bool("memory_capture.enabled", True):
            return
        if not self.config.bool("memory_capture.capture_bot_responses", True):
            return
        if getattr(resp, "role", "") != "assistant":
            return
        if getattr(resp, "tools_call_name", None) or getattr(
            resp, "tools_call_extra_content", None
        ):
            return

        text = clean_text(getattr(resp, "completion_text", "") or "", 2000)
        if not text:
            return

        ctx = await self.identity.resolve_event_context(event)
        if not self._scope_feature_enabled(ctx, "capture"):
            return
        if looks_like_command(ctx.message_text):
            return
        record = self.classifier.from_bot_response(ctx, text)
        if not record:
            return

        if await self._timeline_already_has_message(ctx, "bot_response", content=text):
            return

        memory_id = ""
        injection_state = self._memory_companion_injection_payload(event)
        captured_id = await self.store.add_timeline_event(
            event_type="bot_response",
            session_id=ctx.session_id,
            scope=ctx.scope,
            subject_id=self._bot_subject_id(ctx),
            object_id=ctx.current_target_id,
            content=text,
            metadata={
                "memory_id": memory_id,
                "message_id": clean_text(ctx.message_id, 120),
                "memory_companion_injection_state": injection_state,
                **self._cross_window_event_metadata(ctx),
            },
        )
        if captured_id:
            self._schedule_source_semantic_maintenance(ctx, captured_id)
            self._schedule_session_summary(ctx, reason="after_bot_response")

    async def _timeline_already_has_message(
        self, ctx: SessionContext, event_type: str, *, content: str = ""
    ) -> bool:
        return bool(
            await self._existing_timeline_message_id(ctx, event_type, content=content)
        )

    async def _existing_timeline_message_id(
        self,
        ctx: SessionContext,
        event_type: str,
        *,
        content: str = "",
        recent_rows: list[dict[str, Any]] | None = None,
    ) -> str:
        message_id = clean_text(ctx.message_id, 120)
        rows = recent_rows
        if rows is None:
            rows = await self.store.recent_timeline(
                limit=20,
                scope=ctx.scope,
                session_id=ctx.session_id,
                entity_id=ctx.current_target_id,
            )
        text = clean_text(content or ctx.message_text, 1000)
        subject_id = clean_text(
            self._bot_subject_id(ctx) if event_type == "bot_response" else ctx.user_id,
            120,
        )
        for row in rows:
            if clean_text(row.get("event_type"), 80) != event_type:
                continue
            if subject_id and clean_text(row.get("subject_id"), 120) != subject_id:
                continue
            metadata = json_loads(row.get("metadata"), {})
            existing_message_id = clean_text(
                row.get("message_id") or metadata.get("message_id") or "", 120
            )
            if message_id and existing_message_id and message_id == existing_message_id:
                return clean_text(row.get("id"), 160)
            if text and clean_text(row.get("content"), 1000) == text:
                previous_at = self._parse_utc_datetime(
                    str(row.get("occurred_at") or row.get("created_at") or "")
                )
                if previous_at is not None:
                    gap_seconds = max(
                        0.0, (datetime.now(timezone.utc) - previous_at).total_seconds()
                    )
                    if gap_seconds <= 3:
                        return clean_text(row.get("id"), 160)
        return ""

    async def _conversation_memory_metadata(
        self,
        ctx: SessionContext,
        *,
        source: str,
        recent_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "source": clean_text(source, 80),
            "conversation_memory": True,
        }
        rows = recent_rows
        if rows is None:
            rows = await self.store.recent_timeline(
                limit=1,
                scope=ctx.scope,
                session_id=ctx.session_id,
                entity_id=ctx.current_target_id,
            )
        previous = rows[0] if rows else None
        idle_limit = max(1, self.config.int("conversation_memory.idle_gap_minutes", 20))
        previous_segment_id = ""
        starts_new_segment = True
        if previous:
            previous_metadata = json_loads(previous.get("metadata"), {})
            previous_segment_id = clean_text(
                previous_metadata.get("conversation_segment_id"), 120
            )
            previous_at = self._parse_utc_datetime(
                str(previous.get("occurred_at") or previous.get("created_at") or "")
            )
            if previous_at is not None:
                gap_minutes = max(
                    0.0, (datetime.now(timezone.utc) - previous_at).total_seconds() / 60
                )
                metadata["previous_gap_minutes"] = round(gap_minutes, 2)
                starts_new_segment = gap_minutes >= idle_limit
            else:
                starts_new_segment = False
        if starts_new_segment or not previous_segment_id:
            segment_id = self.stable_id(
                "conversation_segment",
                ctx.session_id,
                ctx.message_id or ctx.message_text or utc_now(),
            )
        else:
            segment_id = previous_segment_id
        metadata["conversation_segment_id"] = segment_id
        metadata["conversation_segment_start"] = bool(starts_new_segment)
        return metadata

    async def _reply_chain_for_event(self, event: Any) -> list[dict[str, Any]]:
        if event is None:
            return []
        try:
            chain = await self.reply_chain.resolve(event, max_depth=3)
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 引用链解析失败，已跳过: error=%s", exc, exc_info=True
            )
            return []
        return chain

    def _reply_chain_metadata(self, chain: list[dict[str, Any]]) -> dict[str, Any]:
        return self.reply_chain.metadata(chain)

    def _timeline_content_with_reply_chain(
        self, message_text: str, chain: list[dict[str, Any]]
    ) -> str:
        text = clean_text(message_text, 1600)
        reply_context = self.reply_chain.format_for_query(chain, max_chars=520)
        if not reply_context:
            return text
        if text:
            return clean_text(f"{text}\n[引用链上下文] {reply_context}", 2000)
        return clean_text(f"[引用链上下文] {reply_context}", 2000)

    def _cross_window_event_metadata(self, ctx: SessionContext) -> dict[str, str]:
        return {
            "owner_bot_id": clean_text(self._bot_subject_id(ctx), 120),
            "persona_id": clean_text(ctx.persona_id, 96),
            "platform": clean_text(ctx.platform, 80),
            "participant_user_id": clean_text(ctx.user_id, 120),
            "participant_user_name": clean_text(ctx.user_name, 80),
            "source_group_id": clean_text(ctx.group_id, 120),
            "source_group_name": clean_text(ctx.group_name, 80),
        }

    def _private_companion_internal_generation_event(self, event: Any) -> bool:
        if event is None:
            return False
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return True
        text = clean_text(getattr(event, "message_str", "") or "", 1200)
        if not text:
            return False
        return (
            "这不是用户消息" in text
            and "Private Companion" in text
            and "主动消息" in text
        )

    def _sanitize_session_context_message_text(self, ctx: SessionContext) -> None:
        cleaned, changed, drop = clean_private_companion_history_text(ctx.message_text)
        if not changed:
            return
        ctx.message_text = "" if drop else clean_text(cleaned, 2000)
        logger.info(
            "[MemoryCompanion] 已清理当前消息里的陪伴插件动态提示残留: session=%s drop=%s",
            ctx.session_id,
            drop,
        )
