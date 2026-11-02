import asyncio
import hashlib
import mimetypes
from pathlib import Path

from quart import jsonify, request, send_file

from astrbot.api import logger

from .web_api_support import (
    MAX_ORIGINAL_IMAGE_BYTES,
    MAX_PREVIEW_IMAGE_BYTES,
    PREVIEW_IMAGE_MAX_DIMENSION,
)


class WebAPIMixinPreview:
    """Preview; shared runtime resources belong to the facade."""

    async def _api_serve_meme_image(self):
        category = request.args.get("category", "")
        filename = request.args.get("filename", "")
        view_context = self._resolve_webui_pack_view_context()
        memes_root = (
            view_context["memes_dir"].resolve()
            if view_context
            else Path(self._resolve_runtime_pack_context()["memes_dir"]).resolve()
        )
        file_path = (memes_root / category / filename).resolve()
        try:
            file_path.relative_to(memes_root)
        except ValueError:
            return jsonify({"status": "error", "message": "非法路径"}), 403
        if not file_path.is_file():
            return jsonify({"status": "error", "message": "文件不存在"}), 404
        return await send_file(str(file_path))

    @staticmethod
    def _preview_revision(file_path: Path) -> str:
        """Identify a file and the preview recipe without reading image bytes.

        Args:
            file_path: Resolved image path.

        Returns:
            An opaque revision based on file identity, metadata and recipe version.
        """
        stat = file_path.stat()
        identity = (
            f"{file_path}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}:"
            f"{PREVIEW_IMAGE_MAX_DIMENSION}:webp82-v1"
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    async def _api_preview_manifest(self):
        """Return current file revisions and the shared preview concurrency setting.

        Returns:
            An uncached, pack-scoped preview manifest.
        """
        context = self._resolve_webui_pack_view_context()
        if request.args.get("managed_pack_id") and context is None:
            return jsonify({"message": "Pack not found"}), 404
        context = context or self._resolve_runtime_pack_context()
        root = Path(context["memes_dir"]).resolve()

        def scan():
            # 目录遍历和文件状态查询不能阻塞事件循环。
            versions = {}
            for category, filenames in self._scan_pack_emojis(root).items():
                for filename in filenames:
                    path = (root / category / filename).resolve()
                    if not path.is_relative_to(root):
                        continue
                    try:
                        versions[f"{category}/{filename}"] = self._preview_revision(
                            path
                        )
                    except OSError:
                        continue
            return versions

        concurrency = self._read_config_value(
            ("webui", "preview_concurrency"), default=2
        )
        if type(concurrency) is not int or not 1 <= concurrency <= 8:
            concurrency = 2
        response = jsonify(
            {
                "pack_id": context["pack_id"],
                "concurrency": concurrency,
                "versions": await asyncio.to_thread(scan),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    async def _api_get_meme_image_data(self):
        category = request.args.get("category", "")
        filename = request.args.get("filename", "")
        size = request.args.get("size", "preview")
        if size not in {"preview", "original"}:
            return jsonify({"message": "Invalid preview size"}), 400
        view_context = self._resolve_webui_pack_view_context()
        if request.args.get("managed_pack_id") and view_context is None:
            return jsonify({"message": "Pack not found"}), 404
        memes_root = (
            view_context["memes_dir"].resolve()
            if view_context
            else Path(self._resolve_runtime_pack_context()["memes_dir"]).resolve()
        )
        file_path = (memes_root / category / filename).resolve()

        try:
            file_path.relative_to(memes_root)
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid path"}), 403

        if not file_path.exists() or not file_path.is_file():
            return jsonify({"status": "error", "message": "File not found"}), 404

        revision = self._preview_revision(file_path)
        requested_revision = request.args.get("v", "")
        if requested_revision and requested_revision != revision:
            return (
                jsonify({"message": "Image changed; reload the preview"}),
                409,
                {"Cache-Control": "no-store"},
            )
        cache_headers = {
            "Cache-Control": (
                "private, max-age=86400" if requested_revision else "private, no-cache"
            ),
            "Vary": "Authorization",
            "ETag": f'"{revision}-{size}"',
        }
        if request.if_none_match.contains(f"{revision}-{size}"):
            return "", 304, cache_headers

        max_bytes = (
            MAX_ORIGINAL_IMAGE_BYTES if size == "original" else MAX_PREVIEW_IMAGE_BYTES
        )
        file_size = file_path.stat().st_size
        if file_size > max_bytes:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Image is too large to preview in the plugin page",
                        "size": file_size,
                        "max_size": max_bytes,
                    }
                ),
                413,
            )

        mime_type = (
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )
        if size == "preview" and mime_type != "image/gif":
            try:
                data_url, mime_type = await asyncio.to_thread(
                    self._build_preview_data_url, file_path
                )
            except Exception as exc:
                logger.warning(f"生成预览缩略图失败，回退原图数据: {exc}")
                data_url = await asyncio.to_thread(
                    self._build_file_data_url, file_path, mime_type
                )
        else:
            data_url = await asyncio.to_thread(
                self._build_file_data_url, file_path, mime_type
            )

        # 读取期间版本发生变化时，不能按旧版本缓存字节。
        if self._preview_revision(file_path) != revision:
            return (
                jsonify({"message": "Image changed; reload the preview"}),
                409,
                {"Cache-Control": "no-store"},
            )
        response = jsonify(
            {
                "category": category,
                "filename": filename,
                "mime_type": mime_type,
                "size": file_size,
                "data_url": data_url,
            }
        )
        response.headers.update(cache_headers)
        return response
