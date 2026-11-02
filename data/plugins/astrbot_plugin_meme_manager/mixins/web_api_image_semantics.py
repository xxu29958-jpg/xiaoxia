import base64
import io
import re
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from quart import jsonify, request

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.semantic.storage import (
    get_category_review_overview,
    get_image_semantic_detail,
)
from .web_api_support import PREVIEW_IMAGE_MAX_DIMENSION


class WebAPIMixinImageSemantics:
    """Image semantics; shared runtime resources belong to the facade."""

    async def _api_get_meme_image_semantic(self):
        category = str(request.args.get("category", "") or "").strip()
        filename = str(request.args.get("filename", "") or "").strip()
        if not self._safe_semantic_image_name(
            category
        ) or not self._safe_semantic_image_name(filename):
            return jsonify({"message": "分类或文件名无效"}), 400
        view_context = self._resolve_webui_pack_view_context()
        memes_root = (
            view_context["memes_dir"].resolve()
            if view_context
            else Path(self._resolve_runtime_pack_context()["memes_dir"]).resolve()
        )
        pack_dir = (
            view_context["pack_dir"].resolve()
            if view_context
            else memes_root.parent.resolve()
        )
        pack_id = str(
            (view_context or {}).get("pack_id") or pack_dir.name or ""
        ).strip()
        requested_file_path = memes_root / category / filename
        if requested_file_path.is_symlink():
            return jsonify({"message": "不允许通过符号链接读取图片语义"}), 400
        file_path = requested_file_path.resolve()

        try:
            file_path.relative_to(memes_root)
        except ValueError:
            return jsonify({"message": "图片路径无效"}), 403
        if not file_path.is_file():
            return jsonify({"message": "图片不存在"}), 404

        try:
            detail = get_image_semantic_detail(pack_dir, file_path)
            return jsonify(
                {
                    "pack_id": pack_id,
                    "category": category,
                    "filename": filename,
                    "semantic": detail,
                }
            )
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("读取图片语义失败: %s", exc, exc_info=True)
            return jsonify({"message": "读取图片语义失败"}), 500

    @staticmethod
    def _safe_semantic_image_name(value: str) -> bool:
        normalized = str(value or "").strip()
        return bool(
            normalized
            and normalized not in {".", ".."}
            and Path(normalized).name == normalized
            and "/" not in normalized
            and "\\" not in normalized
        )

    async def _semantic_image_edit_request(
        self, data: dict[str, Any]
    ) -> tuple[str, str, str, Path]:
        pack_id = await self._semantic_request_pack_id(data)
        expected_pack_id = str(data.get("expected_pack_id") or "").strip()
        if not expected_pack_id:
            raise ValueError("缺少图包编辑快照，请重新打开图片后再操作")
        if expected_pack_id != pack_id:
            raise RuntimeError("当前图包已经切换，请重新打开图片后再编辑")
        expected_digest = str(data.get("expected_content_sha256") or "").strip().lower()
        expected_entry_id = str(data.get("expected_entry_id") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_entry_id
        ):
            raise ValueError("图片编辑快照无效，请重新打开图片后再操作")
        category = str(data.get("category") or "").strip()
        filename = str(data.get("filename") or "").strip()
        if not self._safe_semantic_image_name(
            category
        ) or not self._safe_semantic_image_name(filename):
            raise ValueError("分类或文件名无效")
        pack_dir = (pack_paths.PACKS_DIR / pack_id).resolve()
        memes_root = (pack_dir / "memes").resolve()
        requested_image_path = memes_root / category / filename
        if requested_image_path.is_symlink():
            raise ValueError("不允许通过符号链接编辑图片")
        image_path = requested_image_path.resolve()
        try:
            image_path.relative_to(memes_root)
        except ValueError as exc:
            raise ValueError("图片路径无效") from exc
        return pack_id, category, filename, image_path

    async def _api_semantic_save_image(self):
        return await self._api_semantic_save_image_impl(update_vector=False)

    async def _api_semantic_save_image_and_vector(self):
        return await self._api_semantic_save_image_impl(update_vector=True)

    async def _api_semantic_propose_image_revision(self):
        try:
            data = await request.get_json() or {}
            (
                pack_id,
                category,
                filename,
                image_path,
            ) = await self._semantic_image_edit_request(data)
            proposal = await self.semantic_task_manager.propose_image_semantic_revision(
                pack_id,
                image_path,
                review_instruction=str(data.get("review_instruction") or ""),
                expected_content_sha256=str(data.get("expected_content_sha256") or ""),
                expected_entry_id=str(data.get("expected_entry_id") or ""),
            )
            return jsonify(
                {
                    "message": "视觉模型已重写语义并选择分类候选；检查后请点击保存，当前语义尚未改变",
                    "pack_id": pack_id,
                    "category": category,
                    "filename": filename,
                    "proposal": proposal,
                }
            ), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except RuntimeError as exc:
            status = 503 if "没有可用的视觉模型" in str(exc) else 409
            return jsonify({"message": str(exc)}), status
        except Exception as exc:
            logger.error("按人工复审意见生成图片语义失败: %s", exc, exc_info=True)
            return jsonify({"message": f"视觉模型生成失败：{str(exc)[:300]}"}), 502

    async def _api_semantic_save_image_impl(self, *, update_vector: bool):
        try:
            data = await request.get_json() or {}
            (
                pack_id,
                category,
                filename,
                image_path,
            ) = await self._semantic_image_edit_request(data)
            result = await self.semantic_task_manager.save_image_manual_semantic(
                pack_id,
                image_path,
                caption=str(data.get("caption") or ""),
                tags=data.get("tags", []),
                visible_text=str(data.get("visible_text") or ""),
                category_decision=str(data.get("category_decision") or "keep"),
                expected_content_sha256=str(data.get("expected_content_sha256") or ""),
                expected_entry_id=str(data.get("expected_entry_id") or ""),
                update_vector=update_vector,
                target_category=str(data.get("target_category") or ""),
            )
            vector_status = str(result.get("vector_update", {}).get("status") or "")
            moved = bool(result.get("moved"))
            if not update_vector:
                message = "人工语义已保存，向量等待更新"
            elif vector_status == "done":
                message = (
                    "人工语义已保存，分类已移动，当前图片向量已更新"
                    if moved
                    else "人工语义已保存，当前图片向量已更新"
                )
            else:
                base_message = str(
                    result.get("vector_update", {}).get("message")
                    or "人工语义已保存，向量等待更新"
                )
                message = f"分类已移动；{base_message}" if moved else base_message
            return jsonify(
                {
                    "message": message,
                    "pack_id": pack_id,
                    "category": category,
                    "filename": filename,
                    **result,
                }
            ), 200
        except FileExistsError as exc:
            return jsonify({"message": str(exc)}), 409
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("保存图片人工语义失败: %s", exc, exc_info=True)
            return jsonify({"message": "保存图片人工语义失败"}), 500

    async def _api_semantic_restore_image_auto(self):
        try:
            data = await request.get_json() or {}
            (
                pack_id,
                category,
                filename,
                image_path,
            ) = await self._semantic_image_edit_request(data)
            detail = await self.semantic_task_manager.restore_image_auto_semantic(
                pack_id,
                image_path,
                expected_content_sha256=str(data.get("expected_content_sha256") or ""),
                expected_entry_id=str(data.get("expected_entry_id") or ""),
            )
            return jsonify(
                {
                    "message": "已放弃当前图片的人工修改，恢复为自动生成状态",
                    "pack_id": pack_id,
                    "category": category,
                    "filename": filename,
                    "semantic": detail,
                }
            ), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("恢复图片自动语义失败: %s", exc, exc_info=True)
            return jsonify({"message": "恢复图片自动语义失败"}), 500

    async def _api_semantic_reviews(self):
        try:
            pack_id = await self._semantic_request_pack_id()
            return jsonify(
                {
                    "pack_id": pack_id,
                    **get_category_review_overview(pack_paths.PACKS_DIR / pack_id),
                }
            ), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("读取分类审核状态失败: %s", exc, exc_info=True)
            return jsonify({"message": "读取分类审核状态失败"}), 500

    async def _api_semantic_confirm_category(self):
        try:
            data = await request.get_json() or {}
            (
                pack_id,
                category,
                filename,
                image_path,
            ) = await self._semantic_image_edit_request(data)
            detail = await self.semantic_task_manager.confirm_category(
                pack_id,
                image_path,
                expected_content_sha256=str(data.get("expected_content_sha256") or ""),
                expected_entry_id=str(data.get("expected_entry_id") or ""),
            )
            return jsonify(
                {
                    "message": "已确认当前分类正确",
                    "pack_id": pack_id,
                    "category": category,
                    "filename": filename,
                    "semantic": detail,
                }
            ), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("确认图片分类失败: %s", exc, exc_info=True)
            return jsonify({"message": "确认图片分类失败"}), 500

    @staticmethod
    def _build_file_data_url(file_path, mime_type: str) -> str:
        with open(file_path, "rb") as image_file:
            encoded = base64.b64encode(image_file.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _build_preview_data_url(file_path) -> tuple[str, str]:
        resample_filter = getattr(
            getattr(PILImage, "Resampling", PILImage),
            "LANCZOS",
            PILImage.BICUBIC,
        )
        with PILImage.open(file_path) as image:
            image.thumbnail(
                (PREVIEW_IMAGE_MAX_DIMENSION, PREVIEW_IMAGE_MAX_DIMENSION),
                resample_filter,
            )
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=82, method=4)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/webp;base64,{encoded}", "image/webp"
