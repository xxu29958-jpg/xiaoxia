import asyncio
import secrets
import time

from quart import jsonify, request

from astrbot.api import logger

from ..backend.packs.storage import (
    InstallCancelledError,
    fetch_and_cache_community_index,
    find_cached_pack_entry,
    install_first_official_pack_from_index,
    install_pack_from_github_source,
    load_cached_community_index,
)
from ..config import (
    COMMUNITY_INDEX_URL,
)
from .web_api_support import (
    COMMUNITY_INSTALL_JOB_TTL_SECONDS,
)


class WebAPIMixinCommunity:
    """Community; shared runtime resources belong to the facade."""

    async def _api_fetch_community_index(self):
        try:
            index_url = COMMUNITY_INDEX_URL
            cache_data = await asyncio.to_thread(
                fetch_and_cache_community_index,
                index_url,
                github_accelerator_url=self._get_github_accelerator_url(),
            )
            packs = cache_data.get("index", {}).get("packs", [])
            return (
                jsonify(
                    {
                        "message": "社区索引拉取成功",
                        "fetched_at": cache_data.get("fetched_at"),
                        "source_url": cache_data.get("source_url"),
                        "pack_count": len(packs) if isinstance(packs, list) else 0,
                        "index": cache_data.get("index", {}),
                    }
                ),
                200,
            )
        except ValueError as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"拉取社区索引失败: {e}", exc_info=True)
            return jsonify({"message": f"拉取社区索引失败: {str(e)}"}), 500

    async def _api_get_cached_community_index(self):
        try:
            cache_data = load_cached_community_index()
            packs = cache_data.get("index", {}).get("packs", [])
            return (
                jsonify(
                    {
                        "fetched_at": cache_data.get("fetched_at"),
                        "source_url": cache_data.get("source_url"),
                        "pack_count": len(packs) if isinstance(packs, list) else 0,
                        "index": cache_data.get("index", {}),
                    }
                ),
                200,
            )
        except FileNotFoundError as e:
            return jsonify({"message": str(e)}), 404
        except ValueError as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"读取社区索引缓存失败: {e}", exc_info=True)
            return jsonify({"message": f"读取社区索引缓存失败: {str(e)}"}), 500

    async def _api_install_community_pack(self):
        data = None
        try:
            data = await request.get_json()
            payload = data or {}
            overwrite = bool(payload.get("overwrite", False))
            set_as_default = bool(payload.get("set_as_default", False))
            pack_id = str(payload.get("pack_id") or "").strip()

            source = payload.get("source")
            if not isinstance(source, dict):
                if not pack_id:
                    return (
                        jsonify(
                            {
                                "message": "请提供 source 或 pack_id（用于从缓存索引安装）"
                            }
                        ),
                        400,
                    )
                source = find_cached_pack_entry(pack_id).get("source")
                if not isinstance(source, dict):
                    return jsonify({"message": "缓存条目缺少 source 信息"}), 400

            result = await self._run_guarded_runtime_file_operation(
                "安装社区资源包",
                install_pack_from_github_source,
                source=source,
                overwrite=overwrite,
                set_as_default=set_as_default,
                github_accelerator_url=self._get_github_accelerator_url(),
            )
            self._reload_personas()
            return jsonify({"message": "社区表情包安装成功", **result}), 200
        except FileExistsError as e:
            return jsonify({"message": str(e)}), 409
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except (FileNotFoundError, ValueError) as e:
            logger.warning(
                "社区表情包安装参数或资源错误: %s | pack_id=%s | payload_source=%s",
                e,
                str((data or {}).get("pack_id") or "").strip(),
                bool(isinstance((data or {}).get("source"), dict)),
            )
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"安装社区表情包失败: {e}", exc_info=True)
            return jsonify({"message": f"安装社区表情包失败: {str(e)}"}), 500

    async def _api_start_community_pack_install(self):
        """创建后台安装任务，供资源广场轮询真实下载进度。"""
        try:
            payload = (await request.get_json()) or {}
            source = payload.get("source")
            pack_id = str(payload.get("pack_id") or "").strip()
            if not isinstance(source, dict):
                if not pack_id:
                    return jsonify({"message": "请提供 source 或 pack_id"}), 400
                source = find_cached_pack_entry(pack_id).get("source")
                if not isinstance(source, dict):
                    return jsonify({"message": "缓存条目缺少 source 信息"}), 400

            jobs = self._community_install_jobs
            now = time.time()
            for stale_job_id, stale_job in list(jobs.items()):
                if (
                    stale_job.get("status") != "running"
                    and now - float(stale_job.get("updated_at") or now)
                    > COMMUNITY_INSTALL_JOB_TTL_SECONDS
                ):
                    jobs.pop(stale_job_id, None)
            running_job = next(
                (
                    job
                    for job in jobs.values()
                    if job.get("status") in {"running", "cancelling"}
                ),
                None,
            )
            if running_job:
                return (
                    jsonify(
                        {
                            "message": "已有表情包正在安装，请稍候",
                            "job_id": running_job.get("job_id"),
                        }
                    ),
                    409,
                )

            job_id = secrets.token_urlsafe(18)
            jobs[job_id] = {
                "job_id": job_id,
                "status": "running",
                "phase": "queued",
                "message": "正在准备安装任务",
                "progress": 0,
                "downloaded_bytes": 0,
                "total_bytes": None,
                "result": None,
                "cancel_requested": False,
                "source_label": str(pack_id or source.get("repo") or ""),
                "created_at": now,
                "updated_at": now,
            }
            task = asyncio.create_task(
                self._run_community_pack_install_job(
                    job_id,
                    source,
                    overwrite=bool(payload.get("overwrite", False)),
                    set_as_default=bool(payload.get("set_as_default", False)),
                )
            )
            self._community_install_tasks.add(task)
            task.add_done_callback(self._community_install_tasks.discard)
            return jsonify({"job_id": job_id}), 202
        except (FileNotFoundError, ValueError) as e:
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"启动社区表情包安装任务失败: {e}", exc_info=True)
            return jsonify({"message": f"启动安装任务失败: {str(e)}"}), 500

    async def _run_community_pack_install_job(
        self,
        job_id: str,
        source: dict,
        overwrite: bool,
        set_as_default: bool,
    ) -> None:
        """运行社区表情包安装任务并维护可轮询状态。

        Args:
            job_id: 安装任务的不可预测标识。
            source: GitHub 来源描述。
            overwrite: 是否覆盖同名表情包。
            set_as_default: 是否将安装结果设为默认表情包。
        """
        job = self._community_install_jobs[job_id]
        phase_messages = {
            "connecting": "正在连接下载源",
            "downloading": "正在下载资源包",
            "extracting": "正在解压资源包",
            "preparing": "正在校验并整理文件",
            "installing": "正在写入表情包",
        }
        phase_progress = {
            "connecting": 3,
            "extracting": 88,
            "preparing": 93,
            "installing": 97,
        }

        def update_progress(
            phase: str, downloaded_bytes: int, total_bytes: int | None
        ) -> None:
            if job.get("cancel_requested"):
                raise InstallCancelledError("安装已取消")
            progress = phase_progress.get(phase, job["progress"])
            if phase == "downloading" and total_bytes:
                progress = 5 + round(min(downloaded_bytes / total_bytes, 1) * 80)
            elif phase == "downloading":
                progress = None
            job.update(
                {
                    "phase": phase,
                    "message": phase_messages.get(phase, "正在安装表情包"),
                    "progress": progress,
                    "downloaded_bytes": downloaded_bytes,
                    "total_bytes": total_bytes,
                    "updated_at": time.time(),
                }
            )

        try:
            result = await self._run_guarded_runtime_file_operation(
                "安装社区资源包",
                install_pack_from_github_source,
                source=source,
                overwrite=overwrite,
                set_as_default=set_as_default,
                github_accelerator_url=self._get_github_accelerator_url(),
                progress_callback=update_progress,
                cancel_check=lambda: bool(job.get("cancel_requested")),
            )
            self._reload_personas()
            job.update(
                {
                    "status": "succeeded",
                    "phase": "completed",
                    "message": "表情包安装完成",
                    "progress": 100,
                    "result": result,
                    "updated_at": time.time(),
                }
            )
        except InstallCancelledError:
            job.update(
                {
                    "status": "cancelled",
                    "phase": "cancelled",
                    "message": "安装已取消",
                    "updated_at": time.time(),
                }
            )
        except asyncio.CancelledError:
            job.update(
                {
                    "status": "cancelled",
                    "phase": "cancelled",
                    "message": "安装任务已停止",
                    "updated_at": time.time(),
                }
            )
            raise
        except Exception as e:
            logger.error(f"社区表情包后台安装失败: {e}", exc_info=True)
            job.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "message": str(e),
                    "updated_at": time.time(),
                }
            )

    async def _api_community_pack_install_status(self):
        """返回指定社区表情包安装任务的当前进度。"""
        job_id = str(request.args.get("job_id") or "").strip()
        if not job_id:
            active_jobs = [
                job
                for job in self._community_install_jobs.values()
                if job.get("status") in {"running", "cancelling"}
            ]
            if not active_jobs:
                return jsonify({"status": "idle"}), 200
            job = max(active_jobs, key=lambda item: float(item.get("created_at") or 0))
            return jsonify(job.copy()), 200
        job = self._community_install_jobs.get(job_id)
        if not job:
            return jsonify({"message": "安装任务不存在或已过期"}), 404
        return jsonify(job.copy()), 200

    async def _api_cancel_community_pack_install(self):
        """请求协作式取消正在运行的社区表情包安装任务。"""
        payload = (await request.get_json()) or {}
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return jsonify({"message": "job_id 不能为空"}), 400
        job = self._community_install_jobs.get(job_id)
        if not job:
            return jsonify({"message": "安装任务不存在或已过期"}), 404
        if job.get("status") not in {"running", "cancelling"}:
            return jsonify(job.copy()), 200
        job.update(
            {
                "status": "cancelling",
                "message": "正在取消安装，请稍候",
                "cancel_requested": True,
                "updated_at": time.time(),
            }
        )
        return jsonify(job.copy()), 202

    async def _api_install_official_first_pack(self):
        data = None
        try:
            data = await request.get_json()
            payload = data or {}
            overwrite = bool(payload.get("overwrite", False))
            set_as_default = bool(payload.get("set_as_default", True))

            result = await self._run_guarded_runtime_file_operation(
                "安装官方资源包",
                install_first_official_pack_from_index,
                index_url=COMMUNITY_INDEX_URL,
                overwrite=overwrite,
                set_as_default=set_as_default,
                github_accelerator_url=self._get_github_accelerator_url(),
            )
            self._reload_personas()
            return jsonify({"message": "官方表情包安装成功", **result}), 200
        except FileExistsError as e:
            return jsonify({"message": str(e)}), 409
        except RuntimeError as e:
            return jsonify({"message": str(e)}), 409
        except (FileNotFoundError, ValueError) as e:
            logger.warning(
                "安装官方首个表情包失败: %s | payload=%s",
                e,
                data,
            )
            return jsonify({"message": str(e)}), 400
        except Exception as e:
            logger.error(f"安装官方首个表情包失败: {e}", exc_info=True)
            return jsonify({"message": f"安装官方首个表情包失败: {str(e)}"}), 500
