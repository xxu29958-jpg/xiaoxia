from __future__ import annotations

import asyncio
from typing import Any

from .models import (
    semantic_caption_is_complete,
    utc_now,
)
from .storage import (
    load_metadata,
    reconcile_metadata,
    save_metadata,
)


class SemanticTaskManagerControl:
    """Control; shared runtime resources belong to the facade."""

    @staticmethod
    def _item_is_queued(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        return not semantic_caption_is_complete(item) or item.get(
            "embedding_status"
        ) in {"pending", "running", "failed"}

    def _reset_running_items(
        self, pack_id: str, metadata: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int]:
        """将已不可能返回的进行中记录退回等待队列。"""
        pack_dir = self._pack_dir(pack_id)
        current = metadata if isinstance(metadata, dict) else load_metadata(pack_dir)
        recovered = 0
        for item in current.get("images", {}).values():
            if not isinstance(item, dict):
                continue
            changed = False
            if item.get("caption_status") == "running":
                item["caption_status"] = "pending"
                changed = True
            if item.get("embedding_status") == "running":
                item["embedding_status"] = "pending"
                changed = True
            if changed:
                item["updated_at"] = utc_now()
                recovered += 1
        if recovered:
            save_metadata(pack_dir, current)
        return current, recovered

    async def start(
        self,
        pack_id: str,
        *,
        mode: str = "full",
        force: bool = False,
        concurrency: int | None = None,
        external_data: dict | None = None,
    ) -> dict[str, Any]:
        pack_id = self._validate_pack_id(pack_id)
        mode = str(mode or "full").strip().lower()
        if mode not in {"full", "caption_only", "retry_failed"}:
            raise ValueError("mode 只能是 full、caption_only 或 retry_failed")
        if concurrency is None:
            effective_concurrency = self._configured_concurrency()
        else:
            try:
                effective_concurrency = max(1, min(16, int(concurrency)))
            except (TypeError, ValueError) as exc:
                raise ValueError("并发数必须是 1 到 16 的整数") from exc
        pack_dir = self._pack_dir(pack_id)
        if not pack_dir.is_dir():
            raise FileNotFoundError(f"表情包 {pack_id} 不存在")
        if self._task_is_alive(self._index_tasks.get(pack_id)):
            raise RuntimeError("当前资源包正在建立向量索引，请等待完成后再开始")
        async with self._lock(pack_id):
            external_operation = self._external_pack_operations.get(pack_id)
            if external_operation:
                raise RuntimeError(
                    f"资源包正在执行“{external_operation}”，请等待完成后再开始语义任务"
                )
            existing = self._tasks.get(pack_id)
            if existing and not existing.done():
                raise RuntimeError("同一个资源包已经有语义化任务在运行")
            persisted_state = self._load_state(pack_id)
            if persisted_state.get("task_status") == "paused":
                raise RuntimeError(
                    "当前语义队列处于暂停状态，请使用“继续队列”；"
                    "如需重新扫描，请先清空当前任务队列"
                )
            if persisted_state.get("task_status") == "running":
                raise RuntimeError(
                    "检测到上次运行被中断，请使用“继续队列”恢复；"
                    "如需重新扫描，请先清空当前任务队列"
                )
            embedding = None
            selection = {}
            if mode in {"full", "retry_failed"}:
                embedding = self._require_embedding_provider(pack_id, "开始完整语义化")
                selection = await self._verify_embedding_dimension(pack_id, embedding)
            metadata = await self._run_blocking(
                reconcile_metadata, pack_dir, external_data=external_data
            )
            if force:
                for item in metadata.get("images", {}).values():
                    if item.get("manual_override") or item.get("provenance") in {
                        "manual",
                        "mixed",
                    }:
                        continue
                    item["caption_status"] = "pending"
                    item["embedding_status"] = "pending"
                    item["error"] = None
            elif mode == "retry_failed":
                for item in metadata.get("images", {}).values():
                    if item.get("caption_status") == "failed":
                        item["caption_status"] = "pending"
                    if item.get("embedding_status") == "failed":
                        item["embedding_status"] = "pending"
                    item["error"] = None
            elif mode == "caption_only":
                for item in metadata.get("images", {}).values():
                    if item.get("caption_status") == "failed":
                        item["caption_status"] = "pending"
                    item["error"] = None
            # 普通“一键语义化”只处理没有描述或之前失败的图片。提示词升级、
            # 视觉模型切换都不能静默覆盖已有描述；需要重做时只能由操作者明确
            # 点击“强制重新生成”（force=True）。
            needs_caption = any(
                isinstance(item, dict)
                and not (
                    item.get("manual_override")
                    or item.get("provenance") in {"manual", "mixed"}
                )
                and (force or not semantic_caption_is_complete(item))
                for item in metadata.get("images", {}).values()
            )
            if needs_caption and not self._vision_provider_ready():
                raise RuntimeError("未配置视觉模型，无法生成图片描述")
            save_metadata(pack_dir, metadata)
            state = {
                "task_status": "running",
                "task_phase": "captioning" if needs_caption else "indexing",
                "queue_cleared": False,
                "mode": mode,
                "concurrency": effective_concurrency,
                "current": "",
                "active_items": [],
                "started_at": utc_now(),
                "finished_at": "",
                "paused_at": "",
                "paused_seconds": 0,
                "last_error": None,
                "embedding_provider_id": str(
                    selection.get("effective_provider_id") or ""
                ),
                "embedding_selection_mode": str(selection.get("selection_mode") or ""),
                "embedding_dimension": int(selection.get("verified_dimension", 0) or 0),
                "token_usage": {"input": 0, "output": 0, "total": 0, "calls": 0},
                "vision_calls": 0,
                "reclassified_items": 0,
            }
            self._save_state(pack_id, state)
            pause_event = self._pause_events.setdefault(pack_id, asyncio.Event())
            pause_event.set()
            task = asyncio.create_task(self._run(pack_id, mode=mode, force=force))
            self._tasks[pack_id] = task
        result = self.status(pack_id)
        queue_summary = (
            f"待生成描述 {result.get('queued_caption_tasks', 0)} 张，"
            f"待建立向量 {result.get('queued_embedding_tasks', 0)} 张"
        )
        if mode == "retry_failed":
            result["message"] = (
                f"失败项已重新入队：并发上限 {effective_concurrency}，{queue_summary}。"
            )
        else:
            result["message"] = (
                f"语义化队列已启动：并发上限 {effective_concurrency}，{queue_summary}。"
            )
        if result.get("other_tasks_warning"):
            result["message"] += "\n" + str(result["other_tasks_warning"])
        return result

    async def pause(self, pack_id: str) -> dict[str, Any]:
        pack_id = self._validate_pack_id(pack_id)
        if self._task_is_alive(self._index_tasks.get(pack_id)):
            raise RuntimeError("正在建立向量索引；这个收尾阶段不能暂停，请等待完成")
        async with self._lock(pack_id):
            state = self._load_state(pack_id)
            active_task = self._tasks.get(pack_id)
            task_is_running = self._task_is_alive(active_task)
            if state.get("task_status") == "paused" and not task_is_running:
                self._reset_running_items(pack_id)
                state = self._load_state(pack_id)
                if state.get("active_items") or state.get("current"):
                    state["active_items"] = []
                    state["current"] = ""
                    state["updated_at"] = utc_now()
                    self._save_state(pack_id, state)
                result = self.status(pack_id)
                result["message"] = str(result.get("status_message") or "任务已经暂停")
                return result
            if state.get("task_status") not in {"running", "paused"}:
                raise RuntimeError("当前没有正在运行的语义化任务")
            if state.get("task_phase") == "indexing":
                raise RuntimeError(
                    "图片描述队列已经处理完，正在建立向量索引；这个收尾阶段不能暂停，请等待完成"
                )
            # 先关掉领取闸门并持久化暂停，再取消整个描述任务。
            # 不再等待已发出的模型请求“自然收尾”，否则上游卡住时
            # 页面会永久停在暂停中。
            self._pause_events.setdefault(pack_id, asyncio.Event()).clear()
            state["task_status"] = "paused"
            state["paused_at"] = str(state.get("paused_at") or utc_now())
            state["updated_at"] = utc_now()
            self._save_state(pack_id, state)
            interrupted_requests = len(state.get("active_items") or [])
            if task_is_running and active_task is not None:
                active_task.cancel()
                await asyncio.gather(active_task, return_exceptions=True)
                if self._tasks.get(pack_id) is active_task:
                    self._tasks.pop(pack_id, None)
            _, recovered = self._reset_running_items(pack_id)
            state = self._load_state(pack_id)
            state.update(
                {
                    "task_status": "paused",
                    "task_phase": "captioning",
                    "active_items": [],
                    "current": "",
                    "interrupted_requests": max(interrupted_requests, recovered),
                    "updated_at": utc_now(),
                }
            )
            self._save_state(pack_id, state)
        result = self.status(pack_id)
        interrupted = int(state.get("interrupted_requests", 0) or 0)
        result["message"] = (
            f"队列已完全暂停；已中断 {interrupted} 个模型请求并退回等待队列。"
            if interrupted
            else str(result.get("status_message") or "任务已暂停")
        )
        return result

    async def resume(
        self, pack_id: str, *, concurrency: int | None = None
    ) -> dict[str, Any]:
        pack_id = self._validate_pack_id(pack_id)
        async with self._lock(pack_id):
            state = self._load_state(pack_id)
            task = self._tasks.get(pack_id)
            task_is_running = bool(task and not task.done())
            mode = str(state.get("mode") or "full")
            if state.get("task_status") in {
                "running",
                "paused",
                "failed",
                "completed_with_errors",
            }:
                if state.get("queue_cleared"):
                    raise RuntimeError(
                        "当前任务队列已经清空，请点击开始语义化重新扫描图片"
                    )
                if concurrency is not None:
                    try:
                        requested_concurrency = max(1, min(16, int(concurrency)))
                    except (TypeError, ValueError) as exc:
                        raise ValueError("并发数必须是 1 到 16 的整数") from exc
                    try:
                        current_concurrency = max(
                            1,
                            min(
                                16,
                                int(
                                    state.get("concurrency")
                                    or self._configured_concurrency()
                                ),
                            ),
                        )
                    except (TypeError, ValueError):
                        current_concurrency = self._configured_concurrency()
                    if task_is_running and requested_concurrency != current_concurrency:
                        raise RuntimeError(
                            f"当前队列实际并发数是 {current_concurrency}，暂停期间不能修改；"
                            "如需调整，请清空当前队列后重新开始"
                        )
                    state["concurrency"] = requested_concurrency
                if mode in {"full", "retry_failed", "index_only"}:
                    embedding = self._require_embedding_provider(
                        pack_id, "继续完整语义化"
                    )
                    selection = await self._verify_embedding_dimension(
                        pack_id, embedding
                    )
                    state.update(
                        {
                            "embedding_provider_id": embedding.provider_id,
                            "embedding_selection_mode": str(
                                selection.get("selection_mode") or ""
                            ),
                            "embedding_dimension": embedding.dimension,
                        }
                    )
                if not task_is_running:
                    metadata, _ = self._reset_running_items(pack_id)
                    state["active_items"] = []
                    state["current"] = ""
                    state["task_phase"] = (
                        "captioning"
                        if any(
                            isinstance(item, dict)
                            and not semantic_caption_is_complete(item)
                            for item in metadata.get("images", {}).values()
                        )
                        else "indexing"
                    )
                if state.get("task_status") == "paused" and state.get("paused_at"):
                    try:
                        previous_paused_seconds = max(
                            0, int(state.get("paused_seconds", 0) or 0)
                        )
                    except (TypeError, ValueError):
                        previous_paused_seconds = 0
                    state["paused_seconds"] = (
                        previous_paused_seconds
                        + self._elapsed_seconds(state.get("paused_at"))
                    )
                state["task_status"] = "running"
                state["paused_at"] = ""
                state["updated_at"] = utc_now()
                self._save_state(pack_id, state)
                event = self._pause_events.setdefault(pack_id, asyncio.Event())
                event.set()
                if not task_is_running:
                    self._tasks[pack_id] = asyncio.create_task(
                        self._run(pack_id, mode=mode, force=False)
                    )
        result = self.status(pack_id)
        if result.get("task_status") == "running":
            result["message"] = (
                f"队列已继续：并发上限 {result.get('concurrency', 1)}，"
                f"待生成描述 {result.get('queued_caption_tasks', 0)} 张，"
                f"待建立向量 {result.get('queued_embedding_tasks', 0)} 张。"
            )
            if result.get("other_tasks_warning"):
                result["message"] += "\n" + str(result["other_tasks_warning"])
        return result

    async def retry(self, pack_id: str) -> dict[str, Any]:
        pack_id = self._validate_pack_id(pack_id)
        return await self.start(pack_id, mode="retry_failed")
