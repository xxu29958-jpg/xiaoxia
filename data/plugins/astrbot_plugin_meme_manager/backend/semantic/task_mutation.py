from __future__ import annotations

import asyncio
from typing import Any


class SemanticTaskManagerMutation:
    """Mutation; shared runtime resources belong to the facade."""

    async def run_locked_pack_mutation(
        self, pack_id: str, operation: str, mutation: Any
    ) -> Any:
        """在语义任务锁内执行一个不含 await 的图包变更。"""
        pack_id = self._validate_pack_id(pack_id)
        if not callable(mutation):
            raise TypeError("mutation 必须可调用")
        async with self._lock(pack_id):
            self.assert_pack_mutation_allowed(pack_id, operation)
            return await self._run_blocking(mutation)

    @staticmethod
    async def _run_blocking(function: Any, *args: Any, **kwargs: Any) -> Any:
        """运行同步工作，并在协程取消前等待工作线程安全收尾。

        Args:
            function: 要在工作线程中执行的同步可调用对象。
            *args: 传递给可调用对象的位置参数。
            **kwargs: 传递给可调用对象的关键字参数。

        Returns:
            同步可调用对象的返回值。

        Raises:
            asyncio.CancelledError: 调用协程被取消且工作线程已经收尾。
        """
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(worker)
            except Exception:
                pass
            raise

    async def _cancel_index_task(self, pack_id: str) -> None:
        index_task = self._index_tasks.get(pack_id)
        if not self._task_is_alive(index_task) or index_task is asyncio.current_task():
            return
        index_task.cancel()
        await asyncio.gather(index_task, return_exceptions=True)
