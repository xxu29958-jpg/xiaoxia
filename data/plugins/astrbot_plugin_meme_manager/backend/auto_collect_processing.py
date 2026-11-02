from __future__ import annotations

import asyncio
import hashlib

from astrbot.api import logger

from .auto_collect_support import (
    AutoCollectJob,
)
from .packs.resolver import get_pack_paths
from .semantic.models import REVIEW_CATEGORY


class AutoCollectManagerProcessing:
    """Processing; shared runtime resources belong to the facade."""

    async def _process_job(self, job: AutoCollectJob) -> None:
        """下载、去重、分类并路由一张队列中的图片。

        Args:
            job: 自动收集任务快照。
        """
        content = await asyncio.to_thread(job.snapshot_path.read_bytes)
        extension = await asyncio.to_thread(self._validate_image, content)
        digest = hashlib.sha256(content).hexdigest()
        metadata = self._load_json(self.paths.metadata, {"items": {}})
        if f"{job.target_pack_id}:{digest}" in metadata.get(
            "items", {}
        ) or digest in self._state.get("rejected", {}).get(job.target_pack_id, []):
            self.counters["duplicate_or_rejected"] = (
                self.counters.get("duplicate_or_rejected", 0) + 1
            )
            reason = (
                "图片已在待审箱"
                if f"{job.target_pack_id}:{digest}" in metadata.get("items", {})
                else "图片已在拒绝名单"
            )
            logger.info(
                "[meme_manager] 自动收集跳过图片：%s，表情包：%s，图片摘要：%s",
                reason,
                job.target_pack_id,
                digest[:12],
            )
            return
        if (
            sum(
                isinstance(item, dict)
                and item.get("target_pack_id") == job.target_pack_id
                for item in metadata.get("items", {}).values()
            )
            >= self.pending_limit
        ):
            self.counters["inbox_full"] = self.counters.get("inbox_full", 0) + 1
            logger.info(
                "[meme_manager] 自动收集跳过图片：待审箱已满，表情包：%s，图片摘要：%s",
                job.target_pack_id,
                digest[:12],
            )
            return
        if await asyncio.to_thread(
            self._pack_contains_digest, job.target_pack_id, digest
        ):
            self.counters["duplicate_or_rejected"] = (
                self.counters.get("duplicate_or_rejected", 0) + 1
            )
            logger.info(
                "[meme_manager] 自动收集跳过图片：表情包中已有相同图片，表情包：%s，图片摘要：%s",
                job.target_pack_id,
                digest[:12],
            )
            return

        cache_key = self._cache_key(digest, job.target_pack_id, job.categories)
        decisions = self._state.get("decisions", {})
        decision = decisions.get(cache_key) if isinstance(decisions, dict) else None
        if not isinstance(decision, dict):
            if not self._daily_call_available():
                self.counters["quota"] = self.counters.get("quota", 0) + 1
                logger.info(
                    "[meme_manager] 自动收集跳过图片：已达到每日识别上限，表情包：%s，图片摘要：%s",
                    job.target_pack_id,
                    digest[:12],
                )
                return
            decision = await self._classify(content, extension, job.categories)
            self._remember_decision(cache_key, decision)
        if (
            not bool(decision.get("is_meme"))
            or float(decision.get("meme_confidence", 0) or 0) < self.min_meme_confidence
        ):
            self.counters["model_rejected"] = self.counters.get("model_rejected", 0) + 1
            reason = (
                "视觉模型判定不是表情包"
                if not bool(decision.get("is_meme"))
                else "视觉模型的表情包置信度不足"
            )
            logger.info(
                "[meme_manager] 自动收集跳过图片：%s，表情包：%s，图片摘要：%s",
                reason,
                job.target_pack_id,
                digest[:12],
            )
            return
        category = str(decision.get("category") or "").strip()
        if category not in job.categories or (
            not self.manual_review
            and float(decision.get("category_confidence", 0) or 0)
            < self.min_category_confidence
        ):
            category = REVIEW_CATEGORY
        match = await asyncio.to_thread(
            self.image_index.lookup,
            get_pack_paths(job.target_pack_id)["memes_dir"],
            digest,
            job.snapshot_path,
        )
        if match.similar_path is not None:
            decision = {
                **decision,
                "near_duplicate": str(
                    match.similar_path.relative_to(
                        get_pack_paths(job.target_pack_id)["memes_dir"]
                    )
                ).replace("\\", "/"),
            }
        await self._save_to_inbox(job, content, digest, extension, category, decision)
        if not self.manual_review:
            try:
                await self.accept_pending(
                    job.target_pack_id,
                    [{"id": f"{job.target_pack_id}:{digest}"}],
                    manual_confirmed=False,
                )
            except RuntimeError:
                self.counters["pack_busy"] = self.counters.get("pack_busy", 0) + 1
                logger.info("[meme_manager] 表情包正在处理，已收集图片保留在待审箱")
