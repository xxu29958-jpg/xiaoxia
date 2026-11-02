from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .auto_collect_support import (
    AUTO_COLLECT_SCHEMA_VERSION,
    AutoCollectJob,
)
from .packs.categories import is_safe_category_name
from .packs.protocol import validate_pack_id
from .packs.resolver import get_pack_paths


class AutoCollectManagerStorage:
    """Storage; shared runtime resources belong to the facade."""

    def _pack_contains_digest(self, pack_id: str, digest: str) -> bool:
        """检查表情包内是否已有内容完全相同的受支持图片。

        Args:
            pack_id: 目标表情包 ID。
            digest: 要查找的 SHA-256 摘要。

        Returns:
            表情包内已存在相同字节内容时返回 True。
        """
        return (
            self.image_index.lookup(
                get_pack_paths(pack_id)["memes_dir"], digest
            ).exact_path
            is not None
        )

    def _write_pack_image(
        self,
        pack_id: str,
        category: str,
        content: bytes,
        digest: str,
        extension: str,
    ) -> Path | None:
        """通过原子重命名将图片写入指定表情包。

        Args:
            pack_id: 有效的目标表情包 ID。
            category: 安全的分类名。
            content: 已通过校验的图片字节。
            digest: SHA-256 摘要。
            extension: 可信的文件扩展名。

        Returns:
            保存后的路径；图片已存在时返回 ``None``。

        Raises:
            ValueError: 目标表情包或分类路径不安全。
        """
        pack_id = validate_pack_id(pack_id, "自动收集目标表情包")
        if not is_safe_category_name(category):
            raise ValueError("自动收集分类名不安全")
        pack_dir = (self.paths.packs / pack_id).resolve()
        packs_root = self.paths.packs.resolve()
        try:
            pack_dir.relative_to(packs_root)
        except ValueError as exc:
            raise ValueError("自动收集表情包路径超出了表情包根目录") from exc
        if not pack_dir.is_dir() or (self.paths.packs / pack_id).is_symlink():
            raise ValueError("自动收集目标表情包不可用")
        if self._pack_contains_digest(pack_id, digest):
            return None
        category_dir = (pack_dir / "memes" / category).resolve()
        try:
            category_dir.relative_to(pack_dir / "memes")
        except ValueError as exc:
            raise ValueError("自动收集分类路径超出了目标表情包") from exc
        if category_dir.exists() and category_dir.is_symlink():
            raise ValueError("自动收集分类目录不能是符号链接")
        category_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{int(time.time() * 1000)}_{digest[:12]}{extension}"
        target = category_dir / filename
        temporary = category_dir / f".{filename}.tmp"
        temporary.write_bytes(content)
        os.replace(temporary, target)
        return target

    async def _save_to_inbox(
        self,
        job: AutoCollectJob,
        content: bytes,
        digest: str,
        extension: str,
        category: str,
        decision: dict[str, Any],
    ) -> None:
        """将已收集图片保存到所属表情包的独立待审箱。

        Args:
            job: 自动收集任务快照。
            content: 已通过校验的图片字节。
            digest: SHA-256 摘要。
            extension: 可信的扩展名。
            category: 建议的目标分类。
            decision: 已规范化的视觉识别结果。
        """
        async with self._inbox_lock:
            metadata = self._load_json(
                self.paths.metadata,
                {"schema_version": AUTO_COLLECT_SCHEMA_VERSION, "items": {}},
            )
            items = metadata.setdefault("items", {})
            if not isinstance(items, dict):
                items = {}
                metadata["items"] = items
            record_id = f"{job.target_pack_id}:{digest}"
            if record_id in items:
                logger.info(
                    "[meme_manager] 自动收集跳过图片：保存时发现图片已在待审箱，表情包：%s，图片摘要：%s",
                    job.target_pack_id,
                    digest[:12],
                )
                return
            if (
                sum(
                    isinstance(item, dict)
                    and item.get("target_pack_id") == job.target_pack_id
                    for item in items.values()
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
            self.paths.images.mkdir(parents=True, exist_ok=True)
            filename = f"{job.target_pack_id}__{digest}{extension}"
            target = self.paths.images / filename
            temporary = self.paths.images / f".{filename}.tmp"
            temporary.write_bytes(content)
            os.replace(temporary, target)
            items[record_id] = {
                "id": record_id,
                "target_pack_id": job.target_pack_id,
                "content_sha256": digest,
                "filename": filename,
                "suggested_category": category,
                "source_kind": job.source_kind,
                "source_id": job.source_id,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "classification": decision,
            }
            self._save_json(self.paths.metadata, metadata)
        self.counters["collected"] = self.counters.get("collected", 0) + 1
        logger.info(
            "[meme_manager] 已收集图片，等待审核；表情包：%s，分类：%s",
            job.target_pack_id,
            category,
        )
