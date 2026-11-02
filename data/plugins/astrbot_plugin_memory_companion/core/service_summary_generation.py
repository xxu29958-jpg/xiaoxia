from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
    clean_text,
    json_dumps,
)


class MemoryCompanionServiceSummaryGeneration:
    """service summary generation; the facade owns all shared resources."""

    async def maybe_summarize_session(
        self, ctx: SessionContext, *, force: bool = False
    ) -> str:
        if not force and not self.config.bool("memory_summary.enabled", True):
            return ""
        if not ctx.session_id:
            return ""

        lock = self._summary_locks.setdefault(ctx.session_id, asyncio.Lock())
        self._summary_lock_ts[ctx.session_id] = time.monotonic()
        self._cleanup_stale_summary_locks()
        if lock.locked():
            logger.debug(
                "[MemoryCompanion] 阶段性总结等待同会话现有任务: session=%s force=%s",
                ctx.session_id,
                force,
            )
        async with lock:
            max_calls = max(1, self.config.int("memory_summary.max_retries", 5))
            hourly_limit = max(
                1, self.config.int("memory_summary.max_calls_per_session_hour", 24)
            )
            legacy_failure = await self.store.get_summary_failure(ctx.session_id)
            if legacy_failure:
                await self.store.migrate_summary_failure(
                    ctx.session_id,
                    max_calls,
                    cooldown=self._summary_failure_cooldown_seconds(
                        transient=self._summary_failure_is_transient(
                            legacy_failure.get("last_error")
                        ),
                    ),
                )
            batch = (
                await self.store.next_summary_batch(ctx.session_id, force=True)
                if force
                else None
            )
            window = await self.store.unsummarized_timeline_window(
                session_id=ctx.session_id,
                scope=ctx.scope,
                limit=self.config.int("memory_summary.max_events_per_summary", 40),
                exclude_assigned=True,
            )
            # New messages take priority over bounded recovery of historical batches.
            if batch is None and self._summary_window_ready(window, force=force):
                rows = self.summarizer.rows_for_prompt(list(window.get("rows") or []))
                if not rows:
                    return ""
                batch_id = await self.store.create_summary_batch(
                    ctx.session_id, ctx.scope, rows
                )
                batch = await self.store.get_summary_batch(batch_id)
            if batch is None:
                batch = await self.store.next_summary_batch(ctx.session_id, force=force)
            if batch is None:
                return ""
            batch_id = batch["id"]
            rows = await self.store.summary_batch_rows(batch_id)
            total = len(rows)
            if not rows:
                return ""
            summary_attempts = await self._summary_provider_attempts(ctx)
            if not summary_attempts:
                return ""
            slot_held = await self._acquire_summary_call_slot(force=force)
            if not slot_held:
                return ""
            payload = None
            content = ""
            used_summary = {}
            previous_response = ""
            feedback = (
                batch["last_error"][7:]
                if batch["last_error"].startswith("repair:")
                else ""
            )
            repair = bool(feedback)
            attempt_index = 0
            # Manual runs get a bounded new round, without resetting automatic counters.
            round_calls = 0
            provider_failed = False
            try:
                while attempt_index < len(summary_attempts) and round_calls < max_calls:
                    attempt = summary_attempts[attempt_index]
                    if not await self.store.reserve_summary_call(
                        batch_id,
                        max_calls=max_calls,
                        hourly_limit=hourly_limit,
                        repair=repair,
                        force=force,
                        lease_seconds=max(
                            240,
                            self.config.int(
                                "memory_summary.provider_timeout_seconds", 300
                            )
                            + 60,
                        ),
                    ):
                        break
                    round_calls += 1
                    # Keep the last provider body while a repair attempt is in
                    # flight. If the follow-up provider fails, the original
                    # body can still be retained as a reviewable candidate.
                    provider_failed = False
                    try:
                        payload = await self.summarizer.summarize_with_provider(
                            attempt["provider"],
                            rows=rows,
                            session_label=ctx.label,
                            provider_id=attempt["provider_id"] or attempt["source"],
                            usage_recorder=self._record_token_usage,
                            usage_task="memory_summary_repair"
                            if repair
                            else "memory_summary",
                            repair_feedback=feedback,
                            previous_response=previous_response,
                        )
                        errors = self.summarizer.validation_errors(payload or {})
                        content = self.summarizer.compose_memory_content(payload or {})
                        used_summary = attempt
                        if not errors:
                            break
                        feedback = "; ".join(errors)
                        previous_response = json_dumps(payload or {})
                    except Exception as exc:
                        provider_failed = True
                        feedback = self._describe_exception(exc)
                        if self._summary_failure_is_transient(feedback):
                            current = await self.store.get_summary_batch(batch_id)
                            exhausted = (
                                not force and current["automatic_calls"] >= max_calls
                            )
                            await self.store.defer_summary_batch(
                                batch_id,
                                "repair:" + feedback if repair else feedback,
                                quarantine=exhausted,
                                delay=self._summary_retry_backoff_seconds(
                                    current["automatic_calls"]
                                ),
                            )
                            if exhausted:
                                return ""
                            attempt_index += 1
                            continue
                        previous_response = getattr(exc, "response", "")
                    current = await self.store.get_summary_batch(batch_id)
                    exhausted = not force and current["automatic_calls"] >= max_calls
                    if provider_failed:
                        # The provider itself is unusable for this round, so the
                        # batch is frozen rather than retried forever. Its events
                        # stay owned and auditable instead of being consumed.
                        has_usable_body = bool(
                            payload and self.summarizer.compose_memory_content(payload)
                        )
                        await self.store.defer_summary_batch(
                            batch_id,
                            "repair:" + feedback,
                            quarantine=False if has_usable_body else exhausted,
                            delay=self._summary_retry_backoff_seconds(
                                current["automatic_calls"]
                            ),
                        )
                        if has_usable_body or exhausted:
                            break
                        repair = True
                        continue
                    # A citation problem is not a reason to freeze the window.
                    # Quarantining here kept owning the events forever, so the
                    # conversation never became memory and its timeline rows
                    # never became eligible for cleanup either.  Retry until the
                    # call budget is spent, then keep the body as a candidate.
                    provider_failed = False
                    await self.store.defer_summary_batch(
                        batch_id,
                        "repair:" + feedback,
                        quarantine=False,
                        delay=self._summary_retry_backoff_seconds(
                            current["automatic_calls"]
                        ),
                    )
                    if exhausted:
                        break
                    # Reuse the remaining batch budget with actionable feedback.
                    repair = True
            finally:
                if not force:
                    self._summary_call_semaphore.release()
            if not payload or self.summarizer.validation_errors(payload):
                current = await self.store.get_summary_batch(batch_id)
                state = str((current or {}).get("state") or "")
                if state == "quarantined":
                    logger.warning(
                        "[MemoryCompanion] 阶段总结批次已隔离，本批原始事件冻结等待复核释放: session=%s batch=%s",
                        ctx.session_id,
                        batch_id,
                    )
                    return ""
                if self.summarizer.compose_memory_content(payload or {}):
                    # The model did return a body, so the conversation is worth
                    # keeping even when its citations do not hold up.
                    logger.warning(
                        "[MemoryCompanion] 阶段总结校验未通过，转为待复核候选保留本批内容: session=%s batch=%s error=%s",
                        ctx.session_id,
                        batch_id,
                        feedback,
                    )
                    return await self._store_summary_evidence_candidate(
                        ctx,
                        batch_id,
                        rows,
                        payload,
                        feedback,
                        mark_timeline_summarized=(
                            not force
                            and int((current or {}).get("automatic_calls") or 0)
                            >= max_calls
                        ),
                    )
                spent = (
                    not force
                    and int((current or {}).get("automatic_calls") or 0) >= max_calls
                )
                no_more_attempts = (
                    spent
                    or str((current or {}).get("retry_reason") or "")
                    == self.store.REPAIR_RETRY_REASON
                )
                if no_more_attempts:
                    # Nothing usable came back and this batch is out of attempts,
                    # so freeze it instead of consuming events of unproven value.
                    logger.warning(
                        "[MemoryCompanion] 阶段总结无可用正文，批次隔离等待复核: session=%s batch=%s error=%s",
                        ctx.session_id,
                        batch_id,
                        feedback,
                    )
                    await self.store.defer_summary_batch(
                        batch_id, "repair:" + feedback, quarantine=True, delay=0
                    )
                    return ""
                logger.warning(
                    "[MemoryCompanion] 阶段总结批次保留等待后续触发: session=%s batch=%s",
                    ctx.session_id,
                    batch_id,
                )
                return ""

            return await self._persist_summary_batch(
                batch_id=batch_id,
                content=content,
                ctx=ctx,
                payload=payload,
                rows=rows,
                total=total,
                used_summary=used_summary,
            )

    def _schedule_session_summary(self, ctx: SessionContext, *, reason: str) -> None:
        if not self.config.bool("memory_summary.enabled", True):
            return
        if not self._scope_feature_enabled(ctx, "capture"):
            return
        snapshot = self._snapshot_context(ctx)
        session_id = snapshot.session_id
        if not session_id:
            logger.debug(
                "[MemoryCompanion] 跳过阶段性总结调度: reason=%s cause=empty_session",
                reason,
            )
            return

        worker = self._summary_workers.get(session_id)
        if worker is not None and not worker.done():
            self._summary_pending.add(session_id)
            self._summary_pending_contexts[session_id] = snapshot
            self._summary_pending_reasons[session_id] = reason
            logger.debug(
                "[MemoryCompanion] 阶段性总结任务忙，已合并为一次补偿执行: session=%s reason=%s",
                session_id,
                reason,
            )
            return

        task = self._spawn_background(
            self._background_summarize_session(snapshot, reason),
            label=f"summary:{reason}",
        )
        if task is None:
            logger.debug(
                "[MemoryCompanion] 阶段性总结任务未启动: session=%s reason=%s closing=%s closed=%s",
                session_id,
                reason,
                self._closing,
                self._closed,
            )
            return
        self._summary_workers[session_id] = task
        logger.debug(
            "[MemoryCompanion] 阶段性总结任务已启动: session=%s reason=%s",
            session_id,
            reason,
        )

    async def _background_summarize_session(
        self, ctx: SessionContext, reason: str
    ) -> None:
        session_id = ctx.session_id
        current_ctx = ctx
        current_reason = reason
        if not self._scope_feature_enabled(ctx, "capture"):
            return
        try:
            while True:
                memory_id = await self.maybe_summarize_session(current_ctx)
                if memory_id:
                    logger.info(
                        "[MemoryCompanion] 后台阶段性总结完成: session=%s reason=%s memory=%s",
                        session_id,
                        current_reason,
                        memory_id,
                    )
                if session_id not in self._summary_pending:
                    break
                self._summary_pending.discard(session_id)
                current_ctx = self._summary_pending_contexts.pop(
                    session_id, current_ctx
                )
                current_reason = self._summary_pending_reasons.pop(
                    session_id, "coalesced_trigger"
                )
                logger.debug(
                    "[MemoryCompanion] 开始阶段性总结补偿执行: session=%s reason=%s",
                    session_id,
                    current_reason,
                )
        finally:
            self._summary_pending.discard(session_id)
            self._summary_pending_contexts.pop(session_id, None)
            self._summary_pending_reasons.pop(session_id, None)
            current_task = asyncio.current_task()
            if self._summary_workers.get(session_id) is current_task:
                self._summary_workers.pop(session_id, None)
            logger.debug(
                "[MemoryCompanion] 阶段性总结任务已结束: session=%s", session_id
            )

    def _cleanup_stale_summary_locks(self) -> None:
        """Remove summary locks that haven't been used in TTL seconds and aren't currently held."""
        now = time.monotonic()
        if (now - self._summary_lock_last_cleanup) < 60.0:
            return
        self._summary_lock_last_cleanup = now
        stale = [
            sid
            for sid, ts in self._summary_lock_ts.items()
            if (now - ts) > self._SUMMARY_LOCK_TTL
            and not self._summary_locks.get(sid, asyncio.Lock()).locked()
        ]
        for sid in stale:
            self._summary_locks.pop(sid, None)
            self._summary_lock_ts.pop(sid, None)

    @staticmethod
    def _summary_failure_is_transient(error: Any) -> bool:
        text = clean_text(error, 1000).lower()
        if not text:
            return False
        markers = (
            "connection error",
            "connectionerror",
            "apiconnectionerror",
            "connecterror",
            "connecttimeout",
            "connection reset",
            "connection refused",
            "server disconnected",
            "remoteprotocolerror",
            "readerror",
            "readtimeout",
            "network error",
            "temporarily unavailable",
            "service unavailable",
            "timeout",
            "timed out",
            "rate limit",
            "too many requests",
            "payment required",
            "insufficient balance",
            "insufficient_balance",
            "insufficient quota",
            "insufficient_quota",
            "quota exceeded",
            "exceeded your current quota",
            "resource exhausted",
            "连接错误",
            "连接失败",
            "连接超时",
            "网络错误",
            "网络异常",
            "暂时不可用",
            "余额不足",
            "额度不足",
            "配额不足",
        )
        if any(marker in text for marker in markers):
            return True
        return bool(re.search(r"(?:^|\D)(?:402|429|502|503|504)(?:\D|$)", text))

    def _summary_failure_age_seconds(self, failure: dict[str, Any]) -> float:
        updated_at = self._parse_utc_datetime(str(failure.get("updated_at") or ""))
        if updated_at is None:
            return 0.0
        return max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds())

    def _summary_transient_cooldown_seconds(self) -> int:
        minutes = max(
            0, self.config.int("memory_summary.transient_retry_cooldown_minutes", 10)
        )
        return minutes * 60

    def _summary_failure_cooldown_seconds(self, *, transient: bool) -> int:
        if transient:
            return self._summary_transient_cooldown_seconds()
        minutes = max(
            0,
            self.config.int("memory_summary.non_transient_retry_cooldown_minutes", 30),
        )
        return minutes * 60

    def _summary_retry_backoff_seconds(self, retry_count: int) -> int:
        base = max(0, self.config.int("memory_summary.retry_backoff_seconds", 60))
        if base <= 0:
            return 0
        exponent = max(0, min(8, int(retry_count or 1) - 1))
        cooldown = self._summary_transient_cooldown_seconds()
        return (
            min(base * (2**exponent), cooldown)
            if cooldown > 0
            else base * (2**exponent)
        )

    def _summary_window_ready(self, window: dict[str, Any], *, force: bool) -> bool:
        total = int(window.get("total") or 0)
        min_events = self.config.int("memory_summary.min_events", 20)
        if total < (1 if force else min_events):
            return False
        if force:
            return True
        trigger_count = self.config.int("memory_summary.trigger_event_count", 20)
        if total >= max(min_events, trigger_count):
            return True
        return self.summarizer.interval_elapsed(
            str(window.get("first_occurred_at") or ""),
            self.config.int("memory_summary.trigger_interval_minutes", 60),
        )

    async def _summary_window_after_failure(
        self,
        ctx: SessionContext,
        failure: dict[str, Any],
    ) -> dict[str, Any]:
        """Return fresh work after a failed batch without discarding that batch."""
        return await self.store.unsummarized_timeline_window(
            session_id=ctx.session_id,
            scope=ctx.scope,
            limit=self.config.int("memory_summary.max_events_per_summary", 40),
            after_timeline_id=clean_text(failure.get("end_timeline_id"), 160),
        )

    async def _acquire_summary_call_slot(self, *, force: bool) -> bool:
        """Cap concurrent summary provider calls across sessions (#10).

        Background rounds wait up to ``call_queue_timeout_seconds`` for a
        global slot and skip the round on timeout (the worker is re-triggered
        by new messages); ``force=True`` manual runs bypass the queue.
        """
        if force:
            return True
        timeout = max(
            0, self.config.int("memory_summary.call_queue_timeout_seconds", 20)
        )
        if timeout <= 0:
            await self._summary_call_semaphore.acquire()
            return True
        try:
            await asyncio.wait_for(
                self._summary_call_semaphore.acquire(), timeout=timeout
            )
            return True
        except asyncio.TimeoutError:
            return False

    @staticmethod
    def _context_from_memory_record(record: MemoryRecord | None) -> SessionContext:
        if record is None:
            return SessionContext()
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        return SessionContext(
            session_id=record.session_id,
            scope=record.scope,
            platform=record.platform,
            user_id=record.subject.id if record.subject.kind == "user" else "",
            user_name=record.subject.name if record.subject.kind == "user" else "",
            group_id=record.group_id,
            bot_id=clean_text(record.owner_bot_id or metadata.get("owner_bot_id"), 120),
            persona_id=clean_text(metadata.get("persona_id"), 96),
            message_id=record.message_id,
        )

    def _snapshot_context(self, ctx: SessionContext) -> SessionContext:
        return SessionContext(
            session_id=ctx.session_id,
            scope=ctx.scope,
            platform=ctx.platform,
            user_id=ctx.user_id,
            user_name=ctx.user_name,
            group_id=ctx.group_id,
            group_name=ctx.group_name,
            bot_id=ctx.bot_id,
            persona_id=ctx.persona_id,
            message_id=ctx.message_id,
            message_text=ctx.message_text,
            strict_session_only=ctx.strict_session_only,
            preferred_address=ctx.preferred_address,
            preferred_address_locked=ctx.preferred_address_locked,
        )

    def _bot_entity(self, ctx: SessionContext | None = None) -> EntityRef:
        bot_id = clean_text(getattr(ctx, "bot_id", "") if ctx else "", 120)
        return EntityRef.bot_self(bot_id=bot_id)

    def _bot_subject_id(self, ctx: SessionContext | None = None) -> str:
        return self._bot_entity(ctx).id

    async def _persist_summary_batch(
        self, *, batch_id, content, ctx, payload, rows, total, used_summary
    ):
        """Persist summary batch."""
        consumed_ids = {
            clean_text(item, 160)
            for item in ((payload or {}).get("_consumed_event_ids") or [])
            if clean_text(item, 160)
        }
        if consumed_ids:
            rows = [
                row for row in rows if clean_text(row.get("id"), 160) in consumed_ids
            ]
        if not rows:
            logger.warning(
                "[MemoryCompanion] 阶段性总结没有可确认的已消费事件，拒绝标记时间线: session=%s",
                ctx.session_id,
            )
            return ""

        if self.summarizer.summary_quality(payload) == "no_memory":
            await self.store.finish_summary_batch(
                batch_id,
                [str(row["id"]) for row in rows],
                no_memory=True,
                reason=payload.get("no_memory_reason", ""),
            )
            logger.info(
                "[MemoryCompanion] 批次已处理，无新增记忆，原文保留: batch=%s", batch_id
            )
            return ""

        visibility = "group_public" if ctx.scope == "group" else "private_pair"
        start_at = clean_text(rows[0].get("occurred_at") if rows else "", 80)
        end_at = clean_text(rows[-1].get("occurred_at") if rows else "", 80)
        start_at_local = self._local_time_label(start_at)
        end_at_local = self._local_time_label(end_at)
        evidence = "\n".join(
            clean_text(row.get("content"), 220)
            for row in rows[: self.config.int("memory_summary.evidence_events", 12)]
            if clean_text(row.get("content"), 220)
        )
        summary_quality = self.summarizer.summary_quality(payload or {})
        evidence_gate_passed = summary_quality == "normal"
        candidate_valid_to = (
            (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(
                timespec="seconds"
            )
            if not evidence_gate_passed
            else ""
        )
        record = MemoryRecord(
            id=self.stable_id("summary_batch", batch_id),
            memory_type="conversation_summary",
            subject=self._bot_entity(ctx)
            if ctx.scope == "group"
            else EntityRef(
                kind="user",
                id=ctx.user_id,
                name=ctx.user_name,
                role="conversation_partner",
            ),
            object=EntityRef(
                kind="group", id=ctx.group_id, name=ctx.group_name, role="group"
            )
            if ctx.scope == "group"
            else self._bot_entity(ctx),
            scope=ctx.scope,
            session_id=ctx.session_id,
            platform=ctx.platform,
            group_id=ctx.group_id,
            visibility=visibility,
            sayability="direct",
            reality_level="llm_summary",
            lifecycle="stable_memory"
            if evidence_gate_passed
            else "short_term_candidate",
            content=content,
            evidence=evidence,
            confidence=0.72 if evidence_gate_passed else 0.42,
            importance=float((payload or {}).get("importance", 0.68) or 0.68),
            review_status="auto" if evidence_gate_passed else "pending",
            tags=[
                "summary",
                "long_term" if evidence_gate_passed else "evidence_candidate",
                ctx.scope,
            ]
            + [
                clean_text(topic, 80) for topic in (payload or {}).get("topics", [])[:5]
            ],
            owner_bot_id=self._bot_subject_id(ctx),
            valid_to=candidate_valid_to,
            durability="normal" if evidence_gate_passed else "short",
            sensitivity="internal",
            metadata={
                "summary_batch_id": batch_id,
                "summary_refs": payload.get("summary_refs", []),
                "summary_event_count": len(rows),
                "unsummarized_total": total,
                "start_at": start_at,
                "end_at": end_at,
                "start_at_local": start_at_local,
                "end_at_local": end_at_local,
                "timezone": "Asia/Shanghai",
                "summarizer": "companion_memory_schema_v1",
                "summary_schema_version": "companion_memory_v1",
                "owner_bot_id": self._bot_subject_id(ctx),
                "persona_id": clean_text(ctx.persona_id, 96),
                "summary_quality": summary_quality,
                "evidence_gate_passed": evidence_gate_passed,
                "raw_timeline_preserved": not evidence_gate_passed,
                "quality_warnings": list(
                    (payload or {}).get("_quality_warnings") or []
                ),
                "canonical_summary": clean_text(
                    (payload or {}).get("canonical_summary"), 2000
                ),
                "persona_summary": clean_text(
                    (payload or {}).get("persona_summary")
                    or (payload or {}).get("summary"),
                    2000,
                ),
                "topics": (payload or {}).get("topics", []),
                "key_facts": (payload or {}).get("key_facts", []),
                "key_facts_with_refs": (payload or {}).get("key_facts_with_refs", []),
                "associations": (payload or {}).get("associations", []),
                "routine_check_notes": (payload or {}).get("routine_check_notes", []),
                "bot_self_fact_count": len(
                    (payload or {}).get("bot_self_facts", []) or []
                ),
                "participants": (payload or {}).get("participants", []),
                "sentiment": clean_text((payload or {}).get("sentiment"), 20),
                "summary_provider_id": clean_text(used_summary.get("provider_id"), 120),
                "summary_provider_source": clean_text(used_summary.get("source"), 40),
                "summary_boundary": clean_text(rows[-1].get("id") if rows else "", 160),
                "source_event_ids": [
                    clean_text(row.get("id"), 160)
                    for row in rows
                    if clean_text(row.get("id"), 160)
                ],
                "policy_version": "memory_capture_v1",
            },
        )
        self.importance.calibrate(record, source="conversation_summary")
        memory_id = await self.store.finish_summary_batch(
            batch_id, [str(row["id"]) for row in rows], record=record
        )
        self._schedule_memory_embedding(memory_id, record)
        await self._persist_assertions(ctx, payload or {}, memory_id, rows)
        await self._record_verified_group_bot_self_facts(
            ctx, rows, payload or {}, memory_id
        )
        await self._index_summary_knowledge_graph(ctx, record, payload or {}, memory_id)
        marked = len(rows)
        logger.info(
            "[MemoryCompanion] 已生成阶段性长期记忆: session=%s memory=%s events=%s marked=%s",
            ctx.session_id,
            memory_id,
            len(rows),
            marked,
        )
        return memory_id
