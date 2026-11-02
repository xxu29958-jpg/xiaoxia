import asyncio

from quart import jsonify, request

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.packs.categories import is_safe_category_name
from ..backend.packs.images import (
    DuplicateEmojiError,
    add_emoji_to_category,
    batch_copy_emojis,
    batch_delete_emojis,
    batch_move_emojis,
    clear_all_emojis,
    delete_emoji_from_category,
    get_emoji_by_category,
    move_emoji_to_category,
    scan_emoji_folder,
)
from ..backend.packs.storage import (
    list_installed_packs,
)
from ..backend.packs.transfer import transfer_pack_images


class WebAPIMixinImages:
    """Images; shared runtime resources belong to the facade."""

    async def _api_get_emojis(self):
        view_context = self._resolve_webui_pack_view_context()
        if view_context:
            emoji_data = await asyncio.to_thread(
                self._scan_pack_emojis, view_context["memes_dir"]
            )
        else:
            emoji_data = await scan_emoji_folder()
        for category in emoji_data:
            if not isinstance(emoji_data[category], list):
                emoji_data[category] = []
        return jsonify(emoji_data)

    async def _api_bridge_auth_token(self):
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            if token:
                return jsonify({"token": token}), 200
        return jsonify({"message": "当前请求缺少 Bearer Token"}), 401

    async def _api_get_emoji_by_category(self, category):
        if not is_safe_category_name(str(category or "")):
            return jsonify({"message": "分类名称非法"}), 400
        view_context = self._resolve_webui_pack_view_context()
        if view_context:
            category_path = view_context["memes_dir"] / category
            if not category_path.is_dir():
                emojis = []
            else:
                emojis = [
                    file_path.name
                    for file_path in category_path.iterdir()
                    if file_path.is_file()
                    and file_path.suffix.lower()
                    in {
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".webp",
                    }
                ]
        else:
            emojis = await asyncio.to_thread(get_emoji_by_category, category)
        if emojis is None:
            return jsonify({"message": "分类未找到"}), 404
        return jsonify(emojis if isinstance(emojis, list) else []), 200

    async def _api_add_emoji(self, category):
        try:
            if not is_safe_category_name(str(category or "")):
                return jsonify({"message": "分类名称非法"}), 400
            files = await request.files
            if not files or "file" not in files:
                return jsonify({"message": "没有找到上传的图片文件"}), 400
            image_file = files["file"]
            if not image_file or not image_file.filename:
                return jsonify({"message": "无效的图片文件"}), 400
            logger.info(f"收到上传请求: 类别={category}, 文件名={image_file.filename}")
            try:

                def mutate():
                    add_result = add_emoji_to_category(category, image_file)
                    self.category_manager.sync_with_filesystem()
                    self._invalidate_default_pack_semantics()
                    return add_result

                result = await self._run_default_pack_mutation("上传表情图片", mutate)
                logger.info(f"表情添加成功: {result['path']}")
                return (
                    jsonify(
                        {
                            "message": "表情添加成功",
                            "path": result["path"],
                            "category": category,
                            "filename": result["filename"],
                        }
                    ),
                    201,
                )
            except DuplicateEmojiError as e:
                logger.info(f"跳过重复表情: {e}")
                return (
                    jsonify(
                        {
                            "message": str(e),
                            "code": "duplicate_emoji",
                            "category": category,
                            "filename": e.existing_filename,
                        }
                    ),
                    409,
                )
            except RuntimeError as e:
                return jsonify({"message": str(e)}), 409
        except Exception as e:
            logger.error(f"处理上传请求时出错: {e}", exc_info=True)
            return jsonify({"message": f"处理上传请求时出错: {str(e)}"}), 500

    async def _api_delete_emoji(self):
        data = await request.get_json()
        category = data.get("category")
        image_file = data.get("image_file")
        if category and not is_safe_category_name(str(category)):
            return jsonify({"message": "分类名称非法"}), 400
        if not category or not image_file:
            return jsonify({"message": "分类和文件名不能为空"}), 400

        def mutate():
            deleted = delete_emoji_from_category(category, image_file)
            if deleted:
                self._invalidate_default_pack_semantics()
            return deleted

        try:
            deleted = await self._run_default_pack_mutation("删除表情图片", mutate)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        if deleted:
            return (
                jsonify(
                    {
                        "message": "表情删除成功",
                        "category": category,
                        "filename": image_file,
                    }
                ),
                200,
            )
        return jsonify({"message": "表情未找到"}), 404

    async def _api_batch_delete_emojis(self):
        data = await request.get_json()
        category = data.get("category")
        image_files = data.get("image_files")
        if category and not is_safe_category_name(str(category)):
            return jsonify({"message": "分类名称非法"}), 400
        if not category or not isinstance(image_files, list) or not image_files:
            return jsonify({"message": "分类和文件名列表不能为空"}), 400

        def mutate():
            delete_result = batch_delete_emojis(category, image_files)
            if delete_result.get("deleted_files"):
                self._invalidate_default_pack_semantics()
            return delete_result

        try:
            result = await self._run_default_pack_mutation("批量删除表情图片", mutate)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        if not result["category_exists"]:
            return jsonify({"message": "分类未找到"}), 404
        return (
            jsonify(
                {
                    "message": "批量删除完成",
                    "category": category,
                    "deleted_files": result["deleted_files"],
                    "missing_files": result["missing_files"],
                    "deleted_count": len(result["deleted_files"]),
                    "missing_count": len(result["missing_files"]),
                }
            ),
            200,
        )

    async def _api_move_emoji(self):
        data = await request.get_json()
        source_category = data.get("source_category")
        target_category = data.get("target_category")
        image_file = data.get("image_file")
        if (
            source_category
            and target_category
            and not all(
                is_safe_category_name(str(value))
                for value in (source_category, target_category)
            )
        ):
            return jsonify({"message": "分类名称非法"}), 400
        if not source_category or not target_category or not image_file:
            return jsonify({"message": "源分类、目标分类和文件名不能为空"}), 400
        if source_category == target_category:
            return jsonify({"message": "源分类和目标分类不能相同"}), 400

        def mutate():
            move_result = move_emoji_to_category(
                source_category, image_file, target_category
            )
            if move_result.get("moved"):
                self._invalidate_default_pack_semantics()
            return move_result

        try:
            result = await self._run_default_pack_mutation("移动表情图片", mutate)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        if not result["source_category_exists"]:
            return jsonify({"message": "源分类未找到"}), 404
        if result["conflict"]:
            return jsonify({"message": "目标文件已存在"}), 409
        if result["missing"]:
            return jsonify({"message": "表情未找到"}), 404
        return (
            jsonify(
                {
                    "message": "表情移动成功",
                    "source_category": result["source_category"],
                    "target_category": result["target_category"],
                    "filename": result["filename"],
                }
            ),
            200,
        )

    async def _api_transfer_pack_images(self):
        """Preview or execute a transfer while holding both pack operation locks.

        Returns:
            A category plan, per-image outcomes, or a validation/conflict response.
        """
        locked = []
        manager = getattr(self, "semantic_task_manager", None)
        try:
            data = await request.get_json()
            if not isinstance(data, dict):
                raise ValueError("请求格式无效")
            installed = {pack["id"] for pack in list_installed_packs()}
            pack_ids = [data.get("source_pack_id"), data.get("target_pack_id")]
            if any(
                not isinstance(pack_id, str) or pack_id not in installed
                for pack_id in pack_ids
            ):
                raise ValueError("请选择已安装的来源和目标表情包")
            if manager is None:
                raise RuntimeError("表情包任务管理器尚未就绪，请稍后重试")
            for pack_id in sorted(set(pack_ids)):
                manager.begin_external_pack_operation(pack_id, "跨表情包移动")
                locked.append(pack_id)
            worker = asyncio.create_task(
                asyncio.to_thread(transfer_pack_images, pack_paths.PACKS_DIR, data)
            )
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(worker)
                except Exception:
                    pass
                raise
            if (
                result.get("moved")
                and self._resolve_runtime_pack_context().get("pack_id") in pack_ids
            ):
                try:
                    await self.reload_emotions()
                except Exception:
                    result["warnings"].append(
                        "图片已移动，运行时分类刷新失败，请刷新或重启插件"
                    )
            return jsonify(result), 200
        except (ValueError, FileNotFoundError) as exc:
            return jsonify({"message": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except OSError:
            logger.exception("Failed to prepare cross-pack image transfer")
            return jsonify({"message": "分类配置无法保存，尚未执行图片移动"}), 500
        finally:
            for pack_id in reversed(locked):
                manager.end_external_pack_operation(pack_id)

    async def _api_batch_move_emojis(self):
        data = await request.get_json()
        source_category = data.get("source_category")
        target_category = data.get("target_category")
        image_files = data.get("image_files")
        if (
            source_category
            and target_category
            and not all(
                is_safe_category_name(str(value))
                for value in (source_category, target_category)
            )
        ):
            return jsonify({"message": "分类名称非法"}), 400
        if (
            not source_category
            or not target_category
            or not isinstance(image_files, list)
            or not image_files
        ):
            return jsonify({"message": "源分类、目标分类和文件名列表不能为空"}), 400
        if source_category == target_category:
            return jsonify({"message": "源分类和目标分类不能相同"}), 400

        def mutate():
            move_result = batch_move_emojis(
                source_category, image_files, target_category
            )
            if move_result.get("moved_files"):
                self._invalidate_default_pack_semantics()
            return move_result

        try:
            result = await self._run_default_pack_mutation("批量移动表情图片", mutate)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        if not result["source_category_exists"]:
            return jsonify({"message": "源分类未找到"}), 404
        return (
            jsonify(
                {
                    "message": "批量移动完成",
                    "source_category": source_category,
                    "target_category": target_category,
                    "moved_files": result["moved_files"],
                    "missing_files": result["missing_files"],
                    "conflicting_files": result["conflicting_files"],
                    "moved_count": len(result["moved_files"]),
                    "missing_count": len(result["missing_files"]),
                    "conflict_count": len(result["conflicting_files"]),
                }
            ),
            200,
        )

    async def _api_batch_copy_emojis(self):
        data = await request.get_json()
        source_category = data.get("source_category")
        target_category = data.get("target_category")
        image_files = data.get("image_files")
        if (
            source_category
            and target_category
            and not all(
                is_safe_category_name(str(value))
                for value in (source_category, target_category)
            )
        ):
            return jsonify({"message": "分类名称非法"}), 400
        if (
            not source_category
            or not target_category
            or not isinstance(image_files, list)
            or not image_files
        ):
            return jsonify({"message": "源分类、目标分类和文件名列表不能为空"}), 400

        def mutate():
            copy_result = batch_copy_emojis(
                source_category, image_files, target_category
            )
            if copy_result.get("copied_files"):
                self._invalidate_default_pack_semantics()
            return copy_result

        try:
            result = await self._run_default_pack_mutation("批量复制表情图片", mutate)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        if not result["source_category_exists"]:
            return jsonify({"message": "源分类未找到"}), 404
        return (
            jsonify(
                {
                    "message": "批量复制完成",
                    "source_category": source_category,
                    "target_category": target_category,
                    "copied_files": result["copied_files"],
                    "missing_files": result["missing_files"],
                    "conflicting_files": result["conflicting_files"],
                    "copied_count": len(result["copied_files"]),
                    "missing_count": len(result["missing_files"]),
                    "conflict_count": len(result["conflicting_files"]),
                }
            ),
            200,
        )

    async def _api_clear_all_emojis(self):
        def mutate():
            clear_result = clear_all_emojis()
            if any(clear_result.get("deleted_by_category", {}).values()):
                self._invalidate_default_pack_semantics()
            return clear_result

        try:
            result = await self._run_default_pack_mutation("清空全部表情图片", mutate)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        deleted_count = sum(result["deleted_by_category"].values())
        return (
            jsonify(
                {
                    "message": "所有表情已清空",
                    "deleted_by_category": result["deleted_by_category"],
                    "deleted_count": deleted_count,
                    "affected_categories": len(result["deleted_by_category"]),
                }
            ),
            200,
        )
