from __future__ import annotations

import asyncio
import time
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    MemoryRecord,
    clean_text,
)


class MemoryCompanionServiceBackgroundRuntime:
    """Background runtime; state and transaction ownership remain in the facade."""

    def _spawn_background(
        self,
        coro: Any,
        *,
        label: str,
        defer_during_grace: bool = False,
    ) -> asyncio.Task[Any] | None:
        deferred_pending_coro: Any = None
        if self._closing or self._closed:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return None
        # 启动宽限期屏障：核心迁移窗口内不真正创建后台任务，避免并发 DB 风暴。
        if self._background_grace_seconds > 0:
            elapsed = time.monotonic() - self._service_created_at
            if elapsed < self._background_grace_seconds:
                delay = self._background_grace_seconds - elapsed
                if not defer_during_grace:
                    close = getattr(coro, "close", None)
                    if callable(close):
                        close()
                    logger.debug(
                        "[MemoryCompanion] 启动宽限期内暂缓后台任务 %s (%.0fs/%.0fs)",
                        label,
                        elapsed,
                        self._background_grace_seconds,
                    )
                    return None

                pending_coro = coro
                deferred_pending_coro = pending_coro

                async def _deferred_background() -> None:
                    try:
                        await asyncio.sleep(delay)
                        await pending_coro
                    except asyncio.CancelledError:
                        close = getattr(pending_coro, "close", None)
                        if callable(close):
                            close()
                        raise

                coro = _deferred_background()
        try:
            task = asyncio.create_task(coro, name=f"memory_companion:{label}")
        except RuntimeError:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            close_pending = getattr(deferred_pending_coro, "close", None)
            if callable(close_pending):
                close_pending()
            logger.warning(
                "[MemoryCompanion] 无运行事件循环，后台任务未启动: %s", label
            )
            return None
        self._background_tasks.add(task)

        def _done(done_task: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(done_task)
            if done_task.cancelled():
                close_pending = getattr(deferred_pending_coro, "close", None)
                if callable(close_pending):
                    close_pending()
                return
            try:
                exc = done_task.exception()
            except Exception as error:
                logger.warning(
                    "[MemoryCompanion] 读取后台任务状态失败: %s error=%s", label, error
                )
                return
            if exc:
                logger.warning(
                    "[MemoryCompanion] 后台任务异常: %s error=%s",
                    label,
                    exc,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_done)
        return task

    def _schedule_memory_embedding(
        self, memory_id: str, record: MemoryRecord | None = None
    ) -> None:
        if not self.config.bool("retrieval.embedding_enabled", False):
            return
        # Bot Personal archive rows are served by their dedicated bridge. The
        # display-reference text is not a normal memory document.
        if (
            record is not None
            and clean_text(record.source_plugin, 120) == "bot_personal_bridge"
        ):
            return
        memory_id = clean_text(memory_id, 120)
        if not memory_id or memory_id in self._embedding_memory_inflight:
            return
        self._embedding_memory_inflight.add(memory_id)
        task = self._spawn_background(
            self._background_embed_memory(memory_id, record),
            label=f"embedding:{memory_id[:12]}",
        )
        if task is None:
            self._embedding_memory_inflight.discard(memory_id)

    def _schedule_embedding_backfill(self, provider: Any, provider_id: str) -> None:
        if not self.config.bool("retrieval.embedding_enabled", False):
            return
        if not self.config.bool("retrieval.embedding_backfill_enabled", True):
            return
        provider_id = clean_text(provider_id, 160)
        if not provider_id or provider is None:
            return
        key = provider_id
        if key in self._embedding_backfill_inflight:
            return
        interval = max(
            0, self.config.int("retrieval.embedding_backfill_interval_seconds", 300)
        )
        now = time.monotonic()
        if (
            interval > 0
            and now - self._embedding_backfill_last_run.get(key, 0.0) < interval
        ):
            return
        self._embedding_backfill_inflight.add(key)
        self._embedding_backfill_last_run[key] = now
        task = self._spawn_background(
            self._background_backfill_embeddings(provider, provider_id),
            label=f"embedding_backfill:{provider_id[:24]}",
        )
        if task is None:
            self._embedding_backfill_inflight.discard(key)
