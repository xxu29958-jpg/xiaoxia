from __future__ import annotations

import asyncio
import hashlib
import random
import shutil
import tempfile
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.core.message.components import Image

from ..utils import probability_hit
from .auto_collect_support import (
    MAX_IMAGE_BYTES,
    MAX_OBSERVATION_MESSAGE_IDS,
    MAX_OBSERVATIONS,
    OBSERVATION_TTL_SECONDS,
    AutoCollectJob,
)


class AutoCollectManagerCapture:
    """Capture; shared runtime resources belong to the facade."""

    async def _snapshot_message_images(
        self,
        images: list[tuple[Image, bool]],
        target_pack_id: str,
        categories: dict[str, str],
        source_kind: str,
        source_id: str,
        message_key: str,
    ) -> bool:
        """依次提交同一消息的候选图片，让来源冷却只拦截后续消息。

        Args:
            images: 平台标记的图片优先，其余按消息顺序排列的候选图片。
            target_pack_id: 接收候选图片的表情包 ID。
            categories: 目标表情包的分类描述。
            source_kind: 群聊或个人消息来源类型。
            source_id: 用于冷却检查的来源 ID。
            message_key: 用于排除平台重复投递的消息标识。

        Returns:
            至少有一张图片进入识别队列时返回 True。
        """
        queued = False
        message_seen: set[str] = set()
        for image, marked in images:
            accepted = await self._snapshot_and_enqueue(
                image,
                target_pack_id,
                categories,
                source_kind,
                source_id,
                same_message=queued,
                marked=marked,
                message_key=message_key,
                message_seen=message_seen,
            )
            queued = queued or accepted
        return queued

    def _sample_observation(self, record_id: str, message_key: str) -> bool:
        """记录图片在不同消息中的出现次数，并逐次提高识别概率。

        Args:
            record_id: 目标表情包与图片摘要组成的键。
            message_key: 有值时用于排除同一平台消息的重复投递。

        Returns:
            图片已命中采样或此前已经达到处理门槛时返回 True。
        """
        now = time.time()
        observations = self._state.setdefault("observations", {})
        if not isinstance(observations, dict):
            observations = {}
            self._state["observations"] = observations
        for key, item in list(observations.items()):
            last_seen = item.get("last_seen") if isinstance(item, dict) else None
            if (
                not isinstance(last_seen, (int, float))
                or now - last_seen > OBSERVATION_TTL_SECONDS
            ):
                del observations[key]
        item = observations.get(record_id)
        if not isinstance(item, dict):
            item = {"count": 0, "last_seen": now, "message_ids": [], "triggered": False}
            observations[record_id] = item
        message_ids = item.get("message_ids")
        if not isinstance(message_ids, list):
            message_ids = []
        if message_key and message_key in message_ids:
            self.counters["redelivered"] = self.counters.get("redelivered", 0) + 1
            logger.info(
                "[meme_manager] 自动收集跳过图片：平台重复投递同一消息，图片摘要：%s",
                record_id.rsplit(":", 1)[-1][:12],
            )
            return False
        if message_key:
            message_ids.append(message_key)
        item["message_ids"] = message_ids[-MAX_OBSERVATION_MESSAGE_IDS:]
        count = item.get("count")
        item["count"] = min(8, max(0, count if isinstance(count, int) else 0) + 1)
        item["last_seen"] = now
        digest = record_id.rsplit(":", 1)[-1]
        if item.get("triggered"):
            logger.info(
                "[meme_manager] 自动收集图片此前已命中采样，继续尝试处理；第 %s 次，图片摘要：%s",
                item["count"],
                digest[:12],
            )
        elif item["count"] == 1:
            self.counters["first_observation"] = (
                self.counters.get("first_observation", 0) + 1
            )
            logger.info(
                "[meme_manager] 自动收集首次见到图片：已记录，等待再次出现；图片摘要：%s",
                digest[:12],
            )
        else:
            probability = (
                1.0 if item["count"] >= 8 else 1.0 - 0.55 * 0.9 ** (item["count"] - 2)
            )
            if item["count"] >= 8 or random.random() < probability:
                item["triggered"] = True
                self.counters["progressive_hit"] = (
                    self.counters.get("progressive_hit", 0) + 1
                )
                logger.info(
                    "[meme_manager] 自动收集重复图片命中采样：第 %s 次，本次概率 %.1f%%，图片摘要：%s",
                    item["count"],
                    probability * 100,
                    digest[:12],
                )
            else:
                self.counters["progressive_miss"] = (
                    self.counters.get("progressive_miss", 0) + 1
                )
                logger.info(
                    "[meme_manager] 自动收集重复图片未命中采样：第 %s 次，本次概率 %.1f%%，图片摘要：%s",
                    item["count"],
                    probability * 100,
                    digest[:12],
                )
        if len(observations) > MAX_OBSERVATIONS:
            oldest = sorted(
                observations, key=lambda key: observations[key]["last_seen"]
            )
            for key in oldest[: len(observations) - MAX_OBSERVATIONS]:
                del observations[key]
        self._save_json(self.paths.state, self._state)
        return bool(item.get("triggered"))

    async def _snapshot_and_enqueue(
        self,
        image: Image,
        target_pack_id: str,
        categories: dict[str, str],
        source_kind: str,
        source_id: str,
        *,
        same_message: bool = False,
        marked: bool = True,
        message_key: str = "",
        message_seen: set[str] | None = None,
    ) -> bool:
        """先为候选图片创建快照并预留摘要，再检查冷却时间。

        Args:
            image: 与消息事件独立的候选图片组件。
            target_pack_id: 接收候选图片的表情包 ID。
            categories: 目标表情包的分类描述。
            source_kind: 群聊或个人消息来源类型。
            source_id: 用于冷却检查的来源 ID。
            same_message: 同一消息已有图片入队时跳过来源冷却。
            marked: 平台是否明确将图片标为表情。
            message_key: 用于排除平台重复投递的消息标识。
            message_seen: 同一消息已经处理过的图片摘要。

        Returns:
            快照是否成功进入识别队列。
        """
        snapshot_path: Path | None = None
        copy_task: asyncio.Task | None = None
        record_id: str | None = None
        reserved = False
        queued = False
        try:
            local_path = Path(
                await asyncio.wait_for(image.convert_to_file_path(), timeout=30)
            )
            file_size = (await asyncio.to_thread(local_path.stat)).st_size
            if file_size > MAX_IMAGE_BYTES:
                self.counters["snapshot_failed"] = (
                    self.counters.get("snapshot_failed", 0) + 1
                )
                logger.warning("[meme_manager] 自动收集跳过图片：文件超过 20 MiB 限制")
                return False
            self.paths.temporary.mkdir(parents=True, exist_ok=True)
            snapshot_file = tempfile.NamedTemporaryFile(
                prefix="queued_",
                suffix=local_path.suffix,
                dir=self.paths.temporary,
                delete=False,
            )
            snapshot_path = Path(snapshot_file.name)
            snapshot_file.close()
            copy_task = asyncio.create_task(
                asyncio.to_thread(shutil.copyfile, local_path, snapshot_path)
            )
            # 取消线程任务不会停止复制，删除快照前必须等待复制结束。
            await asyncio.shield(copy_task)
            if (await asyncio.to_thread(snapshot_path.stat)).st_size > MAX_IMAGE_BYTES:
                self.counters["snapshot_failed"] = (
                    self.counters.get("snapshot_failed", 0) + 1
                )
                logger.warning("[meme_manager] 自动收集跳过图片：快照超过 20 MiB 限制")
                return False
            copy_task = asyncio.create_task(asyncio.to_thread(snapshot_path.read_bytes))
            digest = hashlib.sha256(await asyncio.shield(copy_task)).hexdigest()
            record_id = f"{target_pack_id}:{digest}"
            if message_seen is not None:
                if record_id in message_seen:
                    self.counters["same_message_duplicate"] = (
                        self.counters.get("same_message_duplicate", 0) + 1
                    )
                    logger.info(
                        "[meme_manager] 自动收集跳过图片：同一消息已有相同图片，表情包：%s，图片摘要：%s",
                        target_pack_id,
                        digest[:12],
                    )
                    return False
                message_seen.add(record_id)
            pack_duplicate = await asyncio.to_thread(
                self._pack_contains_digest, target_pack_id, digest
            )
            metadata = self._load_json(self.paths.metadata, {"items": {}})
            if (
                pack_duplicate
                or record_id in self._inflight
                or record_id in metadata.get("items", {})
                or digest in self._state.get("rejected", {}).get(target_pack_id, [])
            ):
                self.counters["duplicate_or_rejected"] = (
                    self.counters.get("duplicate_or_rejected", 0) + 1
                )
                if pack_duplicate:
                    reason = "表情包中已有相同图片"
                elif record_id in self._inflight:
                    reason = "相同图片正在处理"
                elif record_id in metadata.get("items", {}):
                    reason = "相同图片已在待审箱"
                else:
                    reason = "图片已在拒绝名单"
                logger.info(
                    "[meme_manager] 自动收集跳过图片：%s，表情包：%s，图片摘要：%s",
                    reason,
                    target_pack_id,
                    digest[:12],
                )
                return False
            if marked:
                if not probability_hit(self.sampling_probability):
                    self.counters["sampling"] = self.counters.get("sampling", 0) + 1
                    logger.info(
                        "[meme_manager] 自动收集跳过图片：平台标记的表情未命中抽样概率，来源：%s:%s，图片摘要：%s",
                        source_kind,
                        source_id,
                        digest[:12],
                    )
                    return False
            else:
                cache_key = self._cache_key(digest, target_pack_id, categories)
                decisions = self._state.get("decisions", {})
                decision = (
                    decisions.get(cache_key) if isinstance(decisions, dict) else None
                )
                if isinstance(decision, dict) and (
                    not bool(decision.get("is_meme"))
                    or float(decision.get("meme_confidence", 0) or 0)
                    < self.min_meme_confidence
                ):
                    self.counters["model_rejected_cached"] = (
                        self.counters.get("model_rejected_cached", 0) + 1
                    )
                    logger.info(
                        "[meme_manager] 自动收集跳过图片：视觉模型已有否定判断，表情包：%s，图片摘要：%s",
                        target_pack_id,
                        digest[:12],
                    )
                    return False
                if not self._sample_observation(record_id, message_key):
                    return False
            if not self._ready:
                self.counters["not_ready"] = self.counters.get("not_ready", 0) + 1
                logger.info(
                    "[meme_manager] 自动收集跳过图片：快照完成后后台任务已停止，表情包：%s，图片摘要：%s",
                    target_pack_id,
                    digest[:12],
                )
                return False
            cooldown_key = f"{source_kind}:{source_id}"
            now = time.monotonic()
            if (
                not same_message
                and now - self._cooldowns.get(cooldown_key, 0.0) < self.cooldown_seconds
            ):
                self.counters["cooldown"] = self.counters.get("cooldown", 0) + 1
                logger.info(
                    "[meme_manager] 自动收集跳过图片：来源仍在冷却时间内，来源：%s:%s，表情包：%s",
                    source_kind,
                    source_id,
                    target_pack_id,
                )
                return False
            self._inflight.add(record_id)
            reserved = True
            self.queue.put_nowait(
                AutoCollectJob(
                    snapshot_path=snapshot_path,
                    target_pack_id=target_pack_id,
                    categories=categories,
                    source_kind=source_kind,
                    source_id=source_id,
                    digest=digest,
                )
            )
            queued = True
            self._cooldowns[cooldown_key] = now
            return True
        except asyncio.CancelledError:
            if copy_task is not None and not copy_task.done():
                await asyncio.gather(copy_task, return_exceptions=True)
            raise
        except asyncio.QueueFull:
            self.counters["queue_full"] = self.counters.get("queue_full", 0) + 1
            logger.info(
                "[meme_manager] 自动收集跳过图片：入队时队列已满，表情包：%s",
                target_pack_id,
            )
            return False
        except Exception as exc:
            self.counters["snapshot_failed"] = (
                self.counters.get("snapshot_failed", 0) + 1
            )
            logger.warning("[meme_manager] 自动收集图片快照失败：%s", exc)
            return False
        finally:
            if not queued:
                if reserved and record_id is not None:
                    self._inflight.discard(record_id)
                if snapshot_path is not None:
                    snapshot_path.unlink(missing_ok=True)
