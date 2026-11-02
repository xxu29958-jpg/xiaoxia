from __future__ import annotations

from typing import Any

from .index import (
    index_is_ready,
    load_index_manifest,
)
from .models import (
    semantic_caption_is_complete,
    utc_now,
)
from .storage import (
    load_metadata,
)


class SemanticTaskManagerStatus:
    """Status; shared runtime resources belong to the facade."""

    def status(self, pack_id: str) -> dict[str, Any]:
        pack_id = self._validate_pack_id(pack_id)
        pack_dir = self._pack_dir(pack_id)
        state = self._load_state(pack_id)
        data = load_metadata(pack_dir, scan_legacy=False)
        metadata_read_only = bool(data.get("metadata_read_only"))
        metadata_migration_required = bool(data.get("metadata_migration_required"))
        queue_cleared = bool(state.get("queue_cleared"))
        task_status = str(state.get("task_status") or "idle")
        worker_alive = self._semantic_operation_is_alive(pack_id)
        if (
            not worker_alive
            and task_status in {"running", "paused"}
            and not metadata_read_only
            and not metadata_migration_required
        ):
            # 进程重启或硬暂停后，旧请求已不可能再返回。把磁盘上
            # 的 running 记录恢复成 pending，避免记录列表永久显示“进行中”。
            data, recovered = self._reset_running_items(pack_id, data)
            if recovered or state.get("active_items") or state.get("current"):
                state["active_items"] = []
                state["current"] = ""
                state["updated_at"] = utc_now()
                self._save_state(pack_id, state)
        images = data.get("images", {})
        caption_done = sum(
            1 for item in images.values() if semantic_caption_is_complete(item)
        )
        caption_failed = sum(
            1
            for item in images.values()
            if isinstance(item, dict) and item.get("caption_status") == "failed"
        )
        embedding_done = sum(
            1
            for item in images.values()
            if isinstance(item, dict) and item.get("embedding_status") == "done"
        )
        embedding_failed = sum(
            1
            for item in images.values()
            if isinstance(item, dict) and item.get("embedding_status") == "failed"
        )
        pending = sum(1 for item in images.values() if self._item_is_queued(item))
        waiting = sum(
            1
            for item in images.values()
            if isinstance(item, dict)
            and (
                not semantic_caption_is_complete(item)
                or item.get("embedding_status") == "pending"
            )
        )
        embedding = self._embedding_adapter(pack_id)
        selection = self._load_selection(pack_id)
        manifest = load_index_manifest(self.plugin_data_dir, pack_id)
        vision = self._vision_provider_details()
        total_items = len(images)
        running = sum(
            1
            for item in images.values()
            if isinstance(item, dict)
            and (
                item.get("caption_status") == "running"
                or item.get("embedding_status") == "running"
            )
        )
        completed = sum(
            1
            for item in images.values()
            if isinstance(item, dict)
            and semantic_caption_is_complete(item)
            and item.get("embedding_status") == "done"
        )
        failed = sum(
            1
            for item in images.values()
            if isinstance(item, dict)
            and (
                item.get("caption_status") == "failed"
                or item.get("embedding_status") == "failed"
            )
        )
        token_usage = self._normalize_token_usage(state.get("token_usage"))
        active_items = state.get("active_items")
        if not isinstance(active_items, list):
            active_items = []
        active_items = [
            str(value) for value in active_items if str(value or "").strip()
        ]
        task_phase = str(state.get("task_phase") or "")
        external_operation = self._external_pack_operations.get(pack_id, "")
        other_active_tasks = self.active_pack_tasks(exclude_pack_id=pack_id)
        other_task_parts = []
        for item in other_active_tasks:
            if item.get("task_phase") == "indexing":
                detail = "正在建立索引"
            else:
                detail = f"{item.get('concurrency', 1)} 并发"
            other_task_parts.append(f"{item.get('pack_id')}（{detail}）")
        other_tasks_warning = (
            "其他资源包也在运行语义任务："
            + "、".join(other_task_parts)
            + "。各资源包并发会叠加，请自行评估模型限流和调用消耗。"
            if other_task_parts
            else ""
        )
        queued_caption_tasks = sum(
            1
            for item in images.values()
            if isinstance(item, dict)
            and (
                not semantic_caption_is_complete(item)
                or (not worker_alive and item.get("caption_status") == "running")
            )
        )
        queued_embedding_tasks = sum(
            1
            for item in images.values()
            if isinstance(item, dict)
            and semantic_caption_is_complete(item)
            and (
                item.get("embedding_status") == "pending"
                or (not worker_alive and item.get("embedding_status") == "running")
            )
        )
        try:
            task_concurrency = max(
                1,
                min(
                    16,
                    int(state.get("concurrency") or self._configured_concurrency()),
                ),
            )
        except (TypeError, ValueError):
            task_concurrency = self._configured_concurrency()
        ready = bool(
            embedding.ready
            and index_is_ready(
                self.plugin_data_dir,
                pack_id,
                data,
                embedding.provider_id,
                embedding.model_name,
                embedding.dimension,
            )
        )
        caption_complete = bool(
            not queue_cleared
            and images
            and all(semantic_caption_is_complete(item) for item in images.values())
        )
        # active_items 是进程内请求快照；插件重载后旧值只能视为待恢复记录，
        # 不能再显示成“仍有模型请求执行中”。
        if not worker_alive:
            active_items = []
        active_request_count = len(active_items)
        queue_status, status_message = self._queue_status_message(
            active_request_count,
            data,
            external_operation,
            failed,
            metadata_migration_required,
            metadata_read_only,
            queue_cleared,
            queued_caption_tasks,
            queued_embedding_tasks,
            task_phase,
            task_status,
            total_items,
            worker_alive,
        )
        can_pause = bool(
            task_status == "running" and worker_alive and task_phase != "indexing"
        )
        can_resume = bool(
            task_status == "paused"
            or task_status in {"failed", "completed_with_errors"}
            or (task_status == "running" and not worker_alive)
        )
        can_start = bool(
            not metadata_read_only
            and not worker_alive
            and not external_operation
            and task_status != "paused"
        )
        can_retry = bool(
            not worker_alive
            and not metadata_read_only
            and not external_operation
            and task_status != "paused"
            and failed
        )
        can_rebuild_index = bool(
            not worker_alive
            and not metadata_read_only
            and not external_operation
            and task_status != "paused"
            and caption_complete
        )
        if task_status == "paused" and state.get("paused_at"):
            elapsed_until = state.get("paused_at")
        elif task_status not in {"running", "paused"}:
            elapsed_until = state.get("finished_at")
        else:
            elapsed_until = None
        try:
            paused_seconds = max(0, int(state.get("paused_seconds", 0) or 0))
        except (TypeError, ValueError):
            paused_seconds = 0
        elapsed_seconds = max(
            0,
            self._elapsed_seconds(state.get("started_at"), elapsed_until)
            - paused_seconds,
        )
        return {
            "pack_id": pack_id,
            "task_status": task_status,
            "task_mode": str(state.get("mode") or "full"),
            "started_at": str(state.get("started_at") or ""),
            "finished_at": str(state.get("finished_at") or ""),
            "elapsed_seconds": elapsed_seconds,
            "concurrency": task_concurrency,
            "task_phase": task_phase,
            "worker_alive": worker_alive,
            "external_operation": external_operation,
            "other_active_tasks": other_active_tasks,
            "other_tasks_warning": other_tasks_warning,
            "queue_status": queue_status,
            "status_message": status_message,
            "metadata_read_only": metadata_read_only,
            "metadata_error": str(data.get("metadata_error") or ""),
            "metadata_migration_required": metadata_migration_required,
            "migrated_from_schema_version": str(
                data.get("migrated_from_schema_version") or ""
            ),
            "can_pause": can_pause,
            "can_resume": can_resume,
            "can_start": can_start,
            "can_retry": can_retry,
            "can_rebuild_index": can_rebuild_index,
            "file_total": int(data.get("file_total", len(images))),
            "unique_total": int(data.get("unique_total", len(images))),
            "reused_duplicate_files": int(data.get("reused_duplicate_files", 0)),
            "caption_done": caption_done,
            "caption_failed": caption_failed,
            "embedding_done": embedding_done,
            "embedding_failed": embedding_failed,
            "pending": pending,
            "waiting_tasks": waiting,
            "total_tasks": total_items,
            "completed_tasks": completed,
            "running_tasks": running,
            "failed_tasks": failed,
            "queued_caption_tasks": queued_caption_tasks,
            "queued_embedding_tasks": queued_embedding_tasks,
            "active_request_count": active_request_count,
            "vision_calls": token_usage["calls"],
            "reclassified_items": int(state.get("reclassified_items", 0) or 0),
            "token_usage_input": token_usage["input"],
            "token_usage_output": token_usage["output"],
            "token_usage_total": token_usage["total"],
            "current": state.get("current", ""),
            "active_items": active_items,
            "vision_provider_id": str(vision["id"]),
            "vision_model": str(vision["model"]),
            "last_error": state.get("last_error"),
            "embedding_provider_id": embedding.provider_id,
            "embedding_configured_provider_id": str(
                self.config.get("embedding_provider_id") or ""
            ).strip(),
            "embedding_selection_mode": str(selection.get("selection_mode") or ""),
            "embedding_model": embedding.model_name,
            "embedding_configured_dimension": embedding.dimension,
            "embedding_verified_dimension": int(
                selection.get("verified_dimension", 0) or 0
            ),
            "embedding_dimension_verified": bool(selection.get("dimension_verified")),
            "embedding_dimension_error": str(selection.get("verification_error") or ""),
            "index_embedding_provider_id": str(
                manifest.get("embedding_provider_id") or ""
            ),
            "index_embedding_dimension": int(
                manifest.get("embedding_dimension", 0) or 0
            ),
            "index_ready": ready,
            "dimension_rebuild_required": bool(
                caption_complete and embedding.ready and not ready
            ),
            "semantic_caption_complete": caption_complete,
            "queue_cleared": queue_cleared,
            "error_items": [
                {"relative_path": item.get("relative_path"), "error": item.get("error")}
                for item in images.values()
                if isinstance(item, dict) and item.get("error")
            ][-20:],
            **self.capabilities(pack_id, embedding_provider=embedding.provider),
        }

    def _queue_status_message(
        self,
        active_request_count,
        data,
        external_operation,
        failed,
        metadata_migration_required,
        metadata_read_only,
        queue_cleared,
        queued_caption_tasks,
        queued_embedding_tasks,
        task_phase,
        task_status,
        total_items,
        worker_alive,
    ):
        """Queue status message."""
        if metadata_read_only:
            queue_status = "metadata_error"
            status_message = str(
                data.get("metadata_error") or "语义元数据无法安全读取，原文件已保持不变"
            )
        elif metadata_migration_required:
            queue_status = "migration_required"
            status_message = (
                "检测到旧版语义数据；开始任务后才会扫描图片、"
                "保留已有描述和人工内容，并备份升级文件。"
            )
        elif external_operation:
            queue_status = "external_operation"
            status_message = (
                f"资源包正在执行“{external_operation}”，完成前不能启动语义任务。"
            )
        elif queue_cleared:
            queue_status = "cleared"
            status_message = "当前任务队列已清空；已有图片描述仍然保留。"
        elif task_status == "paused":
            queue_status = "settling" if active_request_count else "paused"
            if active_request_count:
                status_message = (
                    f"已暂停领取新图片；{active_request_count} 个已经提交给视觉模型的请求"
                    "仍在收尾，结束后不会继续补位。"
                )
            else:
                status_message = (
                    f"队列已完全暂停，仍有 {queued_caption_tasks} 张图片等待生成描述；"
                    "点击“继续队列”即可恢复。"
                )
        elif task_status == "running" and not worker_alive:
            queue_status = "interrupted"
            status_message = "后台任务已经中断，但进度已保存；请点击“继续队列”。"
        elif task_status == "running" and task_phase == "indexing":
            queue_status = "indexing"
            status_message = (
                "图片描述队列已处理完，正在建立向量索引；此阶段请等待完成。"
            )
        elif task_status == "running":
            queue_status = "running"
            status_message = (
                f"正在生成图片描述：{active_request_count} 个模型请求执行中，"
                f"{queued_caption_tasks} 张排队等待。"
            )
        elif failed:
            queue_status = "failed"
            status_message = f"当前有 {failed} 张图片处理失败，可点击“重试失败项”。"
        elif queued_caption_tasks or queued_embedding_tasks:
            queue_status = "waiting"
            status_message = "存在尚未处理的图片或向量，请开始任务或继续已有队列。"
        elif total_items:
            queue_status = "done"
            status_message = "当前没有排队或执行中的图片任务。"
        else:
            queue_status = "empty"
            status_message = "尚无语义记录；点击开始任务后扫描图片并建立队列。"

        return queue_status, status_message
