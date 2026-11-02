from __future__ import annotations

import asyncio
from typing import Any

from .astrbot_compat import logger


class HistoricalChatImporterLifecycle:
    """Lifecycle; shared runtime resources belong to the facade."""

    def _start_worker(self, batch_id: str) -> None:
        current = self._tasks.get(batch_id)
        if current is not None and not current.done():
            return
        task = self.service._spawn_background(
            self._run_batch(batch_id), label=f"chat_import:{batch_id[-10:]}"
        )
        if task is not None:
            self._tasks[batch_id] = task

    def _task_active(self, batch_id: str) -> bool:
        task = self._tasks.get(batch_id)
        return task is not None and not task.done()

    async def pause_batch(self, batch_id: str) -> dict[str, Any]:
        batch = await self.store.get_chat_import_batch(batch_id)
        if not batch:
            raise ValueError("导入批次不存在")
        if batch.get("state") in {
            "completed",
            "completed_with_warnings",
            "rolled_back",
        }:
            return await self.status(batch_id)
        await self.store.update_chat_import_batch(batch_id, state="paused", error="")
        return await self.status(batch_id)

    async def resume_batch(self, batch_id: str) -> dict[str, Any]:
        batch = await self.store.get_chat_import_batch(batch_id)
        if not batch:
            raise ValueError("导入批次不存在")
        if batch.get("state") in {
            "completed",
            "completed_with_warnings",
            "rolled_back",
        }:
            return await self.status(batch_id)
        if batch.get("state") in {"reconciling", "enriching", "indexing"}:
            self._start_worker(batch_id)
            return await self.status(batch_id)
        recover_processing = not self._task_active(batch_id)
        recover_statuses = {"failed"}
        if recover_processing:
            recover_statuses.add("processing")
        recoverable_segments = await self.store.chat_import_segments(
            batch_id, statuses=recover_statuses
        )
        for segment in recoverable_segments:
            await self.store.update_chat_import_segment(
                segment["id"], status="retry", attempts=0, error=""
            )
        await self.store.update_chat_import_batch(batch_id, state="running", error="")
        self._start_worker(batch_id)
        return await self.status(batch_id)

    async def rollback_batch(self, batch_id: str) -> dict[str, Any]:
        task = self._tasks.get(batch_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        api = self._private_api()
        rollback = (
            getattr(api, "rollback_historical_relationship_observations", None)
            if api is not None
            else None
        )
        if callable(rollback):
            try:
                result = rollback(batch_id)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                logger.warning(
                    "[MemoryCompanion] 回滚关系网历史观察失败: batch=%s error=%s",
                    batch_id,
                    exc,
                )
        deleted = await self.store.rollback_chat_import_batch(batch_id)
        return {"batch_id": batch_id, "state": "rolled_back", "deleted": deleted}
