from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .task_caption import SemanticTaskManagerCaption
from .task_control import SemanticTaskManagerControl
from .task_delete import SemanticTaskManagerDelete
from .task_edits import SemanticTaskManagerEdits
from .task_index import SemanticTaskManagerIndex
from .task_mutation import SemanticTaskManagerMutation
from .task_providers import SemanticTaskManagerProviders
from .task_state import SemanticTaskManagerState
from .task_status import SemanticTaskManagerStatus
from .task_support import _revision_category_choice as _revision_category_choice
from .task_support import _revision_original_category as _revision_original_category
from .task_worker import SemanticTaskManagerWorker


class SemanticTaskManager(
    SemanticTaskManagerEdits,
    SemanticTaskManagerMutation,
    SemanticTaskManagerState,
    SemanticTaskManagerProviders,
    SemanticTaskManagerStatus,
    SemanticTaskManagerControl,
    SemanticTaskManagerIndex,
    SemanticTaskManagerDelete,
    SemanticTaskManagerCaption,
    SemanticTaskManagerWorker,
):
    def __init__(
        self,
        plugin_data_dir: Path | str,
        *,
        context: Any = None,
        config: dict | None = None,
    ):
        self.plugin_data_dir = Path(plugin_data_dir).resolve()
        self.context = context
        self.config = config or {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._index_tasks: dict[str, asyncio.Task] = {}
        self._external_pack_operations: dict[str, str] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        """插件卸载时取消后台任务；每张图片的状态已在处理前后持久化。"""
        tasks = list(
            {
                task
                for task in (*self._tasks.values(), *self._index_tasks.values())
                if not task.done()
            }
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _lock(self, pack_id: str) -> asyncio.Lock:
        return self._locks.setdefault(pack_id, asyncio.Lock())

    @staticmethod
    def _task_is_alive(task: asyncio.Task | None) -> bool:
        return bool(task and not task.done())

    def _semantic_operation_is_alive(self, pack_id: str) -> bool:
        return self._task_is_alive(self._tasks.get(pack_id)) or self._task_is_alive(
            self._index_tasks.get(pack_id)
        )

    def active_pack_tasks(self, exclude_pack_id: str = "") -> list[dict[str, Any]]:
        """返回本进程内其他资源包的活动任务，用于提示总请求压力。"""
        result = []
        pack_ids = set(self._tasks) | set(self._index_tasks)
        for pack_id in sorted(pack_ids):
            if pack_id == exclude_pack_id or not self._semantic_operation_is_alive(
                pack_id
            ):
                continue
            state = self._load_state(pack_id)
            try:
                concurrency = max(
                    1,
                    min(
                        16,
                        int(state.get("concurrency") or self._configured_concurrency()),
                    ),
                )
            except (TypeError, ValueError):
                concurrency = self._configured_concurrency()
            phase = str(state.get("task_phase") or "")
            result.append(
                {
                    "pack_id": pack_id,
                    "task_status": str(state.get("task_status") or "running"),
                    "task_phase": phase,
                    "concurrency": concurrency if phase != "indexing" else 0,
                }
            )
        return result

    def begin_external_pack_operation(self, pack_id: str, operation: str) -> None:
        """登记会改动资源包文件的外部任务，阻止语义任务同时启动。"""
        pack_id = self._validate_pack_id(pack_id)
        self.assert_pack_mutation_allowed(pack_id, operation)
        self._external_pack_operations[pack_id] = str(operation or "外部文件任务")

    def end_external_pack_operation(self, pack_id: str) -> None:
        self._external_pack_operations.pop(str(pack_id or "").strip(), None)

    def assert_pack_mutation_allowed(
        self, pack_id: str, operation: str = "修改资源包"
    ) -> None:
        """拒绝与语义任务、暂停队列或其他文件任务冲突的资源包修改。"""
        pack_id = self._validate_pack_id(pack_id)
        external_operation = self._external_pack_operations.get(pack_id)
        if external_operation:
            raise RuntimeError(
                f"资源包 {pack_id} 正在执行“{external_operation}”，暂时不能{operation}"
            )
        if self._semantic_operation_is_alive(pack_id):
            raise RuntimeError(
                f"资源包 {pack_id} 的语义任务尚未结束，暂时不能{operation}；"
                "请等待完成，如必须立即修改，请先在语义页清空任务队列"
            )
        state = self._load_state(pack_id)
        if state.get("task_status") in {"running", "paused"}:
            raise RuntimeError(
                f"资源包 {pack_id} 存在暂停或中断的语义队列，暂时不能{operation}；"
                "请先继续完成或清空任务队列"
            )
