from __future__ import annotations

import asyncio
from typing import Any

from .index import (
    build_index,
)
from .models import (
    semantic_caption_is_complete,
    utc_now,
)
from .storage import (
    load_metadata,
)


class SemanticTaskManagerIndex:
    """Index; shared runtime resources belong to the facade."""

    async def rebuild_index(
        self, pack_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        pack_id = self._validate_pack_id(pack_id)
        pack_dir = self._pack_dir(pack_id)
        if not pack_dir.is_dir():
            raise FileNotFoundError(f"表情包 {pack_id} 不存在")
        current_task = asyncio.current_task()
        async with self._lock(pack_id):
            active_task = self._tasks.get(pack_id)
            if active_task and not active_task.done():
                raise RuntimeError("语义化任务尚未结束，请等待任务完成后再重建索引")
            active_index_task = self._index_tasks.get(pack_id)
            if (
                active_index_task
                and active_index_task is not current_task
                and not active_index_task.done()
            ):
                raise RuntimeError("当前资源包已经在建立向量索引")
            external_operation = self._external_pack_operations.get(pack_id)
            if external_operation:
                raise RuntimeError(
                    f"资源包正在执行“{external_operation}”，暂时不能建立向量索引"
                )
            provider = self._require_embedding_provider(pack_id, "建立向量索引")
            metadata = load_metadata(pack_dir)
            images = metadata.get("images", {})
            if not isinstance(images, dict) or not images:
                raise RuntimeError("还没有可建立索引的图片描述")
            incomplete = sum(
                1
                for item in images.values()
                if (
                    not isinstance(item, dict) or not semantic_caption_is_complete(item)
                )
            )
            if incomplete:
                raise RuntimeError(
                    f"还有 {incomplete} 个图片描述未完成，请先完成全部描述后再建立向量索引"
                )
            if current_task is None:
                raise RuntimeError("无法登记当前索引任务")
            self._index_tasks[pack_id] = current_task
            index_state = self._load_state(pack_id)
            index_state.update(
                {
                    "task_status": "running",
                    "task_phase": "indexing",
                    "mode": "index_only",
                    "current": "正在建立向量索引",
                    "active_items": [],
                    "started_at": utc_now(),
                    "finished_at": "",
                    "paused_at": "",
                    "paused_seconds": 0,
                    "last_error": None,
                    "updated_at": utc_now(),
                }
            )
            self._save_state(pack_id, index_state)
            try:
                await self._verify_embedding_dimension(pack_id, provider, force=force)
                result = await build_index(
                    pack_dir, self.plugin_data_dir, pack_id, provider, force=force
                )
                latest = load_metadata(pack_dir)
                has_errors = any(
                    isinstance(item, dict) and item.get("embedding_status") == "failed"
                    for item in latest.get("images", {}).values()
                )
                final_state = self._load_state(pack_id)
                final_state.update(
                    {
                        "task_status": (
                            "completed_with_errors" if has_errors else "completed"
                        ),
                        "task_phase": "finished",
                        "current": "",
                        "active_items": [],
                        "finished_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
                self._save_state(pack_id, final_state)
                return result
            except asyncio.CancelledError:
                cancelled_state = self._load_state(pack_id)
                cancelled_state.update(
                    {
                        "task_status": "failed",
                        "task_phase": "failed",
                        "current": "",
                        "active_items": [],
                        "finished_at": utc_now(),
                        "last_error": "向量索引任务已取消",
                        "updated_at": utc_now(),
                    }
                )
                self._save_state(pack_id, cancelled_state)
                raise
            except Exception as exc:
                failed_state = self._load_state(pack_id)
                failed_state.update(
                    {
                        "task_status": "failed",
                        "task_phase": "failed",
                        "current": "",
                        "active_items": [],
                        "finished_at": utc_now(),
                        "last_error": self._safe_error(exc, pack_id),
                        "updated_at": utc_now(),
                    }
                )
                self._save_state(pack_id, failed_state)
                raise
            finally:
                if self._index_tasks.get(pack_id) is current_task:
                    self._index_tasks.pop(pack_id, None)
