from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .caption import generate_caption
from .models import (
    SemanticImage,
    ensure_category_tag,
    semantic_caption_is_complete,
    text_hash,
    utc_now,
)
from .storage import (
    safe_relative_path,
    save_metadata,
)


class SemanticTaskManagerCaption:
    """Caption; shared runtime resources belong to the facade."""

    async def _process_caption_item(
        self,
        pack_id: str,
        pack_dir: Path,
        metadata: dict[str, Any],
        digest: str,
        raw_item: dict[str, Any],
        vision_provider: str,
        available_categories: dict[str, str],
        force: bool,
        semaphore: asyncio.Semaphore,
    ) -> None:
        item = SemanticImage.from_dict(raw_item)
        if item.manual_override or item.provenance in {"manual", "mixed"}:
            return
        if not force and semantic_caption_is_complete(item.to_dict()):
            return
        event = self._pause_events.setdefault(pack_id, asyncio.Event())
        # 第一次等待避免暂停状态下无意义地争抢并发名额。
        await event.wait()
        path = safe_relative_path(pack_dir, item.relative_path)
        if path is None or not path.is_file():
            item.caption_status = "failed"
            item.embedding_status = "pending"
            item.error = "图片路径无效或文件不存在"
            item.updated_at = utc_now()
            raw_item.update(item.to_dict())
            save_metadata(pack_dir, metadata)
            return

        active_items: list[str] = []
        try:
            async with semaphore:
                # 关键的第二次检查：图片可能在暂停前已经通过第一次等待，随后
                # 一直堵在并发闸门外。拿到名额后必须重新确认，暂停后才能真正
                # 停止补位，只让已经发出的模型请求自然收尾。
                while True:
                    await event.wait()
                    state = self._load_state(pack_id)
                    if state.get("task_status") != "paused":
                        break
                    event.clear()
                active_items = [
                    str(value)
                    for value in state.get("active_items", [])
                    if str(value or "").strip()
                ]
                if item.relative_path not in active_items:
                    active_items.append(item.relative_path)
                self._save_state(
                    pack_id,
                    {
                        **state,
                        "task_phase": "captioning",
                        "active_items": active_items,
                        "current": active_items[0]
                        if len(active_items) == 1
                        else f"并发处理中（{len(active_items)} 项）",
                        "updated_at": utc_now(),
                    },
                )
                item.caption_status = "running"
                raw_item.update(item.to_dict())
                save_metadata(pack_dir, metadata)
                caption = await generate_caption(
                    self.context,
                    path,
                    vision_provider,
                    category=item.category,
                    category_description=item.category_description,
                    available_categories=available_categories,
                )
            self._record_vision_usage(pack_id, caption.get("token_usage"))
            manual_content = bool(
                item.manual_override or item.provenance in {"manual", "mixed"}
            )
            if not manual_content:
                item.caption = caption["caption"]
                item.tags = ensure_category_tag(caption["tags"], item.category)
                item.visible_text = caption["visible_text"]
                item.auto_caption = item.caption
                item.auto_tags = caption["tags"]
                item.auto_visible_text = item.visible_text
            item.vision_model = caption.get("vision_model", "")
            item.prompt_version = caption.get("prompt_version", item.prompt_version)
            item.category_fit = str(caption.get("category_fit") or "uncertain")
            item.suggested_category = str(
                caption.get("suggested_category") or ""
            ).strip()
            if not (
                item.category_review_status == "manual_confirmed"
                and item.manual_confirmation_context_hash == item.category_context_hash
            ):
                if item.reclassification_status:
                    item.category_review_status = "needs_review"
                    item.category_review_reason = str(
                        item.category_review_reason
                        or item.reclassification_reason
                        or "图片曾被自动重分类，等待人工确认"
                    ).strip()
                else:
                    item.category_review_status = (
                        "auto_match" if item.category_fit == "match" else "needs_review"
                    )
                    item.category_review_reason = str(
                        caption.get("category_review_reason") or ""
                    ).strip()
                item.category_review_context_hash = item.category_context_hash
                item.manual_confirmation_context_hash = ""
            if not manual_content:
                item.auto_category_fit = item.category_fit
                item.auto_category_review_status = item.category_review_status
                item.auto_category_review_reason = item.category_review_reason
            item.caption_status = "done"
            item.embedding_status = "pending"
            item.text_hash = text_hash(item.vector_text)
            item.error = None
        except Exception as exc:
            self._record_vision_usage(pack_id, getattr(exc, "token_usage", None))
            item.error = self._safe_error(exc, pack_id)
            result_preview = str(getattr(exc, "result_preview", "") or "").strip()
            if result_preview:
                item.error = f"{item.error}；模型返回：{result_preview[:1000]}"
            if item.caption_status != "done":
                item.caption_status = "failed"
        finally:
            item.updated_at = utc_now()
            raw_item.update(item.to_dict())
            save_metadata(pack_dir, metadata)
            state = self._load_state(pack_id)
            active_items = [
                str(value)
                for value in state.get("active_items", [])
                if str(value or "").strip() and str(value) != item.relative_path
            ]
            self._save_state(
                pack_id,
                {
                    **state,
                    "active_items": active_items,
                    "current": active_items[0]
                    if len(active_items) == 1
                    else (
                        f"并发处理中（{len(active_items)} 项）" if active_items else ""
                    ),
                    "updated_at": utc_now(),
                },
            )
