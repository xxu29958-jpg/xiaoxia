from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .caption import generate_caption
from .index import (
    build_index,
)
from .models import (
    REVIEW_CATEGORY,
    is_category_tag,
)
from .storage import (
    confirm_image_category,
    get_image_semantic_detail,
    load_category_descriptions,
    restore_image_auto_semantic,
    save_manual_image_semantic,
    save_manual_image_semantic_and_move,
    set_image_embedding_failure,
    validate_image_edit_snapshot,
)
from .task_support import _revision_category_choice, _revision_original_category


class SemanticTaskManagerEdits:
    """Edits; shared runtime resources belong to the facade."""

    async def confirm_category(
        self,
        pack_id: str,
        image_path: Path | str,
        *,
        expected_content_sha256: str = "",
        expected_entry_id: str = "",
    ) -> dict[str, Any]:
        """在图包任务锁内保存人工确认，避免与开始语义任务并发覆盖。"""
        pack_id = self._validate_pack_id(pack_id)
        async with self._lock(pack_id):
            self.assert_pack_mutation_allowed(pack_id, "确认图片分类")
            return confirm_image_category(
                self._pack_dir(pack_id),
                image_path,
                expected_content_sha256=expected_content_sha256,
                expected_entry_id=expected_entry_id,
            )

    async def save_image_manual_semantic(
        self,
        pack_id: str,
        image_path: Path | str,
        *,
        caption: str,
        tags: Any,
        visible_text: str = "",
        category_decision: str = "keep",
        expected_content_sha256: str = "",
        expected_entry_id: str = "",
        update_vector: bool = False,
        target_category: str = "",
    ) -> dict[str, Any]:
        """保存单图人工语义，并可在同一图包锁内只更新该图向量。"""
        pack_id = self._validate_pack_id(pack_id)
        pack_dir = self._pack_dir(pack_id)
        normalized_target = str(target_category or "").strip()
        if normalized_target and not update_vector:
            raise ValueError("移动分类必须使用“保存并更新该图向量”")
        current_task = asyncio.current_task()
        async with self._lock(pack_id):
            operation = (
                "保存图片人工语义并移动分类"
                if normalized_target
                else "保存图片人工语义"
            )
            self.assert_pack_mutation_allowed(pack_id, operation)
            move_result: dict[str, Any] = {}
            effective_image_path = Path(image_path)
            if normalized_target:
                move_result = save_manual_image_semantic_and_move(
                    pack_dir,
                    image_path,
                    normalized_target,
                    caption=caption,
                    tags=tags,
                    visible_text=visible_text,
                    expected_content_sha256=expected_content_sha256,
                    expected_entry_id=expected_entry_id,
                )
                detail = move_result["semantic"]
                effective_image_path = Path(move_result["image_path"])
            else:
                detail = save_manual_image_semantic(
                    pack_dir,
                    image_path,
                    caption=caption,
                    tags=tags,
                    visible_text=visible_text,
                    category_decision=category_decision,
                    expected_content_sha256=expected_content_sha256,
                    expected_entry_id=expected_entry_id,
                )
            result = {
                "semantic": detail,
                "semantic_saved": True,
                "moved": bool(move_result),
                "source_category": str(move_result.get("source_category") or ""),
                "target_category": str(move_result.get("target_category") or ""),
                "category": str(detail.get("category") or ""),
                "filename": Path(str(detail.get("relative_path") or "")).name,
                "vector_update": {
                    "status": "pending",
                    "provider_available": False,
                    "requested_images": 0,
                    "reused_images": 0,
                },
            }
            if not update_vector:
                return result

            embedding = self._embedding_adapter(pack_id)
            if not embedding.ready:
                result["vector_update"].update(
                    {
                        "status": "waiting_provider",
                        "message": "语义已保存，当前没有可用向量模型，向量等待更新",
                    }
                )
                return result
            result["vector_update"]["provider_available"] = True
            if current_task is None:
                result["vector_update"].update(
                    {
                        "status": "pending",
                        "message": "语义已保存，但当前请求无法登记向量任务，请稍后重试",
                    }
                )
                return result

            self._index_tasks[pack_id] = current_task
            try:
                try:
                    await self._verify_embedding_dimension(pack_id, embedding)
                except Exception as exc:
                    failed_detail = set_image_embedding_failure(
                        pack_dir,
                        effective_image_path,
                        self._safe_error(exc, pack_id),
                        expected_content_sha256=detail["content_sha256"],
                        expected_entry_id=detail["entry_id"],
                    )
                    result["semantic"] = failed_detail
                    result["vector_update"].update(
                        {
                            "status": "failed",
                            "message": "语义已保存，但向量模型校验失败",
                        }
                    )
                    return result

                try:
                    manifest = await build_index(
                        pack_dir,
                        self.plugin_data_dir,
                        pack_id,
                        embedding,
                        force=False,
                        target_entry_ids={detail["entry_id"]},
                    )
                except Exception as exc:
                    latest_detail = get_image_semantic_detail(
                        pack_dir,
                        effective_image_path,
                    )
                    result["semantic"] = latest_detail
                    failed = latest_detail.get("embedding_status") == "failed"
                    result["vector_update"].update(
                        {
                            "status": "failed" if failed else "pending",
                            "message": (
                                "语义已保存，但当前图片向量更新失败"
                                if failed
                                else f"语义已保存，向量等待更新：{self._safe_error(exc, pack_id)}"
                            ),
                        }
                    )
                    return result

                latest_detail = get_image_semantic_detail(
                    pack_dir,
                    effective_image_path,
                )
                result["semantic"] = latest_detail
                result["vector_update"].update(
                    {
                        "status": "done",
                        "requested_images": int(
                            manifest.get("requested_embedding_count", 0) or 0
                        ),
                        "reused_images": int(
                            manifest.get("reused_vector_count", 0) or 0
                        ),
                        "item_count": int(manifest.get("item_count", 0) or 0),
                        "message": "人工语义和当前图片向量已更新",
                    }
                )
                return result
            finally:
                if self._index_tasks.get(pack_id) is current_task:
                    self._index_tasks.pop(pack_id, None)

    async def restore_image_auto_semantic(
        self,
        pack_id: str,
        image_path: Path | str,
        *,
        expected_content_sha256: str = "",
        expected_entry_id: str = "",
    ) -> dict[str, Any]:
        """在图包锁内显式放弃当前路径的人工修改。"""
        pack_id = self._validate_pack_id(pack_id)
        async with self._lock(pack_id):
            self.assert_pack_mutation_allowed(pack_id, "恢复图片自动语义")
            return restore_image_auto_semantic(
                self._pack_dir(pack_id),
                image_path,
                expected_content_sha256=expected_content_sha256,
                expected_entry_id=expected_entry_id,
            )

    async def propose_image_semantic_revision(
        self,
        pack_id: str,
        image_path: Path | str,
        *,
        review_instruction: str,
        expected_content_sha256: str = "",
        expected_entry_id: str = "",
    ) -> dict[str, Any]:
        """按人工复审意见调用视觉模型，只返回候选内容而不写入语义。"""
        instruction = str(review_instruction or "").strip()
        if not instruction:
            raise ValueError("请先填写人工复审意见")
        if len(instruction) > 2000:
            raise ValueError("人工复审意见不能超过 2000 个字符")

        pack_id = self._validate_pack_id(pack_id)
        pack_dir = self._pack_dir(pack_id)
        async with self._lock(pack_id):
            self.assert_pack_mutation_allowed(pack_id, "调用视觉模型重写图片语义")
            vision = self._vision_provider_details()
            if not vision["ready"]:
                raise RuntimeError("当前没有可用的视觉模型，请先在插件配置中选择")
            snapshot = validate_image_edit_snapshot(
                pack_dir,
                image_path,
                expected_content_sha256=expected_content_sha256,
                expected_entry_id=expected_entry_id,
            )
            item = snapshot["item"]
            category_descriptions = load_category_descriptions(pack_dir)
            memes_root = pack_dir / "memes"
            category_paths = (
                [
                    category_path
                    for category_path in memes_root.iterdir()
                    if category_path.is_dir() and not category_path.is_symlink()
                ]
                if memes_root.is_dir()
                else []
            )
            for category_path in category_paths:
                category_descriptions.setdefault(category_path.name, "")
            selectable_categories = {
                category_path.name
                for category_path in category_paths
                if category_path.name and category_path.name != REVIEW_CATEGORY
            }
            available_categories = {
                category: description
                for category, description in category_descriptions.items()
                if category in selectable_categories
            }
            original_category = _revision_original_category(
                item,
                selectable_categories,
            )
            try:
                proposal = await generate_caption(
                    self.context,
                    snapshot["source"],
                    str(vision["id"]),
                    category=item.category,
                    category_description=item.category_description,
                    available_categories=available_categories,
                    review_instruction=instruction,
                    current_semantic={
                        "caption": item.caption,
                        "tags": [tag for tag in item.tags if not is_category_tag(tag)],
                        "visible_text": item.visible_text,
                        "current_category": item.category,
                        "original_category": original_category,
                        "reclassification_status": item.reclassification_status,
                        "reclassification_reason": item.reclassification_reason,
                    },
                )
            except Exception as exc:
                self._record_vision_usage(
                    pack_id,
                    getattr(exc, "token_usage", None),
                )
                raise
            self._record_vision_usage(pack_id, proposal.get("token_usage"))
            validate_image_edit_snapshot(
                pack_dir,
                image_path,
                expected_content_sha256=snapshot["content_sha256"],
                expected_entry_id=snapshot["entry_id"],
            )
            category_fit = str(proposal.get("category_fit") or "uncertain")
            suggested_category = str(proposal.get("suggested_category") or "").strip()
            selected_category, classification_action = _revision_category_choice(
                current_category=item.category,
                original_category=original_category,
                category_fit=category_fit,
                suggested_category=suggested_category,
                selectable_categories=selectable_categories,
            )
            return {
                "caption": str(proposal.get("caption") or "").strip(),
                "tags": [
                    tag for tag in proposal.get("tags", []) if not is_category_tag(tag)
                ],
                "visible_text": str(proposal.get("visible_text") or "").strip(),
                "category_fit": category_fit,
                "category_review_reason": str(
                    proposal.get("category_review_reason") or ""
                ).strip(),
                "suggested_category": suggested_category,
                "current_category": item.category,
                "original_category": original_category,
                "selected_category": selected_category,
                "classification_action": classification_action,
                "vision_model": str(proposal.get("vision_model") or vision["id"]),
                "vision_requests": int(
                    (proposal.get("token_usage") or {}).get("calls", 1) or 1
                ),
            }
