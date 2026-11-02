from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .models import (
    IMAGE_EXTENSIONS,
    PROMPT_VERSION,
    REVIEW_CATEGORY,
    SemanticImage,
    ensure_category_tag,
    is_category_tag,
    normalize_tags,
    semantic_entry_id,
    text_hash,
    utc_now,
)
from .storage_common import SemanticMetadataCompatibilityError, file_sha256
from .storage_edit_snapshot import (
    _apply_manual_semantic_inputs,
    _assert_image_snapshot_unchanged,
    _normalize_manual_semantic_inputs,
    _resolve_image_edit_snapshot,
)
from .storage_metadata import load_metadata, save_metadata
from .storage_reclassification import _is_safe_category_key
from .storage_scan import load_category_descriptions
from .storage_views import get_image_semantic_detail


def save_collected_image_semantic(
    pack_dir: Path | str,
    image_path: Path | str,
    decision: dict[str, Any],
    *,
    manual_confirmed: bool = False,
) -> None:
    """Preserve collection analysis without starting a model or index task.

    Args:
        pack_dir: Destination pack protected by its external-operation lock.
        image_path: Newly accepted image inside the destination pack.
        decision: Collection analysis with its semantic prompt and category.
        manual_confirmed: Whether the user explicitly accepted this category.

    Raises:
        ValueError: The image or metadata is invalid for the destination pack.
        RuntimeError: The image changes before metadata can be saved.
    """
    root = Path(pack_dir).resolve()
    requested = Path(image_path)
    source = requested.resolve()
    if (
        requested.is_symlink()
        or not source.is_relative_to(root / "memes")
        or source.suffix.lower() not in IMAGE_EXTENSIONS
        or not source.is_file()
    ):
        raise ValueError("Collected image must be inside the destination pack")
    digest = file_sha256(source)
    relative_path = source.relative_to(root).as_posix()
    category = source.parent.name
    entry_id = semantic_entry_id(digest, category, relative_path)
    # 接收一张图片时不能重新计算整个现有图库的哈希。
    metadata = load_metadata(root)
    if metadata.get("metadata_read_only"):
        raise SemanticMetadataCompatibilityError(str(metadata.get("metadata_error")))
    images = metadata.setdefault("images", {})
    item = SemanticImage(
        content_sha256=digest,
        relative_path=relative_path,
        category=category,
        category_description=load_category_descriptions(root).get(category, ""),
    )
    caption = str(decision.get("caption") or "").strip()[:4000]
    tags = [
        tag for tag in normalize_tags(decision.get("tags")) if not is_category_tag(tag)
    ]
    visible_text = str(decision.get("visible_text") or "").strip()[:4000]
    reusable = bool(
        caption
        and tags
        and decision.get("semantic_prompt_version") == PROMPT_VERSION
        and decision.get("semantic_category") == item.category
        and item.category != REVIEW_CATEGORY
    )
    # 旧版或重新分类的分析只能保留为草稿，不能视为已完成的描述。
    item.auto_caption = caption
    item.auto_tags = tags
    item.auto_visible_text = visible_text
    if reusable:
        item.caption = caption
        item.tags = ensure_category_tag(tags, item.category)
        item.visible_text = visible_text
        item.caption_status = "done"
        item.prompt_version = PROMPT_VERSION
        item.category_fit = "match"
        item.category_review_status = "auto_match"
        item.category_review_context_hash = item.category_context_hash
        item.auto_category_fit = "match"
        item.auto_category_review_status = "auto_match"
    else:
        item.caption = ""
        item.tags = ensure_category_tag([], item.category)
        item.visible_text = ""
        item.caption_status = "pending"
    if manual_confirmed and item.category != REVIEW_CATEGORY:
        item.category_fit = "match"
        item.category_review_status = "manual_confirmed"
        item.category_review_context_hash = item.category_context_hash
        item.manual_confirmation_context_hash = item.category_context_hash
    item.vision_model = str(decision.get("vision_model") or "")
    item.embedding_status = "pending"
    item.text_hash = text_hash(item.vector_text) if reusable else ""
    item.updated_at = utc_now()
    _assert_image_snapshot_unchanged(root, source, digest, entry_id)
    images[entry_id] = item.to_dict()
    metadata["requires_local_index_rebuild"] = True
    save_metadata(root, metadata)


def save_manual_image_semantic(
    pack_dir: Path | str,
    image_path: Path | str,
    *,
    caption: str,
    tags: Any,
    visible_text: str = "",
    category_decision: str = "keep",
    expected_content_sha256: str = "",
    expected_entry_id: str = "",
) -> dict[str, Any]:
    """原子保存一张图片的人工语义，并只让该记录等待向量更新。"""
    normalized_caption, normalized_tags, normalized_visible_text = (
        _normalize_manual_semantic_inputs(caption, tags, visible_text)
    )

    root, source, digest, entry_id, metadata, raw_item = _resolve_image_edit_snapshot(
        pack_dir,
        image_path,
        expected_content_sha256=expected_content_sha256,
        expected_entry_id=expected_entry_id,
    )
    item = _apply_manual_semantic_inputs(
        SemanticImage.from_dict(raw_item),
        caption=normalized_caption,
        tags=normalized_tags,
        visible_text=normalized_visible_text,
        category_decision=category_decision,
    )
    _assert_image_snapshot_unchanged(root, source, digest, entry_id)
    raw_item.update(item.to_dict())
    metadata["requires_local_index_rebuild"] = True
    metadata["last_manual_edit_at"] = item.updated_at
    save_metadata(root, metadata)
    return get_image_semantic_detail(root, source)


def _resolve_image_move_target(
    root: Path,
    source: Path,
    target_category: str,
) -> tuple[str, Path]:
    normalized_target = str(target_category or "").strip()
    if not _is_safe_category_key(normalized_target):
        raise ValueError("目标分类无效")
    if normalized_target == source.parent.name:
        raise ValueError("图片已经位于所选分类")

    memes_root = (root / "memes").resolve()
    requested_target_dir = memes_root / normalized_target
    if requested_target_dir.is_symlink():
        raise ValueError("不允许把图片移动到符号链接分类")
    target_dir = requested_target_dir.resolve()
    try:
        target_dir.relative_to(memes_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("目标分类无效") from exc
    if not target_dir.is_dir():
        raise FileNotFoundError("目标分类不存在，请刷新页面后重试")

    target = target_dir / source.name
    if target.is_symlink() or target.exists():
        raise FileExistsError("目标分类中已有同名图片，请先处理重名文件")
    return normalized_target, target


def _build_category_moved_item(
    root: Path,
    target: Path,
    target_category: str,
    raw_item: dict[str, Any],
) -> SemanticImage:
    moved_payload = copy.deepcopy(raw_item)
    moved_payload.update(
        {
            "category": target_category,
            "category_description": str(
                load_category_descriptions(root).get(target_category, "")
            ).strip(),
            "relative_path": target.relative_to(root).as_posix(),
        }
    )
    for tag_field in ("tags", "auto_tags", "manual_tags"):
        moved_payload[tag_field] = [
            tag
            for tag in normalize_tags(moved_payload.get(tag_field))
            if not is_category_tag(tag)
        ]
    return SemanticImage.from_dict(moved_payload)


def save_manual_image_semantic_and_move(
    pack_dir: Path | str,
    image_path: Path | str,
    target_category: str,
    *,
    caption: str,
    tags: Any,
    visible_text: str = "",
    expected_content_sha256: str = "",
    expected_entry_id: str = "",
) -> dict[str, Any]:
    """一次提交人工语义和分类移动；语义文件失败时把图片移回原处。"""
    normalized_caption, normalized_tags, normalized_visible_text = (
        _normalize_manual_semantic_inputs(caption, tags, visible_text)
    )
    root, source, digest, entry_id, metadata, raw_item = _resolve_image_edit_snapshot(
        pack_dir,
        image_path,
        expected_content_sha256=expected_content_sha256,
        expected_entry_id=expected_entry_id,
    )
    normalized_target, target = _resolve_image_move_target(
        root,
        source,
        target_category,
    )
    moved_item = _apply_manual_semantic_inputs(
        _build_category_moved_item(root, target, normalized_target, raw_item),
        caption=normalized_caption,
        tags=normalized_tags,
        visible_text=normalized_visible_text,
        category_decision="match",
    )
    # 自动结果是在旧分类上下文中生成的，不能作为新分类下“恢复自动生成”
    # 的候选。保留它会让用户恢复后得到带旧分类语义的描述。
    moved_item.auto_caption = ""
    moved_item.auto_tags = []
    moved_item.auto_visible_text = ""
    moved_item.auto_category_fit = "uncertain"
    moved_item.auto_category_review_status = "unchecked"
    moved_item.auto_category_review_reason = ""
    moved_item.suggested_category = ""

    updated_metadata = copy.deepcopy(metadata)
    updated_images = updated_metadata.setdefault("images", {})
    updated_images.pop(entry_id, None)
    updated_images[moved_item.entry_id] = moved_item.to_dict()
    updated_metadata["requires_local_index_rebuild"] = True
    updated_metadata["last_manual_edit_at"] = moved_item.updated_at
    updated_metadata["last_manual_category_move_at"] = moved_item.updated_at

    _assert_image_snapshot_unchanged(root, source, digest, entry_id)
    source.rename(target)
    try:
        if file_sha256(target) != digest:
            raise RuntimeError("图片移动后内容校验失败")
        save_metadata(root, updated_metadata)
    except Exception:
        if target.is_file() and not source.exists():
            target.rename(source)
        raise

    return {
        "moved": True,
        "source_category": source.parent.name,
        "target_category": normalized_target,
        "filename": target.name,
        "image_path": target,
        "semantic": get_image_semantic_detail(root, target),
    }


def restore_image_auto_semantic(
    pack_dir: Path | str,
    image_path: Path | str,
    *,
    expected_content_sha256: str = "",
    expected_entry_id: str = "",
) -> dict[str, Any]:
    """显式放弃当前路径的人工内容，恢复旧自动结果或等待重新生成。"""
    root, source, digest, entry_id, metadata, raw_item = _resolve_image_edit_snapshot(
        pack_dir,
        image_path,
        expected_content_sha256=expected_content_sha256,
        expected_entry_id=expected_entry_id,
    )
    item = SemanticImage.from_dict(raw_item)
    if not (item.manual_override or item.provenance in {"manual", "mixed"}):
        raise ValueError("这张图片当前没有人工修改，无需恢复")

    item.manual_override = False
    item.provenance = "ai"
    item.manual_caption = ""
    item.manual_tags = []
    item.manual_visible_text = ""
    if item.auto_caption and item.auto_tags:
        item.caption = item.auto_caption
        item.tags = ensure_category_tag(item.auto_tags, item.category)
        item.visible_text = item.auto_visible_text
        item.category_fit = item.auto_category_fit
        item.category_review_status = item.auto_category_review_status
        item.category_review_reason = item.auto_category_review_reason
        if item.category_review_status in {"auto_match", "needs_review"}:
            item.category_review_context_hash = item.category_context_hash
        else:
            item.category_review_status = "unchecked"
            item.category_review_context_hash = ""
        item.caption_status = "done"
        item.text_hash = text_hash(item.vector_text)
    else:
        item.caption = ""
        item.tags = ensure_category_tag([], item.category)
        item.visible_text = ""
        item.category_fit = "uncertain"
        item.category_review_status = "unchecked"
        item.category_review_reason = ""
        item.category_review_context_hash = ""
        item.caption_status = "pending"
        item.text_hash = ""
    item.manual_confirmation_context_hash = ""
    item.embedding_status = "pending"
    item.error = None
    item.updated_at = utc_now()
    _assert_image_snapshot_unchanged(root, source, digest, entry_id)
    raw_item.update(item.to_dict())
    metadata["requires_local_index_rebuild"] = True
    metadata["last_manual_restore_at"] = item.updated_at
    save_metadata(root, metadata)
    return get_image_semantic_detail(root, source)


def set_image_embedding_failure(
    pack_dir: Path | str,
    image_path: Path | str,
    error: str,
    *,
    expected_content_sha256: str = "",
    expected_entry_id: str = "",
) -> dict[str, Any]:
    """记录单图向量更新失败，不改变已保存的人工语义。"""
    root, source, digest, entry_id, metadata, raw_item = _resolve_image_edit_snapshot(
        pack_dir,
        image_path,
        expected_content_sha256=expected_content_sha256,
        expected_entry_id=expected_entry_id,
    )
    item = SemanticImage.from_dict(raw_item)
    item.embedding_status = "failed"
    item.error = str(error or "向量更新失败").strip()[:1000]
    item.updated_at = utc_now()
    _assert_image_snapshot_unchanged(root, source, digest, entry_id)
    raw_item.update(item.to_dict())
    metadata["requires_local_index_rebuild"] = True
    save_metadata(root, metadata)
    return get_image_semantic_detail(root, source)


def confirm_image_category(
    pack_dir: Path | str,
    image_path: Path | str,
    *,
    expected_content_sha256: str = "",
    expected_entry_id: str = "",
) -> dict[str, Any]:
    """由用户确认当前分类，并把确认绑定到当前图片和分类描述。"""
    root, source, digest, entry_id, metadata, raw_item = _resolve_image_edit_snapshot(
        pack_dir,
        image_path,
        expected_content_sha256=expected_content_sha256,
        expected_entry_id=expected_entry_id,
    )
    item = SemanticImage.from_dict(raw_item)
    item.category_review_status = "manual_confirmed"
    item.category_fit = "match"
    item.category_review_reason = ""
    item.category_review_context_hash = item.category_context_hash
    item.manual_confirmation_context_hash = item.category_context_hash
    item.updated_at = utc_now()
    _assert_image_snapshot_unchanged(root, source, digest, entry_id)
    raw_item.update(item.to_dict())
    save_metadata(root, metadata)
    return get_image_semantic_detail(root, source)
