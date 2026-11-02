from quart import jsonify, request

from astrbot.api import logger


class WebAPIMixinTasks:
    """Tasks; shared runtime resources belong to the facade."""

    async def _api_semantic_pause(self):
        return await self._api_semantic_task_action("pause")

    async def _api_semantic_resume(self):
        return await self._api_semantic_task_action("resume")

    async def _api_semantic_retry(self):
        try:
            data = await request.get_json() or {}
            pack_id = await self._semantic_request_pack_id(data)
            self._get_img_host_sync_task_status()
            result = await self.semantic_task_manager.start(
                pack_id,
                mode="retry_failed",
                concurrency=data.get("concurrency"),
            )
            return jsonify(result), 202
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("重试语义任务失败: %s", exc, exc_info=True)
            return jsonify({"message": "重试语义任务失败"}), 500

    async def _api_semantic_task_action(self, action: str):
        try:
            data = await request.get_json() or {}
            pack_id = await self._semantic_request_pack_id(data)
            self._get_img_host_sync_task_status()
            if action == "resume":
                result = await self.semantic_task_manager.resume(
                    pack_id, concurrency=data.get("concurrency")
                )
            else:
                result = await getattr(self.semantic_task_manager, action)(pack_id)
            return jsonify(result), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("语义任务操作失败: %s", exc, exc_info=True)
            return jsonify({"message": "语义任务操作失败"}), 500

    async def _api_semantic_rebuild_index(self):
        try:
            data = await request.get_json() or {}
            pack_id = await self._semantic_request_pack_id(data)
            self._get_img_host_sync_task_status()
            result = await self.semantic_task_manager.rebuild_index(
                pack_id, force=bool(data.get("force", False))
            )
            return jsonify({"message": "向量索引已建立", **result}), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("重建语义索引失败: %s", exc, exc_info=True)
            return jsonify({"message": "重建语义索引失败"}), 500

    async def _api_semantic_clear_local_state(self):
        try:
            data = await request.get_json() or {}
            pack_id = await self._semantic_request_pack_id(data)
            result = await self.semantic_task_manager.clear_local_semantic_state(
                pack_id
            )
            return jsonify(
                {"message": "已清理本机任务与向量，图片描述已保留", **result}
            ), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("清理本机语义状态失败: %s", exc, exc_info=True)
            return jsonify({"message": "清理本机语义状态失败"}), 500

    async def _api_semantic_delete_all(self):
        try:
            data = await request.get_json() or {}
            pack_id = await self._semantic_request_pack_id(data)
            result = await self.semantic_task_manager.delete_all_semantic_data(pack_id)
            return jsonify(
                {
                    "message": "已删除当前资源包的全部语义化数据，原图片已保留",
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
            logger.error("删除全部语义化数据失败: %s", exc, exc_info=True)
            return jsonify({"message": "删除全部语义化数据失败"}), 500
