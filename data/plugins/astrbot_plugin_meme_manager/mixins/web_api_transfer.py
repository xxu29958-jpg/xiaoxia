import asyncio
import json
import secrets
import time
import zipfile
from pathlib import Path

from quart import jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.packs.storage import (
    create_local_pack,
    export_pack_archive,
    get_pack_export_capabilities,
    import_pack_archive,
    inspect_pack_archive,
    set_default_pack,
    uninstall_pack,
)
from .web_api_support import (
    MAX_PACK_ARCHIVE_BYTES,
    MAX_PACK_UPLOAD_REQUEST_BYTES,
)


class WebAPIMixinTransfer:
    """Transfer; shared runtime resources belong to the facade."""

    async def _api_set_default_pack(self):
        try:
            data = await request.get_json()
            pack_id = str((data or {}).get("pack_id") or "").strip()
            if not pack_id:
                return jsonify({"message": "pack_id 不能为空"}), 400
            result = set_default_pack(pack_id)
            self._reload_personas()
            result.update(self._semantic_rebuild_guidance(pack_id))
            return jsonify({"message": "默认表情包设置成功", **result}), 200
        except FileNotFoundError as e:
            return jsonify({"message": str(e)}), 404
        except ValueError as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"设置默认表情包失败: {e}", exc_info=True)
            return jsonify({"message": f"设置默认表情包失败: {str(e)}"}), 500

    async def _api_export_pack(self):
        try:
            data = await request.get_json()
            payload = data or {}
            pack_id = str(payload.get("pack_id") or "").strip()
            output_dir = payload.get("output_dir")
            export_mode = str(payload.get("export_mode") or "share").strip().lower()
            include_value = payload.get(
                "include_semantic", payload.get("semantic", True)
            )
            include_semantic = str(include_value).lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            result = await self._run_guarded_pack_file_operation(
                pack_id,
                "导出资源包",
                export_pack_archive,
                pack_id,
                output_dir=output_dir,
                include_semantic=include_semantic,
                export_mode=export_mode,
            )
            return jsonify({"message": "导出成功", **result}), 200
        except FileNotFoundError as e:
            return jsonify({"message": str(e)}), 404
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except ValueError as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"导出表情包失败: {e}", exc_info=True)
            return jsonify({"message": f"导出表情包失败: {str(e)}"}), 500

    async def _api_pack_export_status(self):
        try:
            pack_id = str(request.args.get("pack_id") or "").strip()
            result = await asyncio.to_thread(get_pack_export_capabilities, pack_id)
            return jsonify(result), 200
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("读取表情包导出能力失败: %s", exc, exc_info=True)
            return jsonify({"message": "读取表情包导出能力失败"}), 500

    async def _api_download_pack(self):
        try:
            pack_id = str(request.args.get("pack_id") or "").strip()
            export_mode = str(request.args.get("mode") or "share").strip().lower()
            result = await self._run_guarded_pack_file_operation(
                pack_id,
                "导出资源包",
                export_pack_archive,
                pack_id,
                export_mode=export_mode,
            )
            return await send_file(
                result["archive_path"],
                mimetype="application/zip",
                as_attachment=True,
                attachment_filename=result["archive_filename"],
            )
        except FileNotFoundError as exc:
            return jsonify({"message": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("下载表情包失败: %s", exc, exc_info=True)
            return jsonify({"message": "下载表情包失败"}), 500

    async def _api_import_pack(self):
        temp_zip_path = None
        try:
            self._prepare_archive_upload_request()
            content_length = request.content_length
            if (
                content_length is not None
                and content_length > MAX_PACK_UPLOAD_REQUEST_BYTES
            ):
                return jsonify({"message": "压缩包超过 1 GB，无法通过 WebUI 导入"}), 413
            form = await request.form
            overwrite = str(form.get("overwrite", "false")).lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            set_as_default = str(form.get("set_as_default", "false")).lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            overwrite_manual_semantics = str(
                form.get("overwrite_manual_semantics", "false")
            ).lower() in {"1", "true", "yes", "on"}
            import_signature = self._pack_import_embedding_signature()

            files = await request.files
            if not files or "file" not in files:
                return jsonify({"message": "缺少上传文件字段 file"}), 400

            archive_file = files["file"]
            if not archive_file or not archive_file.filename:
                return jsonify({"message": "无效的压缩包文件"}), 400

            filename = str(archive_file.filename)
            if not filename.lower().endswith(".zip"):
                return jsonify({"message": "仅支持 zip 压缩包"}), 400

            temp_dir = pack_paths.TEMP_DIR
            temp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"import_{int(time.time() * 1000)}.zip"
            temp_zip_path = (temp_dir / safe_name).resolve()
            await self._save_uploaded_file(archive_file, temp_zip_path)

            suggested_pack_id = Path(filename).stem
            if overwrite:
                inspection = await asyncio.to_thread(
                    inspect_pack_archive,
                    temp_zip_path,
                    suggested_pack_id=suggested_pack_id,
                )
                result = await self._run_guarded_pack_file_operation(
                    str(inspection.get("pack_id") or ""),
                    "覆盖安装资源包",
                    import_pack_archive,
                    temp_zip_path,
                    overwrite=True,
                    set_as_default=set_as_default,
                    suggested_pack_id=suggested_pack_id,
                    preserve_existing_manual=not overwrite_manual_semantics,
                    **import_signature,
                )
            else:
                result = await self._run_guarded_runtime_file_operation(
                    "安装资源包",
                    import_pack_archive,
                    temp_zip_path,
                    overwrite=False,
                    set_as_default=set_as_default,
                    suggested_pack_id=suggested_pack_id,
                    preserve_existing_manual=not overwrite_manual_semantics,
                    **import_signature,
                )
            self._reload_personas()
            return jsonify({"message": "导入成功", **result}), 200
        except FileExistsError as e:
            return jsonify({"message": str(e)}), 409
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except RequestEntityTooLarge:
            return jsonify({"message": "压缩包超过 1 GB，无法通过 WebUI 导入"}), 413
        except (FileNotFoundError, ValueError) as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"导入表情包失败: {e}", exc_info=True)
            return jsonify({"message": f"导入表情包失败: {str(e)}"}), 500
        finally:
            if temp_zip_path and temp_zip_path.exists():
                try:
                    temp_zip_path.unlink()
                except Exception:
                    pass

    async def _api_stage_pack_import(self):
        archive_path = None
        metadata_path = None
        try:
            self._prepare_archive_upload_request()
            self._cleanup_pack_import_sessions()
            content_length = request.content_length
            if (
                content_length is not None
                and content_length > MAX_PACK_UPLOAD_REQUEST_BYTES
            ):
                return jsonify({"message": "压缩包超过 1 GB，无法通过 WebUI 导入"}), 413
            files = await request.files
            if not files or "file" not in files:
                return jsonify({"message": "缺少上传文件字段 file"}), 400
            archive_file = files["file"]
            filename = str(getattr(archive_file, "filename", "") or "").strip()
            if not filename or not filename.lower().endswith(".zip"):
                return jsonify({"message": "请选择 zip 格式的表情包"}), 400

            token = secrets.token_hex(16)
            archive_path, metadata_path = self._pack_import_session_paths(token)
            await self._save_uploaded_file(archive_file, archive_path)
            if archive_path.stat().st_size > MAX_PACK_ARCHIVE_BYTES:
                raise ValueError("压缩包超过 1 GB，无法通过 WebUI 导入")

            suggested_pack_id = Path(filename).stem
            inspection = await asyncio.to_thread(
                inspect_pack_archive,
                archive_path,
                suggested_pack_id=suggested_pack_id,
            )
            metadata_path.write_text(
                json.dumps(
                    {
                        "filename": filename,
                        "suggested_pack_id": suggested_pack_id,
                        "pack_id": str(inspection.get("pack_id") or ""),
                        "created_at": time.time(),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return jsonify(
                {
                    "message": "压缩包检查完成，请确认后导入",
                    "import_token": token,
                    **inspection,
                }
            ), 200
        except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
            for path in (archive_path, metadata_path):
                if path and path.exists():
                    path.unlink()
            return jsonify({"message": str(exc)}), 400
        except RequestEntityTooLarge:
            for path in (archive_path, metadata_path):
                if path and path.exists():
                    path.unlink()
            return jsonify({"message": "压缩包超过 1 GB，无法通过 WebUI 导入"}), 413
        except Exception as exc:
            for path in (archive_path, metadata_path):
                if path and path.exists():
                    path.unlink()
            logger.error("预检导入压缩包失败: %s", exc, exc_info=True)
            return jsonify({"message": "预检导入压缩包失败"}), 500

    async def _api_apply_pack_import(self):
        try:
            self._cleanup_pack_import_sessions()
            data = await request.get_json() or {}
            token = str(data.get("import_token") or "").strip()
            archive_path, metadata_path = self._pack_import_session_paths(token)
            if not archive_path.is_file() or not metadata_path.is_file():
                raise ValueError("导入凭证已过期，请重新选择压缩包")
            session_data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(session_data, dict):
                raise ValueError("导入凭证损坏，请重新选择压缩包")

            overwrite = str(data.get("overwrite", "false")).lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            set_as_default = str(data.get("set_as_default", "false")).lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            overwrite_manual_semantics = str(
                data.get("overwrite_manual_semantics", "false")
            ).lower() in {"1", "true", "yes", "on"}
            import_kwargs = {
                "overwrite": overwrite,
                "set_as_default": set_as_default,
                "suggested_pack_id": str(session_data.get("suggested_pack_id") or ""),
                "preserve_existing_manual": not overwrite_manual_semantics,
                **self._pack_import_embedding_signature(),
            }
            if overwrite:
                result = await self._run_guarded_pack_file_operation(
                    str(session_data.get("pack_id") or ""),
                    "覆盖安装资源包",
                    import_pack_archive,
                    archive_path,
                    **import_kwargs,
                )
            else:
                result = await self._run_guarded_runtime_file_operation(
                    "安装资源包",
                    import_pack_archive,
                    archive_path,
                    **import_kwargs,
                )
            archive_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            self._reload_personas()
            result.update(
                self._semantic_rebuild_guidance(str(result.get("pack_id") or ""))
            )
            return jsonify({"message": "表情包导入成功", **result}), 200
        except RuntimeError as exc:
            return jsonify({"message": str(exc)}), 409
        except FileExistsError as exc:
            return jsonify({"message": str(exc)}), 409
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("确认导入表情包失败: %s", exc, exc_info=True)
            return jsonify({"message": "确认导入表情包失败"}), 500

    async def _api_uninstall_pack(self):
        try:
            data = await request.get_json()
            pack_id = str((data or {}).get("pack_id") or "").strip()
            if not pack_id:
                return jsonify({"message": "pack_id 不能为空"}), 400
            result = await self._run_guarded_pack_file_operation(
                pack_id,
                "卸载资源包",
                uninstall_pack,
                pack_id,
            )
            self._reload_personas()
            return jsonify({"message": "卸载成功", **result}), 200
        except FileNotFoundError as e:
            return jsonify({"message": str(e)}), 404
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except ValueError as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"卸载表情包失败: {e}", exc_info=True)
            return jsonify({"message": f"卸载表情包失败: {str(e)}"}), 500

    async def _api_create_local_pack(self):
        """Create and register an empty local pack.

        Returns:
            The created pack identity or a validation error.
        """
        try:
            data = await request.get_json() or {}
            result = await self._run_guarded_runtime_file_operation(
                "创建表情包",
                create_local_pack,
                data.get("name"),
                data.get("description", ""),
            )
            self._reload_personas()
            return jsonify(result), 200
        except (ValueError, RuntimeError) as exc:
            return jsonify({"message": str(exc)}), 400
        except Exception as exc:
            logger.error("Creating local pack failed: %s", exc, exc_info=True)
            return jsonify({"message": "创建失败，请重试"}), 500
