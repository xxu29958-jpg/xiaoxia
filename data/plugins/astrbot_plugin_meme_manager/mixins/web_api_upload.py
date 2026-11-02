import asyncio
import inspect
import time
from pathlib import Path

from quart import jsonify, request

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.semantic.storage import (
    invalidate_semantic_metadata,
)
from .web_api_support import (
    MAX_PACK_UPLOAD_REQUEST_BYTES,
    PACK_IMPORT_SESSION_TTL_SECONDS,
)


class WebAPIMixinUpload:
    """Upload; shared runtime resources belong to the facade."""

    @staticmethod
    def _prepare_archive_upload_request() -> None:
        """覆盖 Quart 兼容层默认的 16 MB 请求上限。"""
        try:
            request.max_content_length = MAX_PACK_UPLOAD_REQUEST_BYTES
        except (AttributeError, RuntimeError):
            # 新版 AstrBot 使用 Starlette 上传对象，不需要在这里调整限制。
            pass

    @staticmethod
    async def _save_uploaded_file(uploaded_file, destination: Path) -> None:
        """同时兼容 Quart 的异步 save 与旧版同步 save。"""
        save_method = uploaded_file.save
        if inspect.iscoroutinefunction(save_method):
            await save_method(str(destination))
            return
        result = await asyncio.to_thread(save_method, str(destination))
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _pack_import_session_paths(token: str) -> tuple[Path, Path]:
        normalized = str(token or "").strip().lower()
        if len(normalized) != 32 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("导入凭证无效，请重新选择压缩包")
        session_dir = pack_paths.TEMP_DIR / "pack_import_sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / f"{normalized}.zip", session_dir / f"{normalized}.json"

    @staticmethod
    def _cleanup_pack_import_sessions() -> None:
        session_dir = pack_paths.TEMP_DIR / "pack_import_sessions"
        if not session_dir.is_dir():
            return
        expire_before = time.time() - PACK_IMPORT_SESSION_TTL_SECONDS
        for path in session_dir.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < expire_before:
                    path.unlink()
            except OSError:
                continue

    def _guard_default_pack_file_operation(self, operation: str):
        pack_id = str(self._resolve_runtime_pack_context().get("pack_id") or "").strip()
        try:
            if pack_id:
                self._semantic_operation_guard(pack_id, operation)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        return None

    async def _run_default_pack_mutation(self, operation: str, mutation):
        """让分类/移动操作与同图包语义任务共享同一把锁。"""
        pack_id = str(self._resolve_runtime_pack_context().get("pack_id") or "").strip()
        manager = getattr(self, "semantic_task_manager", None)
        if manager is None or not pack_id:
            return mutation()

        def guarded_mutation():
            current_pack_id = str(
                self._resolve_runtime_pack_context().get("pack_id") or ""
            ).strip()
            if current_pack_id != pack_id:
                raise RuntimeError("默认资源包已切换，请重新执行当前操作")
            return mutation()

        return await manager.run_locked_pack_mutation(
            pack_id, operation, guarded_mutation
        )

    def _invalidate_default_pack_semantics(self) -> None:
        pack_dir = Path(self._resolve_runtime_pack_context()["pack_dir"]).resolve()
        if not (pack_dir / "semantic_metadata.json").is_file():
            return
        try:
            invalidate_semantic_metadata(pack_dir)
        except Exception as exc:
            logger.error("图片变更后刷新语义元数据失败: %s", exc, exc_info=True)

    def _finish_img_host_local_operation(self) -> None:
        active = getattr(self, "_img_host_local_operation", None)
        if not isinstance(active, dict):
            return
        pack_id = str(active.get("pack_id") or "").strip()
        manager = getattr(self, "semantic_task_manager", None)
        if manager is not None and pack_id:
            pack_dir = pack_paths.PACKS_DIR / pack_id
            if (pack_dir / "semantic_metadata.json").is_file():
                try:
                    invalidate_semantic_metadata(pack_dir)
                except Exception as exc:
                    logger.error(
                        "图床任务结束后刷新语义元数据失败: %s",
                        exc,
                        exc_info=True,
                    )
            manager.end_external_pack_operation(pack_id)
        self._img_host_local_operation = None
