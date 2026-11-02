from __future__ import annotations

import asyncio
import shutil
from typing import Any

from .models import (
    utc_now,
)
from .storage import (
    load_metadata,
    save_metadata,
)


class SemanticTaskManagerDelete:
    """Delete; shared runtime resources belong to the facade."""

    async def clear_local_semantic_state(self, pack_id: str) -> dict[str, Any]:
        """清理错误任务和本机向量，但保留可发布的图片描述。"""
        pack_id = self._validate_pack_id(pack_id)
        pack_dir = self._pack_dir(pack_id)
        if not pack_dir.is_dir():
            raise FileNotFoundError(f"表情包 {pack_id} 不存在")
        external_operation = self._external_pack_operations.get(pack_id)
        if external_operation:
            raise RuntimeError(
                f"资源包正在执行“{external_operation}”，暂时不能清空语义任务"
            )
        await self._cancel_index_task(pack_id)
        async with self._lock(pack_id):
            active_task = self._tasks.get(pack_id)
            if active_task and not active_task.done():
                active_task.cancel()
                await asyncio.gather(active_task, return_exceptions=True)
            self._tasks.pop(pack_id, None)
            self._index_tasks.pop(pack_id, None)
            self._pause_events.pop(pack_id, None)

            index_root = self.plugin_data_dir / "semantic_indexes" / pack_id
            for index_file in index_root.glob("index*.faiss"):
                index_file.unlink(missing_ok=True)
            (index_root / "index_manifest.json").unlink(missing_ok=True)

            metadata = load_metadata(pack_dir)
            kept_images = {}
            for digest, raw_item in metadata.get("images", {}).items():
                if not isinstance(raw_item, dict):
                    continue
                # 已完成的图片描述不是“待处理队列”，保留它们；其向量标记为 cleared，
                # 这样清理后不会再次显示为待处理，但以后点击开始仍会重新建立向量。
                if raw_item.get("caption_status") == "done":
                    item = dict(raw_item)
                    item["embedding_status"] = "cleared"
                    item["error"] = None
                    item["updated_at"] = utc_now()
                    kept_images[str(digest)] = item
            metadata["images"] = kept_images
            metadata["requires_local_index_rebuild"] = True
            save_metadata(pack_dir, metadata)
            self._save_state(
                pack_id,
                {
                    "task_status": "idle",
                    "queue_cleared": True,
                    "current": "",
                    "last_error": None,
                    "updated_at": utc_now(),
                },
            )
        return self.status(pack_id)

    async def delete_all_semantic_data(self, pack_id: str) -> dict[str, Any]:
        """删除当前资源包的全部语义结果和本机索引，但保留原始图片。"""
        pack_id = self._validate_pack_id(pack_id)
        pack_dir = self._pack_dir(pack_id)
        if not pack_dir.is_dir():
            raise FileNotFoundError(f"表情包 {pack_id} 不存在")
        external_operation = self._external_pack_operations.get(pack_id)
        if external_operation:
            raise RuntimeError(
                f"资源包正在执行“{external_operation}”，暂时不能删除语义数据"
            )
        await self._cancel_index_task(pack_id)
        async with self._lock(pack_id):
            active_task = self._tasks.get(pack_id)
            if active_task and not active_task.done():
                active_task.cancel()
                await asyncio.gather(active_task, return_exceptions=True)
            self._tasks.pop(pack_id, None)
            self._index_tasks.pop(pack_id, None)
            self._pause_events.pop(pack_id, None)

            shutil.rmtree(
                self.plugin_data_dir / "semantic_indexes" / pack_id,
                ignore_errors=True,
            )
            save_metadata(
                pack_dir,
                {
                    "pack_id": pack_id,
                    "generated_at": utc_now(),
                    "images": {},
                    "file_total": 0,
                    "unique_total": 0,
                    "reused_duplicate_files": 0,
                    "requires_local_index_rebuild": True,
                    "semantic_data_deleted": True,
                },
            )
            self._save_state(
                pack_id,
                {
                    "task_status": "idle",
                    "queue_cleared": True,
                    "semantic_data_deleted": True,
                    "current": "",
                    "active_items": [],
                    "last_error": None,
                    "deleted_at": utc_now(),
                    "updated_at": utc_now(),
                },
            )
        return self.status(pack_id)
