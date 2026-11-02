from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .. import config as pack_paths
from .auto_collect_capture import AutoCollectManagerCapture
from .auto_collect_classification import AutoCollectManagerClassification
from .auto_collect_inbox import AutoCollectManagerInbox
from .auto_collect_index import AutoCollectImageIndex
from .auto_collect_processing import AutoCollectManagerProcessing
from .auto_collect_storage import AutoCollectManagerStorage
from .auto_collect_submission import AutoCollectManagerSubmission
from .auto_collect_support import (
    AUTO_COLLECT_PROMPT_VERSION as AUTO_COLLECT_PROMPT_VERSION,
)
from .auto_collect_support import (
    AUTO_COLLECT_SCHEMA_VERSION as AUTO_COLLECT_SCHEMA_VERSION,
)
from .auto_collect_support import (
    AUTO_COLLECT_SYSTEM_PROMPT as AUTO_COLLECT_SYSTEM_PROMPT,
)
from .auto_collect_support import FORMAT_EXTENSIONS as FORMAT_EXTENSIONS
from .auto_collect_support import MAX_DECISION_CACHE_ITEMS as MAX_DECISION_CACHE_ITEMS
from .auto_collect_support import MAX_IMAGE_BYTES as MAX_IMAGE_BYTES
from .auto_collect_support import MAX_IMAGE_PIXELS as MAX_IMAGE_PIXELS
from .auto_collect_support import (
    MAX_OBSERVATION_MESSAGE_IDS as MAX_OBSERVATION_MESSAGE_IDS,
)
from .auto_collect_support import MAX_OBSERVATIONS as MAX_OBSERVATIONS
from .auto_collect_support import OBSERVATION_TTL_SECONDS as OBSERVATION_TTL_SECONDS
from .auto_collect_support import QUEUE_SIZE as QUEUE_SIZE
from .auto_collect_support import AutoCollectJob as AutoCollectJob
from .auto_collect_support import CollectionPaths
from .auto_collect_worker import AutoCollectManagerWorker


class AutoCollectManager(
    AutoCollectManagerSubmission,
    AutoCollectManagerCapture,
    AutoCollectManagerWorker,
    AutoCollectManagerClassification,
    AutoCollectManagerStorage,
    AutoCollectManagerProcessing,
    AutoCollectManagerInbox,
):
    """执行有界的后台视觉分类，并安全地收集图片。"""

    def __init__(
        self,
        plugin: Any,
        config: dict[str, Any],
        *,
        paths: CollectionPaths | None = None,
    ):
        """初始化运行时状态，但不启动后台任务。

        Args:
            plugin: MemeSender 插件实例。
            config: 已规范化的自动收集配置。
        """
        self.paths = paths or CollectionPaths.for_root(pack_paths.PLUGIN_DATA_DIR)
        self.image_index = AutoCollectImageIndex()
        self.plugin = plugin
        self.enabled = bool(config.get("enabled", False))
        self.manual_review = bool(config.get("manual_review", True))
        self.pending_limit = max(1, int(config.get("pending_limit", 200) or 200))
        self.counters: dict[str, int] = {}
        self._inflight: set[str] = set()
        self.vision_provider_id = str(config.get("vision_provider_id") or "").strip()
        self.scope = {
            str(value).strip()
            for value in config.get("scope", [])
            if str(value).strip()
        }
        self.target_pack_id = str(config.get("target_pack_id") or "").strip()
        self.sampling_probability = max(
            0, min(100, int(config.get("sampling_probability", 100) or 0))
        )
        self.cooldown_seconds = max(
            0, min(3600, int(config.get("cooldown_seconds", 20) or 0))
        )
        self.daily_recognition_limit = max(
            0, int(config.get("daily_recognition_limit", 100) or 0)
        )
        self.max_images_per_message = max(
            1, min(10, int(config.get("max_images_per_message", 1) or 1))
        )
        self.min_meme_confidence = max(
            0.0, min(1.0, float(config.get("min_meme_confidence", 0.85) or 0))
        )
        self.min_category_confidence = max(
            0.0,
            min(1.0, float(config.get("min_category_confidence", 0.65) or 0)),
        )
        self.queue: asyncio.Queue[AutoCollectJob] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._worker_task: asyncio.Task | None = None
        self._snapshot_tasks: set[asyncio.Task] = set()
        self._ready = False
        self._cooldowns: dict[str, float] = {}
        self._inbox_lock = asyncio.Lock()
        self._state = self._load_json(
            self.paths.state,
            {"schema_version": AUTO_COLLECT_SCHEMA_VERSION, "decisions": {}},
        )

    @staticmethod
    def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        """加载 JSON 对象，失败时返回独立的默认值副本。

        Args:
            path: JSON 文件路径。
            default: 加载失败时使用的默认映射。

        Returns:
            解析后的映射，或默认映射的副本。
        """
        try:
            with path.open(encoding="utf-8") as file_obj:
                value = json.load(file_obj)
            return value if isinstance(value, dict) else dict(default)
        except Exception:
            return dict(default)

    @staticmethod
    def _save_json(path: Path, value: dict[str, Any]) -> None:
        """以原子方式保存 JSON 对象。

        Args:
            path: 目标 JSON 文件路径。
            value: 可序列化的映射。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as file_obj:
            json.dump(value, file_obj, ensure_ascii=False, indent=2)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, path)

    async def start(self) -> None:
        """校验已配置的视觉模型，并启动一个后台工作任务。"""
        if not self.enabled or self._worker_task is not None:
            return
        if not self.vision_provider_id:
            logger.warning("[meme_manager] 已启用自动收集，但尚未选择视觉模型")
            return
        provider = self.plugin.context.get_provider_by_id(self.vision_provider_id)
        if provider is None:
            logger.info(
                "[meme_manager] 自动收集视觉模型尚未就绪，等待加载后重试：%s",
                self.vision_provider_id,
            )
            return
        provider_config = getattr(provider, "provider_config", {})
        modalities = (
            provider_config.get("modalities")
            if isinstance(provider_config, dict)
            else None
        )
        if (
            isinstance(modalities, list)
            and modalities
            and "image" not in {str(item).strip().lower() for item in modalities}
        ):
            logger.warning(
                "[meme_manager] 自动收集视觉模型不支持图片输入：%s",
                self.vision_provider_id,
            )
            return
        self._ready = True
        self._worker_task = asyncio.create_task(
            self._worker(), name="meme_manager_auto_collect"
        )
        logger.info(
            "[meme_manager] 自动收集后台任务已启动，视觉模型：%s",
            self.vision_provider_id,
        )

    async def close(self) -> None:
        """停止后台任务，并等待取消清理完成。"""
        self._ready = False
        snapshot_tasks = tuple(self._snapshot_tasks)
        for snapshot_task in snapshot_tasks:
            snapshot_task.cancel()
        if snapshot_tasks:
            await asyncio.gather(*snapshot_tasks, return_exceptions=True)
        self._snapshot_tasks.clear()
        task = self._worker_task
        self._worker_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        while True:
            try:
                job = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._inflight.discard(f"{job.target_pack_id}:{job.digest}")
            job.snapshot_path.unlink(missing_ok=True)
            self.queue.task_done()
