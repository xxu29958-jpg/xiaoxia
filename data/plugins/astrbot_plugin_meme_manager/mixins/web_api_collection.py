import asyncio
import base64
import hashlib
import io

from quart import jsonify, request, send_file

from astrbot.api import logger


class WebAPIMixinCollection:
    """Collection; shared runtime resources belong to the facade."""

    async def _api_auto_collect_inbox(self):
        """Return all pending images for the selected pack.

        Returns:
            JSON review state or a validation error response.
        """
        try:
            pack_id = await self._semantic_request_pack_id()
            return jsonify(await self.auto_collect_manager.pending_status(pack_id)), 200
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"message": str(exc)}), 400

    async def _api_auto_collect_image(self):
        """Serve an authenticated, pack-scoped pending image.

        Returns:
            Image response or a validation error response.
        """
        try:
            pack_id = await self._semantic_request_pack_id()
            path = self.auto_collect_manager.pending_image_path(
                pack_id, str(request.args.get("id") or "")
            )
            content = await asyncio.to_thread(path.read_bytes)
            extension = await asyncio.to_thread(
                self.auto_collect_manager._validate_image, content
            )
            mime = {
                ".jpg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }[extension]
            response = await send_file(
                io.BytesIO(content),
                mimetype=mime,
                attachment_filename=f"candidate{extension}",
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"message": str(exc)}), 404

    async def _api_auto_collect_image_data(self):
        """Return an authenticated image data URL for embedded dashboards.

        Returns:
            Original image bytes encoded as a data URL.
        """
        try:
            pack_id = await self._semantic_request_pack_id()
            path = self.auto_collect_manager.pending_image_path(
                pack_id, str(request.args.get("id") or "")
            )
            content = await asyncio.to_thread(path.read_bytes)
            extension = await asyncio.to_thread(
                self.auto_collect_manager._validate_image, content
            )
            etag = hashlib.sha256(content).hexdigest()
            headers = {
                "Cache-Control": "private, no-cache",
                "Vary": "Authorization",
                "ETag": f'"{etag}"',
            }
            if request.if_none_match.contains(etag):
                return "", 304, headers
            mime = {
                ".jpg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }[extension]
            response = jsonify(
                {
                    "data_url": f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
                }
            )
            response.headers.update(headers)
            return response, 200
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"message": str(exc)}), 404

    async def _api_auto_collect_accept(self):
        """Accept only explicitly selected collected images.

        Returns:
            Import counts or a validation/conflict response.
        """
        try:
            data = await request.get_json() or {}
            if not isinstance(data, dict):
                raise ValueError("请求格式无效")
            pack_id = await self._semantic_request_pack_id(data)
            result = await self.auto_collect_manager.accept_pending(
                pack_id, data.get("items")
            )
            return jsonify(result), 200
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"message": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409

    async def _api_auto_collect_discard(self):
        """Discard selected collected images with optional rejection memory.

        Returns:
            Discard count or a validation response.
        """
        try:
            data = await request.get_json() or {}
            if not isinstance(data, dict) or not isinstance(
                data.get("remember_rejection", False), bool
            ):
                raise ValueError("请求格式无效")
            pack_id = await self._semantic_request_pack_id(data)
            result = await self.auto_collect_manager.discard_pending(
                pack_id, data.get("ids"), data.get("remember_rejection", False)
            )
            return jsonify(result), 200
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"message": str(exc)}), 400

    async def _api_semantic_import_auto_inbox(self):
        """导入当前表情包对应的自动收集待整理图片。

        Returns:
            包含导入计数的 Quart JSON 响应。
        """
        try:
            data = await request.get_json() or {}
            pack_id = await self._semantic_request_pack_id(data)
            manager = getattr(self, "auto_collect_manager", None)
            if manager is None:
                raise RuntimeError("自动收集管理器不可用")
            result = await manager.import_pending(pack_id)
            return jsonify(
                {
                    "message": (
                        f"已合入 {result['imported']} 张待整理图片"
                        + (
                            f"，跳过 {result['duplicates']} 张重复图片"
                            if result["duplicates"]
                            else ""
                        )
                        + (
                            f"，{result['failed']} 张处理失败"
                            if result["failed"]
                            else ""
                        )
                    ),
                    **result,
                }
            ), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error(
                "[meme_manager] 导入自动收集待整理桶失败：%s",
                exc,
                exc_info=True,
            )
            return jsonify({"message": "合入自动收集待整理桶失败"}), 500
