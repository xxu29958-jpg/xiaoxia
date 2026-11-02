from __future__ import annotations

import asyncio
from typing import Any

from .astrbot_compat import (
    logger,
)
from .identity import (
    normalize_session_context_fields,
)
from .models import (
    SessionContext,
    clean_text,
)
from .operations import (
    PRESET_LABELS,
    apply_preset,
    build_operational_report,
    detect_preset,
    persist_runtime_config,
)


class MemoryCompanionServiceOperations:
    """service operations; the facade owns all shared resources."""

    async def import_livingmemory(self, *, configured_path: str = "") -> dict[str, Any]:
        if self.config.bool("maintenance.backup_before_import", True):
            backup = await self.store.backup_async(".before_livingmemory_import")
            logger.info("[MemoryCompanion] LivingMemory 导入前已备份数据库: %s", backup)
        return await self.migrator.import_data(
            configured_path=configured_path,
            default_review_status=str(
                self.config.get("livingmemory_migration.default_review_status", "auto")
                or "auto"
            ),
            limit=self.config.int("livingmemory_migration.import_limit", 5000),
        )

    def operation_preset_status(self) -> dict[str, Any]:
        preset = detect_preset(self.config)
        return {"preset": preset, "label": PRESET_LABELS[preset]}

    def apply_operation_preset(self, name: str) -> dict[str, Any]:
        raw = self.config.raw
        if not isinstance(raw, dict):
            raise RuntimeError("runtime config is not writable")
        result = apply_preset(raw, name)
        result["config_path"] = str(persist_runtime_config(raw, self.data_dir))
        self._retrieval_result_cache.clear()
        return result

    async def operational_report(self) -> dict[str, Any]:
        report = await build_operational_report(self)
        report["summary_progress"] = await self.store.summary_progress()
        return report

    async def export_portable_data(self) -> dict[str, Any]:
        return await self.portable_archive.export()

    def preview_portable_data(self, path: str) -> dict[str, Any]:
        return self.portable_archive.preview(path)

    async def import_portable_data(self, path: str) -> dict[str, Any]:
        result = await self.portable_archive.import_data(path)
        self._retrieval_result_cache.clear()
        return result

    async def preview_memory_audit(
        self, event: Any | None = None, limit: int = 0
    ) -> dict[str, Any]:
        ctx = (
            await self.identity.resolve_event_context(event)
            if event is not None
            else SessionContext()
        )
        return await self.memory_audit.preview(ctx, limit)

    async def memory_audit_status(self, batch_id: str) -> dict[str, Any]:
        return await self.memory_audit.status(batch_id)

    async def apply_memory_audit(self, batch_id: str, confirm: str) -> dict[str, Any]:
        return await self.memory_audit.apply(batch_id, confirm)

    async def rollback_memory_audit(
        self, batch_id: str, confirm: str
    ) -> dict[str, Any]:
        return await self.memory_audit.rollback(batch_id, confirm)

    async def clear_all_memory_data(self) -> dict[str, Any]:
        await self._cancel_background_tasks_for_clear()
        relationship_observations = (
            await self.chat_importer.clear_relationship_observations()
        )
        result = await self.store.clear_all_memory_data()
        result["historical_chat_archives"] = await asyncio.to_thread(
            self.chat_importer.clear_archives
        )
        result["historical_relationship_observations"] = relationship_observations
        self._relationship_phase_state.clear()
        self._save_relationship_phase_state()
        self._emotional_event_queue.clear()
        self._retrieval_result_cache.clear()
        self._reconstruction_states.clear()
        self._embedding_backfill_inflight.clear()
        self._embedding_memory_inflight.clear()
        self._embedding_backfill_last_run.clear()
        # 清库必须同时通知 store：策略表已经清空，持有的 ACL 投影与已注册
        # 的派生缓存都不允许保留旧值。
        self.store.clear_acl_projection_sync()
        result["invalidated_caches"] = await asyncio.to_thread(
            self.store.invalidate_registered_caches,
            reason="clear_all_memory_data",
        )
        return result

    async def clear_scoped_memory(
        self,
        *,
        target_type: str,
        group_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        await self._cancel_background_tasks_for_clear()
        result = await self.store.clear_scoped_memory(
            target_type=target_type,
            group_id=group_id,
            user_id=user_id,
        )
        self._clear_scoped_runtime_state(
            target_type=target_type,
            group_id=group_id,
            user_id=user_id,
        )
        return result

    async def _cancel_background_tasks_for_clear(self) -> None:
        current = asyncio.current_task()
        tasks = [
            task
            for task in self._background_tasks
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._embedding_backfill_inflight.clear()
        self._embedding_memory_inflight.clear()
        self._embedding_backfill_last_run.clear()

    def _clear_scoped_runtime_state(
        self, *, target_type: str, group_id: str, user_id: str
    ) -> None:
        target_type = clean_text(target_type, 40).lower()
        group_id = clean_text(group_id, 120)
        user_id = clean_text(user_id, 120)

        def identity_matches(identity: Any) -> bool:
            if not isinstance(identity, dict):
                return False
            scope = clean_text(identity.get("scope"), 40)
            target_id = clean_text(identity.get("target_id"), 200)
            member_id = clean_text(identity.get("member_id"), 120)
            if target_type == "group":
                return scope == "group" and target_id == group_id
            if target_type == "private":
                return scope == "private" and target_id == user_id
            return scope == "group" and target_id == group_id and member_id == user_id

        legacy_keys = {
            f"group:{group_id}" if group_id else "",
            f"private:{user_id}" if user_id else "",
        }
        for key, state in list(self._relationship_phase_state.items()):
            if key in legacy_keys or identity_matches(
                state.get("_identity") if isinstance(state, dict) else None
            ):
                self._relationship_phase_state.pop(key, None)
        self._save_relationship_phase_state()

        for session_id in list(self._emotional_event_queue):
            normalized = normalize_session_context_fields(
                session_id=session_id, scope="unknown"
            )
            scope = normalized["scope"]
            if (
                target_type == "group"
                and scope == "group"
                and normalized["group_id"] == group_id
            ):
                self._emotional_event_queue.pop(session_id, None)
            elif (
                target_type == "private"
                and scope == "private"
                and normalized["user_id"] == user_id
            ):
                self._emotional_event_queue.pop(session_id, None)
            elif (
                target_type == "group_member"
                and scope == "group"
                and normalized["group_id"] == group_id
            ):
                self._emotional_event_queue.pop(session_id, None)
        self._retrieval_result_cache.clear()
        self._reconstruction_states.clear()
