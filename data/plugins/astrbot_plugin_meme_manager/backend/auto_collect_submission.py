from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger
from astrbot.core.message.components import Image

from .auto_collect_support import (
    QUEUE_SIZE,
)
from .packs.protocol import validate_pack_id
from .packs.resolver import load_pack_category_mapping


class AutoCollectManagerSubmission:
    """Submission; shared runtime resources belong to the facade."""

    def _source_allowed(self, event: Any) -> tuple[bool, str, str]:
        """检查消息是否匹配配置的群聊或个人来源范围。

        Args:
            event: 当前 AstrBot 消息事件。

        Returns:
            是否允许该来源、来源类型和来源 ID 组成的元组。
        """
        private = bool(event.is_private_chat())
        source_kind = "user" if private else "group"
        source_id = str(
            event.get_sender_id() if private else event.get_group_id()
        ).strip()
        if not source_id:
            source_id = str(event.get_session_id() or "").strip()
        if not self.scope:
            return True, source_kind, source_id
        candidates = {
            source_id,
            f"{source_kind}:{source_id}",
            str(getattr(event, "unified_msg_origin", "") or "").strip(),
        }
        return bool(self.scope.intersection(candidates)), source_kind, source_id

    async def submit(self, event: Any) -> bool:
        """过滤消息，并将符合条件的直接图片按单条消息上限加入队列。

        Args:
            event: 当前 AstrBot 消息事件。

        Returns:
            成功加入任务队列时返回 True。
        """
        if not self.enabled:
            return False
        images = [
            component
            for component in event.message_obj.message
            if isinstance(component, Image)
        ]
        if not images:
            return False
        allowed, source_kind, source_id = self._source_allowed(event)
        if not self._ready:
            self.counters["not_ready"] = self.counters.get("not_ready", 0) + 1
            logger.info(
                "[meme_manager] 自动收集跳过图片：后台任务尚未就绪，来源：%s:%s",
                source_kind,
                source_id,
            )
            return False
        if not allowed:
            self.counters["scope"] = self.counters.get("scope", 0) + 1
            logger.info(
                "[meme_manager] 自动收集跳过图片：消息来源不在收集范围内，来源：%s:%s",
                source_kind,
                source_id,
            )
            return False
        candidates = [(image, False) for image in images]
        raw_message = getattr(event.message_obj, "raw_message", None)
        if isinstance(raw_message, Mapping):
            raw_segments = raw_message.get("message")
            if isinstance(raw_segments, list):
                raw_image_data = [
                    segment.get("data")
                    for segment in raw_segments
                    if isinstance(segment, Mapping)
                    and segment.get("type") == "image"
                    and isinstance(segment.get("data"), Mapping)
                ]
                if len(raw_image_data) == len(images):
                    # NapCat 仅在原始 OneBot 消息段中提供图片分类。
                    # AstrBot 的 Image 组件会丢弃这些扩展字段，因此按位置对应图片。
                    marked_images = []
                    uncertain_images = []
                    for image, data in zip(images, raw_image_data, strict=True):
                        summary = str(data.get("summary") or "").strip()
                        explicit_summary = summary in {
                            "动画表情",
                            "[动画表情]",
                            "[表情]",
                        }
                        try:
                            image_sub_type = int(data.get("sub_type"))
                        except (TypeError, ValueError):
                            image_sub_type = None
                        if (
                            image_sub_type == 1
                            or bool(data.get("emoji_id"))
                            or bool(data.get("emoji_package_id"))
                            or explicit_summary
                        ):
                            marked_images.append((image, True))
                        else:
                            uncertain_images.append((image, False))
                    candidates = marked_images + uncertain_images
        if self.queue.qsize() + len(self._snapshot_tasks) >= QUEUE_SIZE:
            self.counters["queue_full"] = self.counters.get("queue_full", 0) + 1
            logger.info(
                "[meme_manager] 自动收集跳过图片：待处理队列已满，来源：%s:%s",
                source_kind,
                source_id,
            )
            return False

        try:
            if self.target_pack_id:
                target_pack_id = validate_pack_id(
                    self.target_pack_id, "自动收集目标表情包"
                )
                if not (self.paths.packs / target_pack_id).is_dir():
                    logger.warning(
                        "[meme_manager] 自动收集目标表情包不存在：%s",
                        target_pack_id,
                    )
                    return False
                categories = load_pack_category_mapping(target_pack_id)
            else:
                context = self.plugin._resolve_runtime_pack_context(event=event)
                target_pack_id = str(context.get("pack_id") or "").strip()
                categories = dict(context.get("category_mapping") or {})
        except Exception as exc:
            logger.warning("[meme_manager] 解析自动收集目标失败：%s", exc)
            return False
        if not target_pack_id or not categories:
            self.counters["no_categories"] = self.counters.get("no_categories", 0) + 1
            logger.warning(
                "[meme_manager] 自动收集跳过图片：目标表情包没有可用分类，表情包：%s",
                target_pack_id,
            )
            return False

        if len(candidates) > self.max_images_per_message:
            self.counters["message_limit"] = self.counters.get("message_limit", 0) + (
                len(candidates) - self.max_images_per_message
            )
            logger.info(
                "[meme_manager] 自动收集单条消息图片上限为 %s 张，跳过其余 %s 张，来源：%s:%s",
                self.max_images_per_message,
                len(candidates) - self.max_images_per_message,
                source_kind,
                source_id,
            )
        selected_images = candidates[: self.max_images_per_message]
        message_id = str(getattr(event.message_obj, "message_id", "") or "").strip()
        if not message_id and isinstance(raw_message, Mapping):
            message_id = str(raw_message.get("message_id") or "").strip()
        origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
        origin = origin or f"{source_kind}:{source_id}"
        message_key = f"{origin}:{message_id}" if message_id else ""
        snapshot = self._snapshot_message_images(
            selected_images,
            target_pack_id,
            categories,
            source_kind,
            source_id,
            message_key,
        )
        if any(
            str(image.url or image.file or "").startswith(("https://", "http://"))
            for image, _ in selected_images
        ):
            task = asyncio.create_task(
                snapshot, name="meme_manager_auto_collect_snapshot"
            )
            self._snapshot_tasks.add(task)
            task.add_done_callback(self._snapshot_tasks.discard)
            return True
        return await snapshot
