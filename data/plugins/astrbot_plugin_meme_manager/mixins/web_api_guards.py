import asyncio
import re

from quart import request

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.semantic.index import EmbeddingAdapter


class WebAPIMixinGuards:
    """Guards; shared runtime resources belong to the facade."""

    @staticmethod
    def _get_webui_response_status(response) -> int | str:
        if isinstance(response, tuple) and len(response) > 1:
            return response[1]
        return getattr(response, "status_code", "unknown")

    async def _semantic_request_pack_id(self, data: dict | None = None) -> str:
        payload = data or {}
        pack_id = str(
            payload.get("pack_id")
            or request.args.get("pack_id")
            or request.args.get("managed_pack_id")
            or ""
        ).strip()
        if not pack_id:
            pack_id = str(
                getattr(self, "_resolve_runtime_pack_context", lambda: {})().get(
                    "pack_id"
                )
                or ""
            )
        if not pack_id:
            raise ValueError("pack_id 不能为空")
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", pack_id):
            raise ValueError("pack_id 无效")
        manager = getattr(self, "semantic_task_manager", None)
        packs_root = (
            manager.plugin_data_dir / "packs"
            if manager is not None
            else pack_paths.PACKS_DIR
        )
        pack_dir = (packs_root / pack_id).resolve()
        try:
            pack_dir.relative_to(packs_root.resolve())
        except ValueError as exc:
            raise ValueError("pack_id 无效") from exc
        if not pack_dir.is_dir():
            raise FileNotFoundError(f"表情包 {pack_id} 不存在")
        return pack_id

    def _semantic_operation_guard(self, pack_id: str, operation: str) -> None:
        if getattr(self, "_img_host_local_operation", None):
            self._get_img_host_sync_task_status()
        manager = getattr(self, "semantic_task_manager", None)
        if manager is not None:
            manager.assert_pack_mutation_allowed(pack_id, operation)

    def _semantic_rebuild_guidance(self, pack_id: str) -> dict:
        """返回切换或导入资源包后是否需要补建本机向量。"""
        guidance = {
            "semantic_rebuild_required": False,
            "semantic_rebuild_pack_id": str(pack_id or "").strip(),
        }
        if not guidance["semantic_rebuild_pack_id"] or not bool(
            getattr(self, "semantic_enabled", False)
        ):
            return guidance
        manager = getattr(self, "semantic_task_manager", None)
        if manager is None:
            return guidance
        try:
            status = manager.status(guidance["semantic_rebuild_pack_id"])
        except Exception as exc:
            logger.warning(
                "读取资源包向量重建提示失败: %s | pack_id=%s",
                exc,
                guidance["semantic_rebuild_pack_id"],
            )
            return guidance
        task_status = str(status.get("task_status") or "")
        guidance.update(
            {
                "semantic_rebuild_required": bool(
                    status.get("dimension_rebuild_required")
                    and status.get("semantic_caption_complete")
                    and task_status not in {"running", "paused"}
                ),
                "semantic_task_status": task_status,
                "semantic_caption_complete": bool(
                    status.get("semantic_caption_complete")
                ),
                "semantic_index_ready": bool(status.get("index_ready")),
                "semantic_embedding_provider_id": str(
                    status.get("embedding_provider_id") or ""
                ),
                "semantic_embedding_model": str(status.get("embedding_model") or ""),
                "semantic_embedding_dimension": int(
                    status.get("embedding_configured_dimension", 0) or 0
                ),
            }
        )
        return guidance

    def _pack_import_embedding_signature(self) -> dict:
        """返回当前本地嵌入模型签名，用于安全恢复备份。"""
        try:
            provider = self._resolve_embedding_provider()
            embedding = EmbeddingAdapter(
                provider, str(getattr(self, "semantic_embedding_provider_id", "") or "")
            )
        except Exception:
            return {
                "embedding_provider_id": "",
                "embedding_model": "",
                "embedding_dimension": 0,
            }
        if not embedding.ready:
            return {
                "embedding_provider_id": "",
                "embedding_model": "",
                "embedding_dimension": 0,
            }
        return {
            "embedding_provider_id": embedding.provider_id,
            "embedding_model": embedding.model_name,
            "embedding_dimension": embedding.dimension,
        }

    async def _run_guarded_pack_file_operation(
        self,
        pack_id: str,
        operation: str,
        function,
        *args,
        **kwargs,
    ):
        """在整个文件快照期间阻止同一表情包启动语义任务。"""
        manager = getattr(self, "semantic_task_manager", None)
        locked = False
        if manager is not None and pack_id:
            manager.begin_external_pack_operation(pack_id, operation)
            locked = True
        kwargs["operation_guard"] = None if locked else self._semantic_operation_guard
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # 浏览器断开或请求超时时，to_thread 中的磁盘操作不会自动停止。
            # 必须等它真正收尾后再释放外部操作锁，否则语义任务可能与仍在
            # 运行的导出/覆盖线程同时改动同一个表情包。
            try:
                await asyncio.shield(worker)
            except Exception:
                pass
            raise
        finally:
            if locked:
                manager.end_external_pack_operation(pack_id)

    async def _run_guarded_runtime_file_operation(
        self,
        operation: str,
        function,
        *args,
        **kwargs,
    ):
        """在运行时全局文件操作期间持有所有已安装表情包的锁。"""
        if not hasattr(self, "_runtime_file_lock"):
            self._runtime_file_lock = asyncio.Lock()
        async with self._runtime_file_lock:
            manager = getattr(self, "semantic_task_manager", None)
            pack_ids = (
                sorted(
                    path.name
                    for path in pack_paths.PACKS_DIR.iterdir()
                    if path.is_dir()
                )
                if pack_paths.PACKS_DIR.is_dir()
                else []
            )
            locked_pack_ids = []
            if manager is not None:
                try:
                    for pack_id in pack_ids:
                        manager.begin_external_pack_operation(pack_id, operation)
                        locked_pack_ids.append(pack_id)
                except Exception:
                    for pack_id in reversed(locked_pack_ids):
                        manager.end_external_pack_operation(pack_id)
                    raise

            kwargs["operation_guard"] = (
                None if manager is not None else self._semantic_operation_guard
            )
            worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(worker)
                except Exception:
                    pass
                raise
            finally:
                for pack_id in reversed(locked_pack_ids):
                    manager.end_external_pack_operation(pack_id)
