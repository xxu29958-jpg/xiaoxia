from __future__ import annotations

from typing import Any

from .astrbot_compat import (
    logger,
)
from .bridge import (
    serialize_memory,
)
from .models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceCorePreferences:
    """service core preferences; the facade owns all shared resources."""

    async def add_manual_memory(self, event: Any, content: str) -> str:
        ctx = await self.identity.resolve_event_context(event)
        visibility = "internal"
        if ctx.scope == "private":
            visibility = "private_pair"
        elif ctx.scope == "group":
            visibility = "group_public"
        record = MemoryRecord(
            memory_type="manual_memory",
            subject=EntityRef(
                kind="user", id=ctx.user_id, name=ctx.user_name, role="admin"
            ),
            object=self._bot_entity(ctx),
            scope=ctx.scope,
            session_id=ctx.session_id,
            platform=ctx.platform,
            group_id=ctx.group_id,
            visibility=visibility,
            sayability="direct",
            reality_level="real_user_fact",
            lifecycle="stable_memory",
            content=content,
            evidence=content,
            confidence=0.9,
            importance=0.75,
            review_status="auto",
            tags=["manual"],
            metadata={"owner_bot_id": self._bot_subject_id(ctx)},
        )
        self.importance.calibrate(record, source="manual_memory")
        memory_id = await self.store.insert_memory(record)
        self._schedule_memory_embedding(memory_id, record)
        return memory_id

    async def core_memories_for_context(
        self,
        ctx: SessionContext,
        *,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """Return visible always-in-context blocks without similarity scoring."""
        if not self.config.bool("core_memory.enabled", True):
            return []
        ctx = self._normalized_session_context(ctx)
        result_limit = max(
            1,
            int(limit)
            if limit is not None
            else self.config.int("core_memory.max_blocks", 8),
        )
        candidates = await self.store.list_core_memories(
            limit=max(20, result_limit, self.config.int("core_memory.scan_limit", 200))
        )
        policy = self.visibility_policy()
        visible: list[MemoryRecord] = []
        for record in candidates:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            if metadata.get("core_enabled", True) is not True:
                continue
            persona_id = clean_text(metadata.get("persona_id"), 120)
            if persona_id and persona_id != clean_text(ctx.persona_id, 120):
                continue
            core_scope = clean_text(metadata.get("core_scope"), 24).lower()
            target_id = clean_text(metadata.get("target_id"), 160)
            if (
                core_scope == "private"
                and target_id
                and target_id != clean_text(ctx.user_id, 160)
            ):
                continue
            if (
                core_scope == "group"
                and target_id
                and target_id != clean_text(ctx.group_id, 160)
            ):
                continue
            allowed, _reason = policy.is_visible(record, ctx)
            if not allowed:
                continue
            visible.append(record)
            if len(visible) >= result_limit:
                break
        return visible

    async def list_core_memory_blocks(self) -> list[MemoryRecord]:
        return await self.store.list_core_memories(limit=1000)

    async def save_core_memory_block(self, payload: dict[str, Any]) -> dict[str, Any]:
        label = clean_text(payload.get("label"), 80)
        content = clean_text(payload.get("content"), 4000)
        if not label:
            return {"ok": False, "code": "missing_label"}
        if not content:
            return {"ok": False, "code": "missing_content"}

        core_scope = clean_text(payload.get("scope"), 24).lower() or "global"
        if core_scope not in {"global", "private", "group"}:
            return {"ok": False, "code": "invalid_scope"}
        target_id = clean_text(payload.get("target_id"), 160)
        if core_scope in {"private", "group"} and not target_id:
            return {"ok": False, "code": "missing_target_id"}
        core_kind = clean_text(payload.get("kind"), 32).lower() or "fact"
        if core_kind not in {
            "rule",
            "boundary",
            "preference",
            "profile",
            "fact",
            "state",
        }:
            return {"ok": False, "code": "invalid_kind"}
        try:
            priority = max(0, min(100, int(payload.get("priority", 50))))
        except (TypeError, ValueError):
            return {"ok": False, "code": "invalid_priority"}

        bot_id = clean_text(payload.get("bot_id"), 120) or "self"
        persona_id = clean_text(payload.get("persona_id"), 120)
        platform = clean_text(payload.get("platform"), 80)
        session_id = clean_text(payload.get("session_id"), 220)
        memory_id = clean_text(payload.get("id"), 120)
        if not memory_id:
            memory_id = self.stable_id(
                "core_memory",
                core_scope,
                target_id,
                bot_id,
                persona_id,
                label.casefold(),
            )
        for existing in await self.store.list_core_memories(limit=1000):
            if existing.id == memory_id:
                continue
            existing_metadata = (
                existing.metadata if isinstance(existing.metadata, dict) else {}
            )
            same_key = (
                clean_text(existing_metadata.get("core_label"), 80).casefold()
                == label.casefold()
                and clean_text(existing_metadata.get("core_scope"), 24).lower()
                == core_scope
                and clean_text(existing_metadata.get("target_id"), 160) == target_id
                and clean_text(existing_metadata.get("owner_bot_id"), 120) == bot_id
                and clean_text(existing_metadata.get("persona_id"), 120) == persona_id
            )
            if same_key:
                return {"ok": False, "code": "label_conflict", "id": existing.id}

        if core_scope == "private":
            subject = EntityRef(
                kind="user",
                id=target_id,
                name=clean_text(payload.get("target_name"), 80),
                role="user",
            )
            object_ref = EntityRef.bot_self(bot_id)
            record_scope = "private"
            visibility = "private_pair"
            group_id = ""
        elif core_scope == "group":
            subject = EntityRef(
                kind="group",
                id=target_id,
                name=clean_text(payload.get("target_name"), 80),
                role="group",
            )
            object_ref = EntityRef.bot_self(bot_id)
            record_scope = "group"
            visibility = "group_public"
            group_id = target_id
        else:
            subject = EntityRef.bot_self(bot_id)
            object_ref = EntityRef.bot_self(bot_id)
            record_scope = "global"
            visibility = "bot_self"
            group_id = ""

        metadata = {
            "core_memory": True,
            "core_label": label,
            "core_kind": core_kind,
            "core_scope": core_scope,
            "core_priority": priority,
            "core_enabled": bool(payload.get("enabled", True)),
            "target_id": target_id,
            "persona_id": persona_id,
            "owner_bot_id": bot_id,
            "managed_by": "memory_panel",
        }
        record = MemoryRecord(
            id=memory_id,
            memory_type="core_memory",
            subject=subject,
            object=object_ref,
            scope=record_scope,
            session_id=session_id,
            platform=platform,
            group_id=group_id,
            visibility=visibility,
            sayability="indirect",
            reality_level="explicit_user_statement",
            lifecycle="stable_memory",
            content=content,
            evidence="由记忆管理端确认的核心记忆块",
            confidence=1.0,
            importance=max(0.5, priority / 100.0),
            owner_bot_id=bot_id,
            validity_status="active",
            salience=max(0.5, priority / 100.0),
            durability="pinned",
            sensitivity="private" if core_scope != "global" else "internal",
            review_status="manual",
            tags=["core_memory", core_kind, f"core:{label}"],
            metadata=metadata,
            source_plugin="memory_companion_core",
        )
        expected_revision: int | None = None
        if payload.get("expected_revision") is not None:
            try:
                expected_revision = max(0, int(payload.get("expected_revision")))
            except (TypeError, ValueError):
                return {"ok": False, "code": "invalid_expected_revision"}
        elif not payload.get("id"):
            expected_revision = 0
        return await self.store.save_core_memory(
            record,
            expected_revision=expected_revision,
        )

    async def delete_core_memory_block(self, memory_id: str) -> dict[str, Any]:
        memory_id = clean_text(memory_id, 120)
        record = await self.store.get_memory(memory_id)
        if record is None:
            return {"ok": False, "code": "not_found"}
        if record.memory_type != "core_memory":
            return {"ok": False, "code": "memory_type_conflict"}
        return {"ok": bool(await self.store.delete_memory(memory_id))}

    async def tool_core_memory(
        self,
        event: Any,
        *,
        action: str,
        label: str = "",
        content: str = "",
        kind: str = "fact",
        priority: int = 50,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if not self.config.bool("core_memory.llm_management_enabled", True):
            return {"ok": False, "code": "llm_management_disabled"}
        ctx = self._normalized_session_context(
            await self.identity.resolve_event_context(event)
        )
        if ctx.scope != "private" or not ctx.user_id:
            return {"ok": False, "code": "private_scope_required"}

        normalized_action = clean_text(action, 24).lower()
        action_aliases = {
            "list": "list",
            "get": "list",
            "查看": "list",
            "set": "set",
            "upsert": "set",
            "update": "set",
            "设置": "set",
            "修改": "set",
            "delete": "delete",
            "remove": "delete",
            "删除": "delete",
        }
        normalized_action = action_aliases.get(normalized_action, "")
        if not normalized_action:
            return {"ok": False, "code": "invalid_action"}

        records = await self.core_memories_for_context(ctx, limit=1000)
        private_records: list[MemoryRecord] = []
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            if (
                clean_text(metadata.get("core_scope"), 24) == "private"
                and clean_text(metadata.get("target_id"), 160) == ctx.user_id
            ):
                private_records.append(record)

        if normalized_action == "list":
            return {
                "ok": True,
                "blocks": [
                    {
                        "id": record.id,
                        "label": clean_text(record.metadata.get("core_label"), 80),
                        "kind": clean_text(record.metadata.get("core_kind"), 32),
                        "content": clean_text(record.content, 1000),
                        "revision": int(record.metadata.get("core_revision") or 0),
                    }
                    for record in private_records
                ],
            }

        clean_label = clean_text(label, 80)
        if not clean_label:
            return {"ok": False, "code": "missing_label"}
        existing = next(
            (
                record
                for record in private_records
                if clean_text(record.metadata.get("core_label"), 80).casefold()
                == clean_label.casefold()
            ),
            None,
        )
        if normalized_action == "delete":
            if existing is None:
                return {"ok": False, "code": "not_found"}
            return await self.delete_core_memory_block(existing.id)

        if not clean_text(content, 4000):
            return {"ok": False, "code": "missing_content"}
        payload = {
            "id": existing.id if existing is not None else "",
            "expected_revision": (
                int(existing.metadata.get("core_revision") or 0)
                if existing is not None
                else 0
            ),
            "label": clean_label,
            "content": content,
            "kind": kind,
            "priority": priority,
            "enabled": enabled,
            "scope": "private",
            "target_id": ctx.user_id,
            "target_name": ctx.user_name,
            "bot_id": clean_text(ctx.bot_id, 120) or self._bot_subject_id(ctx),
            "persona_id": clean_text(ctx.persona_id, 120),
            "platform": ctx.platform,
            "session_id": ctx.session_id,
        }
        return await self.save_core_memory_block(payload)

    async def correct_user_memory(
        self,
        *,
        event,
        action="correct",
        memory_id="",
        expected_version="",
        correction_id="",
        content="",
        profile_value="",
        profile_polarity="",
        trace_id="",
    ):
        from .memory_revision import (
            MemoryRevisionError,
            correction_owner,
            current_memory,
            memory_ref,
        )

        ctx = await self.identity.resolve_event_context(event)
        if (
            ctx.scope != "private"
            or not self._scope_feature_enabled(ctx, "capture")
            or getattr(event, "private_companion_proactive_framework", False)
        ):
            return {"ok": False, "reason_code": "correction_scope_disabled"}
        if action == "inspect":
            gate = await self._p5_gate(event=event, sink="memory_recall")
            if not gate.get("ok"):
                return {"ok": False, "reason_code": "memory_read_gate_unavailable"}
            record = await self.store.get_memory(memory_id)
            if (
                record is None
                or not correction_owner(ctx, record)
                or not current_memory(record)
                or not self._scope_feature_enabled(ctx, "recall")
            ):
                return {"ok": False, "reason_code": "memory_not_owned_or_current"}
            ref = memory_ref(record)
            self._remember_observed_refs(event, [ref])
            return {
                "ok": True,
                "memory": serialize_memory(record),
                "memory_ref": ref,
                "profile_dimension": record.metadata.get("profile_dimension", ""),
                "profile_value": record.metadata.get("profile_value", ""),
                "profile_polarity": record.metadata.get("profile_polarity", ""),
            }
        try:
            receipt = await self.store.correct_user_memory(
                ctx,
                action=action,
                memory_id=clean_text(memory_id, 120),
                expected_version=clean_text(expected_version, 80),
                correction_id=clean_text(correction_id, 120),
                content=clean_text(content, 4000),
                profile_value=clean_text(profile_value, 500),
                profile_polarity=clean_text(profile_polarity, 40),
                trace_id=clean_text(trace_id, 160),
            )
        except MemoryRevisionError as exc:
            return {"ok": False, "reason_code": str(exc)}
        if receipt.get("ok"):
            cache = getattr(self, "_injection_cache", None)
            if isinstance(cache, dict):
                cache.pop(ctx.session_id, None)
            self._remember_observed_refs(event, [receipt["new_ref"]])
            if action == "correct" and not receipt.get("deduplicated"):
                try:
                    record = await self.store.get_memory(receipt["new_ref"]["id"])
                    if record:
                        self._schedule_memory_embedding(record.id, record)
                except Exception:
                    logger.warning("[MemoryCompanion] 纠正已提交，向量索引任务暂未启动")
        return receipt

    async def check_memory_dependencies(self, *, event, refs):
        from .memory_revision import current_memory, memory_ref

        ctx = await self.identity.resolve_event_context(event)
        if not self._scope_feature_enabled(ctx, "recall"):
            return {
                "status": "unavailable",
                "reason_code": "scope_recall_disabled",
                "items": [],
            }
        if (
            not isinstance(refs, list)
            or len(refs) > 128
            or any(
                not isinstance(ref, dict)
                or not isinstance(ref.get("id"), str)
                or not isinstance(ref.get("version"), str)
                or not ref["id"]
                or len(ref["id"]) > 120
                or len(ref["version"]) != 64
                for ref in refs
            )
        ):
            return {
                "status": "unavailable",
                "reason_code": "invalid_memory_refs",
                "items": [],
            }
        policy = self.visibility_policy()
        items = []
        records = await self.store.get_memories_by_ids([ref["id"] for ref in refs])
        for ref in refs:
            record = records.get(ref["id"])
            state = "unavailable"
            if record and (
                record.metadata.get("persona_id") in {None, "", "legacy"}
                or record.metadata["persona_id"] == ctx.persona_id
            ):
                # Archived old versions are compared without returning their text.
                from copy import deepcopy

                visible_record = deepcopy(record)
                visible_record.lifecycle = "stable_memory"
                if policy.is_visible(visible_record, ctx)[0]:
                    state = (
                        "current"
                        if current_memory(record) and memory_ref(record) == ref
                        else "changed"
                    )
            items.append({"id": ref["id"], "version": ref["version"], "state": state})
        return {
            "status": "current"
            if all(item["state"] == "current" for item in items)
            else "needs_revalidation",
            "items": items,
        }
