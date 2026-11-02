import asyncio

from quart import jsonify, request

from astrbot.api import logger

from ..backend.packs.categories import is_safe_category_name
from ..backend.packs.images import (
    clear_category_emojis,
)


class WebAPIMixinCategories:
    """Categories; shared runtime resources belong to the facade."""

    async def _api_get_emotions(self):
        try:
            view_context = self._resolve_webui_pack_view_context()
            if view_context:
                descriptions = await asyncio.to_thread(
                    self._load_pack_descriptions, view_context
                )
            else:
                descriptions = await asyncio.to_thread(
                    self.category_manager.get_descriptions
                )
            return jsonify(descriptions)
        except Exception as e:
            logger.error(f"获取标签描述失败: {e}")
            return jsonify({"error": "获取标签描述失败"}), 500

    async def _api_delete_category(self):
        try:
            data = await request.get_json()
            category = data.get("category")
            if not category:
                return jsonify({"message": "分类不能为空"}), 400
            deleted = await self._run_default_pack_mutation(
                "删除表情分类",
                lambda: self.category_manager.delete_category(category),
            )
            if deleted:
                return jsonify({"message": "分类删除成功"}), 200
            return jsonify({"message": "分类删除失败"}), 500
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except Exception as e:
            return jsonify({"message": f"分类删除失败: {str(e)}"}), 500

    async def _api_clear_category(self):
        data = await request.get_json()
        category = data.get("category")
        if category and not is_safe_category_name(str(category)):
            return jsonify({"message": "分类名称非法"}), 400
        if not category:
            return jsonify({"message": "分类不能为空"}), 400

        def mutate():
            clear_result = clear_category_emojis(category)
            if clear_result.get("deleted_files"):
                self._invalidate_default_pack_semantics()
            return clear_result

        try:
            result = await self._run_default_pack_mutation("清空表情分类", mutate)
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        if not result["category_exists"]:
            return jsonify({"message": "分类未找到"}), 404
        return (
            jsonify(
                {
                    "message": "分类表情已清空",
                    "category": category,
                    "deleted_files": result["deleted_files"],
                    "deleted_count": len(result["deleted_files"]),
                }
            ),
            200,
        )

    async def _api_restore_category(self):
        try:
            data = await request.get_json()
            category = data.get("category")
            description = data.get("description", "请添加描述")
            if not category:
                return jsonify({"message": "分类不能为空"}), 400
            created = await self._run_default_pack_mutation(
                "创建表情分类",
                lambda: self.category_manager.create_category(category, description),
            )
            if created:
                return (
                    jsonify({"message": "分类创建成功", "description": description}),
                    200,
                )
            return jsonify({"message": "分类创建失败"}), 500
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except Exception as e:
            return jsonify({"message": f"分类创建失败: {str(e)}"}), 500

    async def _api_rename_category(self):
        try:
            data = await request.get_json()
            old_name = data.get("old_name")
            new_name = data.get("new_name")
            if not old_name or not new_name:
                return jsonify({"message": "旧分类名和新分类名不能为空"}), 400
            renamed = await self._run_default_pack_mutation(
                "重命名表情分类",
                lambda: self.category_manager.rename_category(old_name, new_name),
            )
            if renamed:
                return jsonify({"message": "分类重命名成功"}), 200
            return jsonify({"message": "分类重命名失败"}), 500
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except Exception as e:
            return jsonify({"message": f"分类重命名失败: {str(e)}"}), 500

    async def _api_update_description(self):
        try:
            data = await request.get_json()
            category = data.get("tag")
            description = data.get("description")
            if not category or not description:
                return jsonify({"message": "分类和描述不能为空"}), 400
            updated = await self._run_default_pack_mutation(
                "更新表情分类描述",
                lambda: self.category_manager.update_description(category, description),
            )
            if updated:
                return jsonify({"category": category, "description": description}), 200
            return jsonify({"message": "更新分类描述失败"}), 500
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except Exception as e:
            return jsonify({"message": f"更新分类描述失败: {str(e)}"}), 500

    async def _api_remove_from_config(self):
        try:
            data = await request.get_json()
            category = data.get("category")
            if not category:
                return jsonify({"message": "分类不能为空"}), 400
            removed = await self._run_default_pack_mutation(
                "移除表情分类配置",
                lambda: self.category_manager.remove_from_config(category),
            )
            if removed:
                return jsonify({"message": "已从配置中移除分类"}), 200
            return jsonify({"message": "从配置中移除分类失败"}), 500
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except Exception as e:
            return jsonify({"message": f"从配置中移除分类失败: {str(e)}"}), 500
