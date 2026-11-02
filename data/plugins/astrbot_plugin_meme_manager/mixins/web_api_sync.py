import asyncio
import json
import time
from pathlib import Path

from quart import jsonify, make_response, request

from astrbot.api import logger

from .web_api_support import (
    IMG_HOST_STATUS_CACHE_TTL_SECONDS,
)


class WebAPIMixinSync:
    """Sync; shared runtime resources belong to the facade."""

    async def _api_sync_status(self):
        try:
            missing_in_config, deleted_categories = await asyncio.to_thread(
                self.category_manager.get_sync_status
            )
            return jsonify(
                {
                    "status": "ok",
                    "missing_in_config": missing_in_config,
                    "deleted_categories": deleted_categories,
                    "differences": {
                        "missing_in_config": missing_in_config,
                        "deleted_categories": deleted_categories,
                    },
                }
            )
        except Exception as e:
            logger.error(f"获取同步状态失败: {e}")
            return jsonify({"error": "获取同步状态失败"}), 500

    async def _api_sync_config(self):
        try:
            logger.info("开始同步配置...")
            synced = await self._run_default_pack_mutation(
                "同步表情分类配置",
                self.category_manager.sync_with_filesystem,
            )
            if synced:
                logger.info("配置同步成功")
                return jsonify({"message": "配置同步成功"}), 200
            logger.warning("配置同步失败")
            return jsonify({"message": "配置同步失败"}), 500
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except Exception as e:
            logger.error(f"配置同步失败: {e}")
            return jsonify({"message": f"配置同步失败: {str(e)}"}), 500

    def _get_provider_label(self) -> str:
        if self.img_sync_provider_type == "lsky":
            return "兰空图床（开源版 2.x）"
        if self.img_sync_provider_type == "cloudflare_r2":
            return "Cloudflare R2"
        if self.img_sync_provider_type == "stardots":
            return "StarDots"
        if self.img_sync_provider_type == "webdav":
            return "WebDAV"
        if self.img_sync and hasattr(self.img_sync, "provider"):
            return self.img_sync.provider.__class__.__name__
        return "未知图床"

    @staticmethod
    def _resolve_requested_sync_pack_id(payload: dict | None = None) -> str:
        managed_pack_id = str(request.args.get("managed_pack_id") or "").strip()
        if managed_pack_id:
            return managed_pack_id
        if isinstance(payload, dict):
            for key in ("managed_pack_id", "pack_id"):
                value = str(payload.get(key) or "").strip()
                if value:
                    return value
        return ""

    def _get_img_host_sync_task_status(self) -> dict:
        """Expose worker progress and release pack locks only after the worker exits.

        Returns:
            Current or last completed synchronization status.
        """
        if not self.img_sync:
            self._finish_img_host_local_operation()
            return {
                "available": False,
                "running": False,
                "completed": True,
                "success": False,
                "message": "图床服务未配置",
            }
        status = self.img_sync.get_task_status()
        status["managed_pack_id"] = getattr(self, "_img_sync_pack_id", "")
        if not status["running"]:
            previous = self._last_img_host_sync_task_status or {}
            if status.get("task_id") and (
                previous.get("task_id") != status["task_id"] or previous.get("running")
            ):
                self._invalidate_img_host_status_cache()
            self._finish_img_host_local_operation()
            self._last_img_host_sync_task_status = dict(status)
        return status

    def _ensure_img_host_status_cache(self) -> dict[str, dict]:
        cache = getattr(self, "_img_host_sync_status_cache", None)
        if isinstance(cache, dict):
            return cache
        cache = {}
        self._img_host_sync_status_cache = cache
        return cache

    def _invalidate_img_host_status_cache(self, pack_id: str | None = None) -> None:
        cache = self._ensure_img_host_status_cache()
        if not pack_id:
            cache.clear()
            return
        target_pack_id = str(pack_id).strip()
        keys_to_remove = [key for key in cache if key.startswith(f"{target_pack_id}::")]
        for key in keys_to_remove:
            cache.pop(key, None)

    def _get_img_host_status_cache_ttl(self) -> int:
        raw_value = self._read_config_value(
            ("sync", "status_cache_ttl_seconds"),
            default=IMG_HOST_STATUS_CACHE_TTL_SECONDS,
        )
        try:
            ttl = int(raw_value)
        except (TypeError, ValueError):
            return IMG_HOST_STATUS_CACHE_TTL_SECONDS
        return max(0, min(ttl, 300))

    @staticmethod
    def _make_img_host_status_cache_key(pack_id: str, local_dir: Path | str) -> str:
        normalized_pack_id = str(pack_id or "").strip() or "__default__"
        normalized_local_dir = str(local_dir or "").replace("\\", "/").rstrip("/")
        return f"{normalized_pack_id}::{normalized_local_dir}"

    def _start_img_host_sync_task(self, task: str, pack_id: str | None = None) -> dict:
        sync_client = self._ensure_img_sync_for_pack(pack_id)
        if not sync_client:
            raise RuntimeError("图床服务未配置")

        status = self._get_img_host_sync_task_status()
        if not status.get("available", False):
            raise RuntimeError(status.get("message") or "图床服务未配置")
        if status.get("running"):
            raise RuntimeError("已有同步任务正在运行，请等待当前任务完成")

        self._invalidate_img_host_status_cache(pack_id)
        self._last_img_host_sync_task_status = None
        changes_local_files = task in {"overwrite_from_remote", "download"}
        effective_pack_id = str(
            pack_id
            or getattr(self, "_img_sync_pack_id", "")
            or self._resolve_runtime_pack_context().get("pack_id")
        ).strip()
        manager = getattr(self, "semantic_task_manager", None)
        if changes_local_files and manager is not None and effective_pack_id:
            operation_name = (
                "从远端覆盖本地表情包"
                if task == "overwrite_from_remote"
                else "从图床下载表情包"
            )
            manager.begin_external_pack_operation(effective_pack_id, operation_name)
            self._img_host_local_operation = {
                "pack_id": effective_pack_id,
                "operation": operation_name,
            }
        try:
            sync_client.sync_process = sync_client._start_sync_process(task)
            # 即使浏览器断开，也要回收并释放表情包操作。
            monitor = asyncio.create_task(
                asyncio.to_thread(sync_client.sync_process.join)
            )
            self._img_host_sync_monitor = monitor
            monitor.add_done_callback(
                lambda completed: (
                    None
                    if completed.cancelled() or completed.exception()
                    else self._get_img_host_sync_task_status()
                )
            )
        except Exception:
            if changes_local_files:
                self._finish_img_host_local_operation()
            raise
        return self._get_img_host_sync_task_status()

    async def _api_img_host_sync_status(self):
        try:
            pack_id = self._resolve_requested_sync_pack_id()
            sync_client = self._ensure_img_sync_for_pack(pack_id)
            if not sync_client:
                return jsonify({"error": "图床服务未配置"}), 400

            task_status = self._get_img_host_sync_task_status()
            cache_ttl = self._get_img_host_status_cache_ttl()
            cache_key = self._make_img_host_status_cache_key(
                pack_id, getattr(sync_client, "local_dir", "")
            )
            cache_store = self._ensure_img_host_status_cache()
            now = time.monotonic()
            if not task_status.get("running") and cache_ttl > 0:
                cached_entry = cache_store.get(cache_key)
                if (
                    cached_entry
                    and (now - cached_entry.get("created_at", 0.0)) < cache_ttl
                ):
                    cached_payload = dict(cached_entry.get("payload") or {})
                    cached_payload["status_cache_hit"] = True
                    cached_payload["status_cache_ttl"] = cache_ttl
                    return jsonify(cached_payload)

            status = await asyncio.to_thread(sync_client.check_status)
            status["upload_count"] = len(status.get("to_upload", []))
            status["download_count"] = len(status.get("to_download", []))
            status["remote_extra_count"] = len(status.get("to_delete_remote", []))
            status["local_extra_count"] = len(status.get("to_delete_local", []))
            status["provider_label"] = self._get_provider_label()
            status["status_cache_hit"] = False
            status["status_cache_ttl"] = cache_ttl
            if pack_id:
                status["managed_pack_id"] = pack_id

            if not task_status.get("running") and cache_ttl > 0:
                cache_store[cache_key] = {
                    "created_at": now,
                    "payload": dict(status),
                }
            return jsonify(status)
        except Exception as e:
            error_text = str(e)
            lower_error_text = error_text.lower()
            is_rate_limited = any(
                keyword in lower_error_text
                for keyword in (
                    "exceed times limit",
                    "rate limit",
                    "too many requests",
                    "调用频次",
                    "调用次数",
                    "请求频率",
                )
            )
            if is_rate_limited:
                return (
                    jsonify(
                        {
                            "error": "图床接口触发频率限制，请稍后再试",
                            "details": error_text,
                        }
                    ),
                    429,
                )
            return jsonify({"error": error_text}), 500

    async def _api_img_host_sync_upload(self):
        try:
            payload = await request.get_json(silent=True)
            pack_id = self._resolve_requested_sync_pack_id(payload)
            if not self._ensure_img_sync_for_pack(pack_id):
                return jsonify({"message": "图床服务未配置"}), 400
            task_status = self._start_img_host_sync_task("upload", pack_id=pack_id)
            return jsonify({"success": True, "task": task_status})
        except Exception as e:
            status_code = (
                409
                if any(
                    marker in str(e)
                    for marker in ("已有同步任务", "语义任务", "语义队列")
                )
                else 500
            )
            return jsonify({"message": str(e)}), status_code

    async def _api_img_host_sync_overwrite_to_remote(self):
        try:
            payload = await request.get_json(silent=True)
            pack_id = self._resolve_requested_sync_pack_id(payload)
            if not self._ensure_img_sync_for_pack(pack_id):
                return jsonify({"message": "图床服务未配置"}), 400
            task_status = self._start_img_host_sync_task(
                "overwrite_to_remote", pack_id=pack_id
            )
            return jsonify({"success": True, "task": task_status})
        except Exception as e:
            status_code = (
                409
                if any(
                    marker in str(e)
                    for marker in ("已有同步任务", "语义任务", "语义队列")
                )
                else 500
            )
            return jsonify({"message": str(e)}), status_code

    async def _api_img_host_sync_overwrite_from_remote(self):
        try:
            payload = await request.get_json(silent=True)
            pack_id = self._resolve_requested_sync_pack_id(payload)
            if not self._ensure_img_sync_for_pack(pack_id):
                return jsonify({"message": "图床服务未配置"}), 400
            task_status = self._start_img_host_sync_task(
                "overwrite_from_remote", pack_id=pack_id
            )
            return jsonify({"success": True, "task": task_status})
        except Exception as e:
            status_code = (
                409
                if any(
                    marker in str(e)
                    for marker in ("已有同步任务", "语义任务", "语义队列")
                )
                else 500
            )
            return jsonify({"message": str(e)}), status_code

    async def _api_img_host_sync_download(self):
        try:
            payload = await request.get_json(silent=True)
            pack_id = self._resolve_requested_sync_pack_id(payload)
            if not self._ensure_img_sync_for_pack(pack_id):
                return jsonify({"message": "图床服务未配置"}), 400
            task_status = self._start_img_host_sync_task("download", pack_id=pack_id)
            return jsonify({"success": True, "task": task_status})
        except Exception as e:
            status_code = (
                409
                if any(
                    marker in str(e)
                    for marker in ("已有同步任务", "语义任务", "语义队列")
                )
                else 500
            )
            return jsonify({"message": str(e)}), status_code

    async def _api_img_host_sync_task_status(self):
        return jsonify(self._get_img_host_sync_task_status())

    async def _api_img_host_sync_cancel(self):
        """Cancel the active image transfer without blocking the event loop."""
        payload = await request.get_json(silent=True)
        pack_id = self._resolve_requested_sync_pack_id(payload)
        if pack_id and pack_id != getattr(self, "_img_sync_pack_id", ""):
            return jsonify({"message": "当前同步任务属于其他表情包"}), 409
        if self.img_sync:
            await asyncio.to_thread(self.img_sync.stop_sync)
        return jsonify(self._get_img_host_sync_task_status())

    async def _api_img_host_sync_progress(self):
        async def generate():
            while True:
                status = self._get_img_host_sync_task_status()
                yield f"data: {json.dumps(status)}\n\n"
                if status.get("completed"):
                    return
                if status.get("running"):
                    await asyncio.sleep(1)
                else:
                    return

        response = await make_response(
            generate(),
            {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        response.timeout = None
        return response
