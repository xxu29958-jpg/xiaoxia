import asyncio
from pathlib import Path

from quart import jsonify, request

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.packs.storage import (
    get_pack_detail,
    list_installed_packs,
)
from ..backend.semantic.index import EmbeddingAdapter, index_is_ready
from ..backend.semantic.storage import (
    import_metadata_file,
    load_metadata,
    metadata_items,
)


class WebAPIMixinPacks:
    """Packs; shared runtime resources belong to the facade."""

    async def _api_list_packs(self):
        try:
            return jsonify({"packs": await asyncio.to_thread(list_installed_packs)})
        except Exception as e:
            logger.error(f"获取已安装表情包列表失败: {e}", exc_info=True)
            return jsonify({"message": f"获取已安装表情包列表失败: {str(e)}"}), 500

    async def _api_get_pack_detail(self, pack_id: str):
        try:
            return jsonify(await asyncio.to_thread(get_pack_detail, pack_id))
        except FileNotFoundError as e:
            return jsonify({"message": str(e)}), 404
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except ValueError as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"获取表情包详情失败: {e}", exc_info=True)
            return jsonify({"message": f"获取表情包详情失败: {str(e)}"}), 500

    async def _api_semantic_status(self):
        try:
            pack_id = await self._semantic_request_pack_id()
            self._get_img_host_sync_task_status()
            result = self.semantic_task_manager.status(pack_id)
            metadata = await asyncio.to_thread(
                load_metadata, pack_paths.PACKS_DIR / pack_id, scan_legacy=False
            )
            provider = EmbeddingAdapter(
                self.semantic_task_manager._resolve_embedding_provider(pack_id),
                str(getattr(self, "semantic_embedding_provider_id", "") or ""),
            )
            result["index_ready"] = index_is_ready(
                pack_paths.PLUGIN_DATA_DIR,
                pack_id,
                metadata,
                provider.provider_id,
                provider.model_name,
                provider.dimension,
            )
            result["semantic_enabled"] = bool(getattr(self, "semantic_enabled", False))
            result["semantic_config_ready"] = bool(
                not result["semantic_enabled"] or result.get("embedding_provider_ready")
            )
            manager = getattr(self, "auto_collect_manager", None)
            result["auto_collect_inbox"] = (
                await manager.pending_status(pack_id)
                if manager is not None
                else {"visible": False, "count": 0, "items": []}
            )
            return jsonify(result), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("读取语义状态失败: %s", exc, exc_info=True)
            return jsonify({"message": "读取语义状态失败"}), 500

    async def _api_semantic_items(self):
        try:
            pack_id = await self._semantic_request_pack_id()
            try:
                page = max(1, int(request.args.get("page", 1) or 1))
            except (TypeError, ValueError):
                page = 1
            try:
                page_size = int(request.args.get("page_size", 20) or 20)
            except (TypeError, ValueError):
                page_size = 20
            page_size = min(100, max(10, page_size))
            all_items = await asyncio.to_thread(
                metadata_items,
                pack_paths.PACKS_DIR / pack_id,
                request.args.get("status"),
            )
            total = len(all_items)
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, total_pages)
            start = (page - 1) * page_size
            return jsonify(
                {
                    "pack_id": pack_id,
                    "items": all_items[start : start + page_size],
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": total_pages,
                }
            ), 200
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("读取语义记录失败: %s", exc, exc_info=True)
            return jsonify({"message": "读取语义记录失败"}), 500

    async def _api_semantic_start(self):
        try:
            data = await request.get_json() or {}
            pack_id = await self._semantic_request_pack_id(data)
            self._get_img_host_sync_task_status()
            external_data = (
                data.get("external_metadata")
                if isinstance(data.get("external_metadata"), dict)
                else None
            )
            external_path = data.get("external_metadata_path")
            if external_path:
                source = Path(str(external_path)).expanduser().resolve()
                allowed_roots = [
                    pack_paths.PLUGIN_DATA_DIR.resolve(),
                    pack_paths.TEMP_DIR.resolve(),
                ]
                if not any(
                    source == root or root in source.parents for root in allowed_roots
                ):
                    raise ValueError("外部语义文件必须位于插件数据目录或临时目录")
                external_data = import_metadata_file(source)
            result = await self.semantic_task_manager.start(
                pack_id,
                mode=str(data.get("mode") or "full"),
                force=bool(data.get("force", False)),
                concurrency=data.get("concurrency"),
                external_data=external_data,
            )
            return jsonify(result), 202
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("启动语义任务失败: %s", exc, exc_info=True)
            return jsonify({"message": "启动语义任务失败"}), 500
