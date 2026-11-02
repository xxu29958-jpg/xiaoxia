from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from .astrbot_compat import (
    logger,
)
from .bridge import (
    serialize_memory,
)
from .models import (
    MemoryRecord,
    SearchResult,
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceBridgeContext:
    """Bridge context; state and transaction ownership remain in the facade."""

    async def bridge_search(
        self,
        query: str,
        *,
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> list[dict[str, Any]]:
        gate = await self._p5_gate(
            sink="bridge_serialization",
            attestation=p5_attestation,
            consumer=p5_attestation_consumer,
        )
        if not gate.get("ok"):
            return []
        ctx = self.session_context_from_bridge(session_context)
        results = await self.search(
            query, ctx, top_k or self.config.int("memory_injection.top_k", 4)
        )
        serialized = [
            serialize_memory(item.memory, item.score, item.reason) for item in results
        ]
        snapshot = gate.get("snapshot")
        if snapshot is not None:
            await self._p5_record_observed(
                [item.memory.id for item in results], snapshot
            )
        return serialized

    async def bridge_compose_injection(
        self,
        query: str,
        *,
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
        _p5_gate_passed: bool = False,
    ) -> str:
        if not _p5_gate_passed:
            gate = await self._p5_gate(
                sink="bridge_serialization",
                attestation=p5_attestation,
                consumer=p5_attestation_consumer,
            )
            if not gate.get("ok"):
                return ""
        ctx = self.session_context_from_bridge(session_context)
        query_text = clean_text(query or ctx.message_text, 1400)
        if not query_text:
            return ""
        return await self._compose_memory_injection(
            ctx,
            explicit_query=query_text,
            top_k=top_k or self.config.int("memory_injection.top_k", 4),
            max_chars=max_chars or self.config.int("memory_injection.max_chars", 1800),
            note="bridge_injection",
            write_log=False,
            companion_bot_mood=companion_bot_mood,
            companion_bot_energy=companion_bot_energy,
        )

    async def bridge_compose_context(
        self,
        *,
        query: str = "",
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        retrieval_profile: str = "",
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> str:
        gate = await self._p5_gate(
            sink="bridge_serialization",
            attestation=p5_attestation,
            consumer=p5_attestation_consumer,
        )
        if not gate.get("ok"):
            return ""
        profile = clean_text(retrieval_profile, 40).lower()
        fast_profile_enabled = {
            "schedule_fast": self.config.bool(
                "private_companion_bridge.schedule_fast_context_enabled",
                True,
            ),
            "outfit_fast": self.config.bool(
                "private_companion_bridge.outfit_fast_context_enabled",
                True,
            ),
        }
        if profile in fast_profile_enabled and fast_profile_enabled[profile]:
            return await self._bridge_compose_schedule_fast_context(
                query=query,
                session_context=session_context,
                top_k=top_k,
                max_chars=max_chars,
                companion_bot_mood=companion_bot_mood,
                companion_bot_energy=companion_bot_energy,
                context_kind="outfit" if profile == "outfit_fast" else "schedule",
            )
        return await self.bridge_compose_injection(
            query,
            session_context=session_context,
            top_k=top_k,
            max_chars=max_chars,
            companion_bot_mood=companion_bot_mood,
            companion_bot_energy=companion_bot_energy,
            _p5_gate_passed=True,
        )

    async def _bridge_compose_schedule_fast_context(
        self,
        *,
        query: str,
        session_context: SessionContext | dict[str, Any] | None,
        top_k: int | None,
        max_chars: int | None,
        companion_bot_mood: str,
        companion_bot_energy: float,
        context_kind: str = "schedule",
    ) -> str:
        started = time.monotonic()
        ctx = self._normalized_session_context(
            self.session_context_from_bridge(session_context)
        )
        query_text = clean_text(query or ctx.message_text, 1400)
        if not query_text:
            return ""
        core_memories = await self.core_memories_for_context(ctx)
        core_memory_max_chars = self.config.int("core_memory.max_chars", 800)
        limit = max(1, min(8, int(top_k or 6)))
        outfit_focus = clean_text(context_kind, 40).lower() == "outfit"
        fast_path = "outfit_fast_local" if outfit_focus else "schedule_fast_local"
        skip_reason = "skipped_outfit_fast" if outfit_focus else "skipped_schedule_fast"
        records = await self.store.list_schedule_context_memories(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            bot_id=ctx.bot_id,
            limit=max(72, limit * 12) if outfit_focus else max(24, limit * 6),
            strict_session_only=ctx.strict_session_only,
        )
        policy = self.visibility_policy()
        visible = [record for record in records if policy.is_visible(record, ctx)[0]]
        if not visible:
            self._last_retrieval_path_info = {
                "mode": "basic",
                "path": fast_path,
                "reason": "no_outfit_context"
                if outfit_focus
                else "no_schedule_context",
                "candidate_count": len(records),
                "embedding_reason": skip_reason,
            }
            return self.injection.compose(
                ctx,
                [],
                max_chars or self.config.int("memory_injection.max_chars", 4000),
                core_memories=core_memories,
                core_memory_max_chars=core_memory_max_chars,
                max_item_chars=min(
                    220, self.config.int("memory_injection.max_item_chars", 320)
                ),
            )

        schedule_types = {"schedule_fragment", "persona_life", "companion_note"}
        profile_types = {"user_profile", "user_preference", "relationship_claim"}
        selected: list[tuple[str, MemoryRecord]] = []

        def record_text(record: MemoryRecord) -> str:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            metadata_parts: list[str] = []
            for key in ("query_anchors", "topics", "keywords", "summary"):
                value = metadata.get(key)
                if isinstance(value, list):
                    metadata_parts.extend(
                        clean_text(item, 120) for item in value if clean_text(item, 120)
                    )
                elif clean_text(value, 400):
                    metadata_parts.append(clean_text(value, 400))
            return clean_text(
                " ".join(
                    [
                        record.content,
                        record.evidence,
                        " ".join(record.tags or []),
                        *metadata_parts,
                    ]
                ),
                6000,
            ).lower()

        def profile_can_influence_fast_context(record: MemoryRecord) -> bool:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            return (
                clean_text(metadata.get("mention_policy"), 60).lower()
                != "avoid_unless_asked"
            )

        if outfit_focus:
            outfit_tags = {
                "daily_outfit",
                "outfit",
                "clothing",
                "今日穿搭",
                "每日穿搭",
                "衣服颜色",
            }
            clothing_terms = (
                "穿搭",
                "衣服",
                "服装",
                "颜色",
                "配色",
                "裙",
                "外套",
                "上衣",
                "裤",
                "鞋",
                "饰品",
                "自拍",
                "造型",
            )
            outfit_schedule_terms = (
                "天气",
                "气温",
                "降温",
                "下雨",
                "下雪",
                "出门",
                "外出",
                "室外",
                "运动",
                "上班",
                "工作",
                "聚会",
                "约会",
                "散步",
                "旅行",
                "正式场合",
            )
            relevant_image_terms = (
                "自拍",
                "穿搭",
                "衣服",
                "服装",
                "全身照",
                "半身照",
                "镜子",
                "造型",
            )

            def is_outfit_history(record: MemoryRecord) -> bool:
                tags = {
                    clean_text(tag, 80).lower()
                    for tag in (record.tags or [])
                    if clean_text(tag, 80)
                }
                content = clean_text(record.content, 2000)
                return (
                    bool(tags & outfit_tags)
                    or clean_text(record.id, 160).startswith(
                        "private_companion_daily_outfit_"
                    )
                    or "每日穿搭图" in content
                    or "穿搭提示摘要" in content
                )

            outfit_records = [record for record in visible if is_outfit_history(record)]
            schedule_records = [
                record
                for record in visible
                if record.memory_type in schedule_types
                and not is_outfit_history(record)
                and any(term in record_text(record) for term in outfit_schedule_terms)
            ]
            profile_records = [
                record
                for record in visible
                if record.memory_type in profile_types
                and profile_can_influence_fast_context(record)
                and any(term in record_text(record) for term in clothing_terms)
            ]
            image_records = [
                record
                for record in visible
                if record.memory_type == "image_action"
                and any(term in record_text(record) for term in relevant_image_terms)
            ]
            seen: set[str] = set()

            def append_records(
                records_to_add: list[MemoryRecord], count: int, slot: str
            ) -> None:
                added = 0
                for record in records_to_add:
                    if len(selected) >= limit or added >= count:
                        break
                    record_key = clean_text(record.id, 160) or f"object:{id(record)}"
                    if record_key in seen:
                        continue
                    seen.add(record_key)
                    selected.append((slot, record))
                    added += 1

            append_records(schedule_records, 1, "self_timeline")
            append_records(outfit_records, 2, "self_timeline")
            append_records(profile_records, 1, "user_profile")
            append_records(image_records, 1, "self_timeline")
            for records_to_add, slot in (
                (outfit_records, "self_timeline"),
                (schedule_records, "self_timeline"),
                (profile_records, "user_profile"),
                (image_records, "self_timeline"),
            ):
                append_records(records_to_add, limit, slot)
        else:
            schedule_profile_terms = (
                "日程",
                "安排",
                "提醒",
                "催",
                "主动",
                "打扰",
                "联系",
                "作息",
                "睡",
                "起床",
                "吃饭",
                "饮食",
                "工作",
                "上班",
                "下班",
                "学习",
                "运动",
                "时间",
                "早上",
                "晚上",
            )
            schedule_records = [
                record for record in visible if record.memory_type in schedule_types
            ]
            activity_records = [
                record
                for record in visible
                if record.memory_type not in schedule_types
                and record.memory_type not in profile_types
            ]
            profile_records = [
                record
                for record in visible
                if record.memory_type in profile_types
                and profile_can_influence_fast_context(record)
                and any(term in record_text(record) for term in schedule_profile_terms)
            ]
            selected.extend(
                ("self_timeline", record) for record in schedule_records[:2]
            )
            selected.extend(
                ("self_timeline", record)
                for record in activity_records[: max(0, limit - len(selected) - 1)]
            )
            if profile_records and len(selected) < limit:
                selected.append(("user_profile", profile_records[0]))
            seen = {record.id for _slot, record in selected if record.id}
            allowed_profile_objects = {id(record) for record in profile_records}
            for record in visible:
                if len(selected) >= limit:
                    break
                if (
                    record.memory_type in profile_types
                    and id(record) not in allowed_profile_objects
                ):
                    continue
                if record.id and record.id in seen:
                    continue
                slot = (
                    "user_profile"
                    if record.memory_type in profile_types
                    else "self_timeline"
                )
                selected.append((slot, record))
                if record.id:
                    seen.add(record.id)

        if not selected:
            self._last_retrieval_path_info = {
                "mode": "basic",
                "path": fast_path,
                "reason": "no_relevant_outfit_context"
                if outfit_focus
                else "no_schedule_context",
                "candidate_count": len(records),
                "embedding_reason": skip_reason,
            }
            return self.injection.compose(
                ctx,
                [],
                max_chars or self.config.int("memory_injection.max_chars", 4000),
                core_memories=core_memories,
                core_memory_max_chars=core_memory_max_chars,
                max_item_chars=min(
                    220, self.config.int("memory_injection.max_item_chars", 320)
                ),
            )

        slot_map: dict[str, list[SearchResult]] = {
            "self_timeline": [],
            "user_profile": [],
        }
        results: list[SearchResult] = []
        for index, (slot, record) in enumerate(selected):
            # Fast profiles already perform a narrow, intent-specific selection.
            # Mark usable facts as mention; tone now intentionally carries no raw text.
            expression = "mention"
            result = SearchResult(
                memory=record,
                score=max(0.1, 1.0 - index * 0.05),
                reason=f"{fast_path};slot={slot};expression={expression}",
            )
            slot_map[slot].append(result)
            results.append(result)
        slot_map = {slot: items for slot, items in slot_map.items() if items}
        self._last_retrieval_path_info = {
            "mode": "basic",
            "path": fast_path,
            "reason": "local_readonly_snapshot",
            "candidate_count": len(records),
            "selected_count": len(results),
            "embedding_reason": skip_reason,
            "provider_id": "",
        }
        if outfit_focus:
            intent_context = "- 每日穿搭参考近期穿搭、日程、服装偏好和图片；历史穿搭不代表今天已穿，未知款式不补猜。"
        else:
            intent_context = (
                "- 当前为日程连续性快速上下文；只参考近期 Bot 自我日程、行动和当前对象的稳定边界。\n"
                "- 本次未执行向量检索或重排；没有直接记录时保持不确定，不补造当前状态。"
            )
        injection = self.injection.compose(
            ctx,
            results,
            max_chars or self.config.int("memory_injection.max_chars", 4000),
            intent_context=intent_context,
            slot_sections=self._slot_sections(slot_map),
            compact_memory=True,
            emotional_tone="neutral",
            companion_bot_mood=companion_bot_mood,
            companion_bot_energy=companion_bot_energy,
            time_of_day="" if outfit_focus else self._compute_time_of_day(),
            address_hint="" if outfit_focus else self._address_hint_for_injection(ctx),
            core_memories=core_memories,
            core_memory_max_chars=core_memory_max_chars,
            max_item_chars=min(
                220, self.config.int("memory_injection.max_item_chars", 320)
            ),
        )
        logger.info(
            "[MemoryCompanion] %s快速上下文已生成: session=%s candidates=%s selected=%s chars=%s elapsed_ms=%s",
            "穿搭" if outfit_focus else "日程",
            ctx.session_id,
            len(records),
            len(results),
            len(injection),
            int((time.monotonic() - started) * 1000),
        )
        return injection

    def bridge_get_emotional_events(
        self, *, session_id: str = "", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Consume a redacted exact-window queue during the capability migration."""

        self._cleanup_emotional_event_queue()

        try:
            compatibility_enabled = self.config.bool(
                "private_companion_bridge.legacy_emotion_compatibility_enabled",
                True,
            )
        except Exception:
            compatibility_enabled = False
        if compatibility_enabled is not True:
            return []
        safe_session = clean_text(session_id, 220)
        if not safe_session or ":" not in safe_session:
            return []
        try:
            bounded_limit = max(1, min(20, int(limit or 5)))
        except (TypeError, ValueError):
            bounded_limit = 5
        now = time.time()
        queue = self._emotional_event_queue.get(safe_session, [])
        fresh: list[dict[str, Any]] = []
        for item in queue:
            if not isinstance(item, dict):
                continue
            try:
                age = now - float(item.get("ts") or 0.0)
            except (TypeError, ValueError):
                continue
            if 0.0 <= age < self._EMOTIONAL_EVENT_TTL:
                fresh.append(item)
        self._emotional_event_queue[safe_session] = fresh
        selected = sorted(
            fresh, key=lambda item: float(item.get("ts") or 0.0), reverse=True
        )[:bounded_limit]
        consumed = {
            clean_text(item.get("id") or item.get("event_id"), 96) for item in selected
        }
        if consumed:
            self._emotional_event_queue[safe_session] = [
                item
                for item in fresh
                if clean_text(item.get("id") or item.get("event_id"), 96)
                not in consumed
            ]
        return [
            {
                "id": clean_text(item.get("id") or item.get("event_id"), 96),
                "event_type": clean_text(item.get("event_type"), 48),
                "energy_delta": float(
                    item.get("energy_delta") or item.get("applied_energy_delta") or 0.0
                ),
                "mood_hint": clean_text(item.get("mood_hint"), 40),
                "ts": float(item.get("ts") or 0.0),
            }
            for item in selected
        ]

    async def bridge_search_open_loops(
        self, *, session_id: str = "", limit: int = 3
    ) -> list[dict[str, Any]]:
        """Search for unresolved open-loop / promise memories for proactive companionship."""
        ctx = SessionContext(session_id=session_id or "", scope="private")
        try:
            results = await self.search(
                "约定 承诺 下次 继续 没完成 待续 还没 回头",
                ctx,
                top_k=max(limit * 3, 6),
            )
        except Exception:
            return []
        open_loops: list[dict[str, Any]] = []
        now = time.time()
        for item in results:
            memory = item.memory
            metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
            try:
                open_loop_w = float(metadata.get("open_loop_weight") or 0.0)
                promise_w = float(metadata.get("promise_weight") or 0.0)
            except Exception:
                open_loop_w = promise_w = 0.0
            if max(open_loop_w, promise_w) < 0.30:
                continue
            if metadata.get("resolved_at"):
                continue
            occurred = clean_text(memory.occurred_at or memory.created_at, 40)
            age_days = None
            if occurred:
                try:
                    dt = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                    age_days = (now - dt.timestamp()) / 86400.0
                except Exception:
                    pass
            if age_days is not None and age_days < 0.5:
                continue
            open_loops.append(
                {
                    "memory_id": memory.id,
                    "content": clean_text(memory.content, 300),
                    "session_id": memory.session_id,
                    "occurred_at": occurred,
                    "age_days": round(age_days, 1) if age_days is not None else None,
                    "open_loop_weight": round(open_loop_w, 3),
                    "promise_weight": round(promise_w, 3),
                    "memory_reason": clean_text(metadata.get("memory_reason"), 200),
                }
            )
            if len(open_loops) >= limit:
                break
        return open_loops
