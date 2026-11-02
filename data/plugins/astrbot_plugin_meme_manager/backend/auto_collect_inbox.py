from __future__ import annotations

import asyncio
import hashlib
import math
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .packs.protocol import validate_pack_id
from .packs.resolver import load_pack_category_mapping
from .semantic.models import REVIEW_CATEGORY
from .semantic.storage import save_collected_image_semantic


class AutoCollectManagerInbox:
    """Inbox; shared runtime resources belong to the facade."""

    async def pending_status(self, pack_id: str) -> dict[str, Any]:
        """List every candidate belonging to a pack, with confident items first.

        Args:
            pack_id: Target pack identifier.

        Returns:
            Review items, categories, limits, and collection counters.
        """
        pack_id = validate_pack_id(pack_id, "表情包")
        async with self._inbox_lock:
            metadata = self._load_json(self.paths.metadata, {"items": {}})
            records = []
            raw_items = metadata.get("items", {})
            for item in raw_items.values() if isinstance(raw_items, dict) else []:
                if not isinstance(item, dict) or item.get("target_pack_id") != pack_id:
                    continue
                decision = item.get("classification", {})
                if not isinstance(decision, dict):
                    decision = {}
                confidence_values = {}
                for key in ("meme_confidence", "category_confidence"):
                    try:
                        value = float(decision.get(key, 0) or 0)
                    except (TypeError, ValueError):
                        value = 0.0
                    confidence_values[key] = (
                        max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0
                    )
                records.append(
                    {
                        **{
                            key: item.get(key, "")
                            for key in (
                                "id",
                                "suggested_category",
                                "source_kind",
                                "source_id",
                                "received_at",
                            )
                        },
                        **{
                            key: decision.get(key, "")
                            for key in (
                                "reason",
                                "caption",
                                "visible_text",
                                "near_duplicate",
                            )
                        },
                        "tags": decision.get("tags", []),
                        **confidence_values,
                    }
                )
        records.sort(
            key=lambda item: (
                item["category_confidence"],
                item["meme_confidence"],
                item["received_at"],
            ),
            reverse=True,
        )
        return {
            "visible": True,
            "count": len(records),
            "items": records,
            "categories": load_pack_category_mapping(pack_id),
            "manual_review": self.manual_review,
            "pending_limit": self.pending_limit,
            "counters": dict(self.counters),
        }

    def pending_image_path(
        self, pack_id: str, record_id: str, *, _record: dict[str, Any] | None = None
    ) -> Path:
        """Resolve a candidate while enforcing pack ownership and file containment.

        Args:
            pack_id: Target pack identifier.
            record_id: Candidate identifier.
            _record: Internal batch record read while holding the inbox lock.

        Returns:
            Safe existing image path.

        Raises:
            ValueError: The record belongs to another pack or has an unsafe path.
            FileNotFoundError: The candidate or image no longer exists.
        """
        pack_id = validate_pack_id(pack_id, "表情包")
        item = _record
        if item is None:
            metadata = self._load_json(self.paths.metadata, {"items": {}})
            items = metadata.get("items", {})
            item = items.get(record_id) if isinstance(items, dict) else None
        if not isinstance(item, dict):
            raise FileNotFoundError("待审核图片不存在")
        digest = str(item.get("content_sha256") or "")
        if (
            item.get("target_pack_id") != pack_id
            or record_id != f"{pack_id}:{digest}"
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("待审核图片不属于当前表情包")
        source = self.paths.images / str(item.get("filename") or "")
        if (
            source.is_symlink()
            or source.resolve().parent != self.paths.images.resolve()
        ):
            raise ValueError("待审核图片路径无效")
        if not source.is_file():
            raise FileNotFoundError("待审核图片文件不存在")
        return source

    async def accept_pending(
        self,
        pack_id: str,
        selections: list[dict[str, Any]],
        *,
        manual_confirmed: bool = True,
    ) -> dict[str, int]:
        """Accept selected candidates, retaining failed candidates for retry.

        Args:
            pack_id: Target pack identifier.
            selections: Candidate IDs with optional category overrides.
            manual_confirmed: Whether a person explicitly accepted these images.

        Returns:
            Imported, duplicate, and failed item counts.

        Raises:
            ValueError: Selection, ownership, or category is invalid.
            RuntimeError: Another operation holds the pack write lock.
        """
        pack_id = validate_pack_id(pack_id, "表情包")
        if not (self.paths.packs / pack_id).is_dir():
            raise FileNotFoundError("表情包不存在")
        if not isinstance(selections, list) or not selections or len(selections) > 2000:
            raise ValueError("请选择要接收的图片")
        categories = load_pack_category_mapping(pack_id)
        result = {"imported": 0, "duplicates": 0, "failed": 0}
        async with self._inbox_lock:
            metadata = self._load_json(self.paths.metadata, {"items": {}})
            items = metadata.get("items", {})
            validated = {}
            for selection in selections:
                if not isinstance(selection, dict):
                    raise ValueError("图片选择格式无效")
                record_id = str(selection.get("id") or "")
                source = self.pending_image_path(
                    pack_id, record_id, _record=items.get(record_id, {})
                )
                item = items[record_id]
                category = str(
                    selection.get("category")
                    or item.get("suggested_category")
                    or REVIEW_CATEGORY
                )
                if category not in categories and category != REVIEW_CATEGORY:
                    raise ValueError("请选择当前表情包中的分类")
                validated[record_id] = (source, category)
            self.plugin.semantic_task_manager.begin_external_pack_operation(
                pack_id, "审核自动收集图片"
            )
            try:
                for record_id, (source, category) in validated.items():
                    item = items[record_id]
                    try:
                        content = await asyncio.to_thread(source.read_bytes)
                        digest = hashlib.sha256(content).hexdigest()
                        if digest != item.get("content_sha256"):
                            raise ValueError("待审核图片内容已改变")
                        extension = await asyncio.to_thread(
                            self._validate_image, content
                        )
                        saved = await asyncio.to_thread(
                            self._write_pack_image,
                            pack_id,
                            category,
                            content,
                            digest,
                            extension,
                        )
                        if saved is not None:
                            try:
                                await asyncio.to_thread(
                                    save_collected_image_semantic,
                                    self.paths.packs / pack_id,
                                    saved,
                                    item.get("classification", {}),
                                    manual_confirmed=manual_confirmed,
                                )
                            except Exception:
                                saved.unlink(missing_ok=True)
                                raise
                            result["imported"] += 1
                        else:
                            result["duplicates"] += 1
                        del items[record_id]
                        self._save_json(self.paths.metadata, metadata)
                        source.unlink(missing_ok=True)
                    except Exception as exc:
                        result["failed"] += 1
                        logger.error(
                            "[meme_manager] 接收候选图片 %s 失败：%s",
                            record_id,
                            exc,
                        )
            finally:
                self.plugin.semantic_task_manager.end_external_pack_operation(pack_id)
        if result["imported"]:
            await self.plugin.reload_emotions()
        return result

    async def discard_pending(
        self, pack_id: str, ids: list[str], remember_rejection: bool = False
    ) -> dict[str, int]:
        """Discard selected candidates and optionally block their recollection.

        Args:
            pack_id: Target pack identifier.
            ids: Selected candidate identifiers.
            remember_rejection: Persist exact hashes as rejected for this pack.

        Returns:
            Number of discarded candidates.

        Raises:
            ValueError: Selection or pack ownership is invalid.
        """
        if (
            not isinstance(ids, list)
            or not ids
            or len(ids) > 2000
            or any(not isinstance(record_id, str) for record_id in ids)
        ):
            raise ValueError("请选择要丢弃的图片")
        async with self._inbox_lock:
            metadata = self._load_json(self.paths.metadata, {"items": {}})
            items = metadata.get("items", {})
            paths = {
                record_id: self.pending_image_path(
                    pack_id, record_id, _record=items.get(record_id, {})
                )
                for record_id in ids
            }
            if remember_rejection:
                rejected = self._state.setdefault("rejected", {}).setdefault(
                    pack_id, []
                )
                rejected.extend(
                    items[record_id]["content_sha256"]
                    for record_id in paths
                    if items[record_id]["content_sha256"] not in rejected
                )
                self._save_json(self.paths.state, self._state)
            for record_id in paths:
                del items[record_id]
            self._save_json(self.paths.metadata, metadata)
            for source in paths.values():
                source.unlink(missing_ok=True)
        return {"discarded": len(paths)}

    async def import_pending(self, pack_id: str) -> dict[str, int]:
        """Import legacy inbox candidates only when manual review is disabled.

        Args:
            pack_id: Target pack identifier.

        Returns:
            Import result counts.

        Raises:
            RuntimeError: Manual review requires explicit candidate selections.
        """
        if self.manual_review:
            raise RuntimeError("请在表情包管理页面预览并选择要接收的图片")
        status = await self.pending_status(pack_id)
        if not status["items"]:
            return {"imported": 0, "duplicates": 0, "failed": 0}
        return await self.accept_pending(
            pack_id, [{"id": item["id"]} for item in status["items"]]
        )
