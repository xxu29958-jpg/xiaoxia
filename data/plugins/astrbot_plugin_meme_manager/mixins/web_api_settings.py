import base64
import binascii
import time

from quart import jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.packs.storage import (
    export_runtime_backup,
    get_selection_rules,
    import_runtime_backup,
    save_selection_rules,
)
from ..backend.plugin_settings import describe_settings, validate_settings_changes
from .web_api_support import PLUGIN_NAME


class WebAPIMixinSettings:
    """Settings; shared runtime resources belong to the facade."""

    async def _api_settings_config(self):
        """Edit the same AstrBotConfig and reload through AstrBot's plugin manager.

        Returns:
            A redacted configuration snapshot or a validation/conflict response.
        """
        try:
            data = await request.get_json() if request.method == "POST" else None
            snapshot, values = describe_settings(self.config)
            if request.method == "GET":
                providers = {"chat": [], "embedding": []}
                for kind, getter in (
                    ("chat", self.context.get_all_providers),
                    ("embedding", self.context.get_all_embedding_providers),
                ):
                    for provider in getter():
                        meta = provider.meta()
                        providers[kind].append(
                            {"id": meta.id, "model": meta.model or ""}
                        )
                snapshot["providers"] = providers
                return jsonify(snapshot), 200
            if not isinstance(data, dict):
                return jsonify({"message": "请求格式不正确。"}), 400
            if data.get("revision") != snapshot["revision"]:
                return jsonify(
                    {
                        "message": "配置已在其他页面更新。请重新加载后再保存，避免覆盖新的设置。"
                    }
                ), 409
            updated = validate_settings_changes(
                values, snapshot["fields"], data.get("changes"), self.config.schema
            )
            manager = getattr(self.context, "_star_manager", None)
            if manager is None:
                return jsonify({"message": "暂时无法应用设置，请重载插件后重试。"}), 503
            metadata = self.context.get_registered_star(PLUGIN_NAME)
            if metadata is None or metadata.star_cls is not self:
                return jsonify(
                    {"message": "插件已经重新加载，请刷新设置页面后重试。"}
                ), 409
            if any(
                not task.done()
                for task in (
                    *getattr(self, "_community_install_tasks", set()),
                    *getattr(self, "_community_update_tasks", set()),
                )
            ):
                return jsonify(
                    {"message": "正在安装或更新表情包，请等待完成后再保存设置。"}
                ), 409
            task_manager = getattr(self, "semantic_task_manager", None)
            if task_manager:
                if (
                    task_manager.active_pack_tasks()
                    or task_manager._external_pack_operations
                ):
                    return jsonify(
                        {"message": "正在处理表情包任务，请等待完成后再保存设置。"}
                    ), 409
            sync = getattr(self, "img_sync", None)
            sync_process = getattr(sync, "sync_process", None)
            if sync_process and sync_process.is_alive():
                return jsonify(
                    {"message": "图床正在同步，请等待完成后再保存设置。"}
                ), 409
            # 在让出执行权之前，使用 AstrBot 的原子配置写入器同步保存。
            try:
                self.config.clear()
                self.config.save_config(updated)
            except Exception:
                self.config.clear()
                self.config.update(values)
                raise
            snapshot, _ = describe_settings(self.config)
        except ValueError as error:
            return jsonify({"message": str(error)}), 400
        except Exception:
            logger.exception("读取或保存插件设置失败")
            return jsonify(
                {"message": "读取或保存配置失败，请检查服务日志后重试。"}
            ), 500
        try:
            success, _ = await manager.reload(PLUGIN_NAME)
            if success:
                return jsonify(
                    {**snapshot, "applied": True, "message": "设置已保存并生效。"}
                ), 200
        except Exception:
            logger.exception("插件设置已保存，但重新加载失败")
        return jsonify(
            {
                **snapshot,
                "applied": False,
                "message": "设置已保存，但插件重载失败。请在 AstrBot 插件管理中重载此插件。",
            }
        ), 200

    async def _api_settings_rules(self):
        if request.method == "GET":
            try:
                return jsonify(get_selection_rules()), 200
            except Exception as e:
                logger.error(f"获取规则失败: {e}", exc_info=True)
                return jsonify({"message": f"获取规则失败: {str(e)}"}), 500

        try:
            data = await request.get_json()
            rules = (data or {}).get("rules", [])
            before = get_selection_rules()
            before_map = {
                str(item.get("id") or ""): str(item.get("pack_id") or "")
                for item in before.get("rules", [])
                if isinstance(item, dict)
            }
            saved = save_selection_rules(rules)
            self._reload_personas()
            rebuild_packs = []
            if bool(getattr(self, "semantic_enabled", False)):
                for item in saved.get("rules", []):
                    if not isinstance(item, dict):
                        continue
                    rule_id = str(item.get("id") or "")
                    pack_id = str(item.get("pack_id") or "")
                    if before_map.get(rule_id) == pack_id or not pack_id:
                        continue
                    status = self.semantic_task_manager.status(pack_id)
                    if status.get("dimension_rebuild_required") and status.get(
                        "semantic_caption_complete"
                    ):
                        rebuild_packs.append(pack_id)
            saved["semantic_rebuild_packs"] = sorted(set(rebuild_packs))
            return jsonify({"message": "规则保存成功", **saved}), 200
        except ValueError as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"保存规则失败: {e}", exc_info=True)
            return jsonify({"message": f"保存规则失败: {str(e)}"}), 500

    async def _api_export_runtime_backup(self):
        try:
            data = await request.get_json()
            output_dir = (data or {}).get("output_dir")
            result = await self._run_guarded_runtime_file_operation(
                "导出全量备份",
                export_runtime_backup,
                output_dir=output_dir,
            )
            return jsonify({"message": "全量备份导出成功", **result}), 200
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except Exception as e:
            logger.error(f"导出全量备份失败: {e}", exc_info=True)
            return jsonify({"message": f"导出全量备份失败: {str(e)}"}), 500

    async def _api_settings_targets(self):
        try:
            rules_payload = get_selection_rules()
            rules = (
                rules_payload.get("rules", [])
                if isinstance(rules_payload, dict)
                else []
            )

            session_targets = []
            seen_session_targets = set()
            if isinstance(rules, list):
                for rule in rules:
                    if not isinstance(rule, dict):
                        continue
                    if str(rule.get("scope") or "").strip() != "session":
                        continue
                    target = str(rule.get("target") or "").strip()
                    if not target or target in seen_session_targets:
                        continue
                    seen_session_targets.add(target)
                    session_targets.append(target)

            persona_targets = []
            personas = getattr(self.context.provider_manager, "personas", [])
            for index, persona in enumerate(
                personas if isinstance(personas, list) else []
            ):
                if not isinstance(persona, dict):
                    continue
                if hasattr(self, "_get_persona_key"):
                    persona_id = str(self._get_persona_key(persona, index)).strip()
                else:
                    persona_id = str(
                        persona.get("id") or persona.get("name") or index
                    ).strip()
                if not persona_id:
                    continue
                persona_name = str(persona.get("name") or persona_id)
                persona_targets.append({"id": persona_id, "label": persona_name})

            return (
                jsonify(
                    {
                        "persona_targets": persona_targets,
                        "session_targets": session_targets,
                    }
                ),
                200,
            )
        except Exception as e:
            logger.error(f"获取规则 target 建议值失败: {e}", exc_info=True)
            return jsonify({"message": f"获取规则 target 建议值失败: {str(e)}"}), 500

    async def _api_import_runtime_backup(self):
        temp_zip_path = None
        try:
            self._prepare_archive_upload_request()
            overwrite_param = request.args.get("overwrite")
            form = await request.form
            json_payload = await request.get_json(silent=True)

            overwrite_raw = overwrite_param
            if overwrite_raw is None:
                if isinstance(form, dict) and form.get("overwrite") is not None:
                    overwrite_raw = form.get("overwrite")
                elif isinstance(json_payload, dict):
                    overwrite_raw = json_payload.get("overwrite", "false")
                else:
                    overwrite_raw = "false"

            overwrite = str(overwrite_raw).lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

            pack_paths.TEMP_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = f"runtime_restore_{int(time.time() * 1000)}.zip"
            temp_zip_path = (pack_paths.TEMP_DIR / safe_name).resolve()

            files = await request.files
            if files and "file" in files:
                archive_file = files["file"]
                if not archive_file or not archive_file.filename:
                    return jsonify({"message": "无效的备份文件"}), 400
                if not str(archive_file.filename).lower().endswith(".zip"):
                    return jsonify({"message": "仅支持 zip 备份文件"}), 400
                await self._save_uploaded_file(archive_file, temp_zip_path)
            elif isinstance(json_payload, dict):
                file_name = str(json_payload.get("file_name") or "").strip()
                file_b64 = str(json_payload.get("file_b64") or "").strip()
                if not file_name or not file_name.lower().endswith(".zip"):
                    return jsonify({"message": "仅支持 zip 备份文件"}), 400
                if not file_b64:
                    return jsonify({"message": "缺少 file_b64"}), 400
                try:
                    raw_bytes = base64.b64decode(file_b64, validate=True)
                except (ValueError, binascii.Error):
                    return jsonify({"message": "file_b64 非法"}), 400
                temp_zip_path.write_bytes(raw_bytes)
            else:
                return (
                    jsonify({"message": "缺少上传文件字段 file 或 JSON file_b64"}),
                    400,
                )

            result = await self._run_guarded_runtime_file_operation(
                "恢复全量备份",
                import_runtime_backup,
                temp_zip_path,
                overwrite=overwrite,
            )
            self._reload_personas()
            return jsonify({"message": "全量备份导入成功", **result}), 200
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except RequestEntityTooLarge:
            return jsonify({"message": "备份文件超过 1 GB，无法通过 WebUI 导入"}), 413
        except (FileNotFoundError, ValueError) as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"导入全量备份失败: {e}", exc_info=True)
            return jsonify({"message": f"导入全量备份失败: {str(e)}"}), 500
        finally:
            if temp_zip_path and temp_zip_path.exists():
                try:
                    temp_zip_path.unlink()
                except Exception:
                    pass
