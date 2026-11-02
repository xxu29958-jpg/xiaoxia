import asyncio
import mimetypes
import re
import secrets
import time
from pathlib import Path

from quart import jsonify, request

from astrbot.api import logger

from .. import config as pack_paths
from ..backend.packs import storage as pack_storage
from ..backend.packs.categories import is_safe_category_name
from ..backend.packs.protocol import validate_pack_id
from ..backend.packs.storage import (
    InstallCancelledError,
)
from ..backend.packs.updates import (
    apply_update,
    community_update_status,
    plan_update,
    prepare_update,
    restore_update,
)
from .web_api_support import (
    MAX_PREVIEW_IMAGE_BYTES,
)


class WebAPIMixinUpdates:
    """Updates; shared runtime resources belong to the facade."""

    async def _api_community_updates(self):
        """Read fresh catalog installation and update status.

        Returns:
            Per-pack availability and recovery backups.
        """
        try:
            return jsonify(
                {"packs": await asyncio.to_thread(community_update_status)}
            ), 200
        except FileNotFoundError:
            return jsonify({"packs": {}}), 200
        except Exception as exc:
            logger.error("Reading update status failed: %s", exc, exc_info=True)
            return jsonify({"message": "读取更新状态失败，请重试"}), 500

    async def _api_community_update(self):
        """Preview synchronously or start a durable background update operation.

        Returns:
            A preview or a job ID that can be polled after request completion.
        """
        try:
            data = await request.get_json() or {}
            if not isinstance(data, dict):
                raise ValueError("请求必须是对象")
            action = data.get("action")
            if action == "cancel":
                job = getattr(self, "_community_update_jobs", {}).get(
                    str(data.get("job_id") or "")
                )
                if job is None:
                    raise ValueError("更新任务不存在，请刷新广场检查结果")
                if job["action"] != "prepare":
                    raise ValueError("写入或恢复已经开始，不能取消")
                job["cancel_requested"] = True
                if job["state"] == "completed":
                    job["state"] = "cancelled"
                return jsonify({"message": "已请求取消检查更新"}), 200
            if action in {"preview", "restore_preview"}:
                data["preview"] = True
                result = await self._run_guarded_runtime_file_operation(
                    "预览资源包更新",
                    plan_update if action == "preview" else restore_update,
                    data,
                )
                return jsonify(result), 200
            if action not in {"prepare", "apply", "restore"}:
                raise ValueError("更新操作无效")
            jobs = getattr(self, "_community_update_jobs", None)
            if jobs is None:
                jobs = self._community_update_jobs = {}
                self._community_update_tasks = set()
            if any(job["state"] == "running" for job in jobs.values()):
                return jsonify({"message": "已有更新任务正在执行，请稍后重试"}), 409
            for key in list(jobs):
                if time.time() - jobs[key]["created_at"] > 86400:
                    del jobs[key]
            job_id = secrets.token_hex(16)
            jobs[job_id] = {
                "id": job_id,
                "state": "running",
                "created_at": time.time(),
                "action": action,
            }
            task = asyncio.create_task(self._run_community_update_job(job_id, data))
            self._community_update_tasks.add(task)
            task.add_done_callback(self._community_update_tasks.discard)
            return jsonify({"job_id": job_id}), 202
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            return jsonify({"message": str(exc)}), 409
        except Exception as exc:
            logger.error("Preparing update request failed: %s", exc, exc_info=True)
            return jsonify({"message": "更新预览失败，请刷新重试"}), 500

    async def _run_community_update_job(self, job_id: str, data: dict):
        """Keep file-operation locks until a background job has finished.

        Args:
            job_id: Server-generated identifier for polling.
            data: Validated action and update selections.
        """
        job = self._community_update_jobs[job_id]
        try:
            if data["action"] == "prepare":
                pack_id = str(data.get("pack_id") or "")
                result = await self._run_guarded_pack_file_operation(
                    pack_id,
                    "准备资源包更新",
                    prepare_update,
                    pack_id,
                    github_accelerator_url=self._get_github_accelerator_url(),
                    cancel_check=lambda: bool(job.get("cancel_requested")),
                )
            else:
                data["preview"] = False
                result = await self._run_guarded_runtime_file_operation(
                    "更新或恢复资源包",
                    apply_update if data["action"] == "apply" else restore_update,
                    data,
                )
                self._reload_personas()
            if job.get("cancel_requested"):
                job.update(state="cancelled", message="已取消检查更新")
            else:
                job.update(state="completed", result=result)
        except InstallCancelledError:
            job.update(state="cancelled", message="已取消检查更新")
        except Exception as exc:
            logger.error("Community update job failed: %s", exc, exc_info=True)
            job.update(state="failed", message=str(exc))

    async def _api_community_update_status(self):
        """Return a running or completed update job.

        Returns:
            Current job status, or a not-found response after expiry/restart.
        """
        job = getattr(self, "_community_update_jobs", {}).get(
            str(request.args.get("job_id") or "")
        )
        if job is None:
            return jsonify({"message": "更新任务不存在，请刷新广场检查结果"}), 404
        return jsonify(job), 200

    async def _api_community_update_image_data(self):
        """Return a bounded preview from an active update session.

        Returns:
            A preview data URL for one local or upstream image.
        """
        token = str(request.args.get("session") or "")
        side = request.args.get("side")
        relative = str(request.args.get("path") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", token) or side not in {
            "local",
            "upstream",
        }:
            return jsonify({"message": "更新预览参数无效"}), 400
        parts = Path(relative).parts
        if (
            len(parts) != 3
            or parts[0] != "memes"
            or not is_safe_category_name(parts[1])
            or parts[2] in {".", ".."}
            or Path(parts[2]).suffix.lower() not in pack_storage.IMAGE_EXTENSIONS
        ):
            return jsonify({"message": "图片路径无效"}), 400
        session = pack_paths.TEMP_DIR / "community_updates" / token
        record = pack_storage._load_json(session / "session.json", {})
        if not record or time.time() - record.get("created_at", 0) > 14400:
            return jsonify({"message": "更新预览已过期"}), 404
        try:
            pack_id = validate_pack_id(record["pack_id"])
        except (KeyError, ValueError):
            return jsonify({"message": "表情包 ID 无效"}), 400
        root = (
            pack_paths.PACKS_DIR / pack_id if side == "local" else session / "candidate"
        ).resolve()
        if side == "upstream" and relative not in record.get("remote", {}).get(
            "files", {}
        ):
            return jsonify({"message": "图片不存在"}), 404
        file_path = (root / relative).resolve()
        if not file_path.is_relative_to(root) or not file_path.is_file():
            return jsonify({"message": "图片不存在"}), 404
        if file_path.stat().st_size > MAX_PREVIEW_IMAGE_BYTES:
            return jsonify({"message": "图片过大，无法预览"}), 413
        mime_type = (
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )
        if mime_type == "image/gif":
            data_url = await asyncio.to_thread(
                self._build_file_data_url, file_path, mime_type
            )
        else:
            data_url, mime_type = await asyncio.to_thread(
                self._build_preview_data_url, file_path
            )
        response = jsonify({"data_url": data_url, "mime_type": mime_type})
        response.headers["Cache-Control"] = "private, no-store"
        return response
