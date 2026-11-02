from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import (
    IMAGE_EXTENSIONS,
    SemanticImage,
    ensure_category_tag,
    is_category_tag,
    normalize_tags,
    semantic_entry_id,
    text_hash,
    utc_now,
)
from .storage_common import file_sha256
from .storage_reconciliation import reconcile_metadata


def _resolve_image_edit_snapshot(
    pack_dir: Path | str,
    image_path: Path | str,
    *,
    expected_content_sha256: str = "",
    expected_entry_id: str = "",
) -> tuple[Path, Path, str, str, dict[str, Any], dict[str, Any]]:
    """重新核对编辑对象，拒绝删除、移动、换图或旧页面提交。"""
    root = Path(pack_dir).resolve()
    memes_root = (root / "memes").resolve()
    requested_source = Path(image_path)
    if requested_source.is_symlink():
        raise ValueError("不允许通过符号链接编辑图片")
    source = requested_source.resolve()
    try:
        source.relative_to(memes_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("图片路径不属于当前表情包") from exc
    if not source.is_file():
        raise FileNotFoundError("图片已被删除或移动，请重新打开后再编辑")
    if source.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError("当前文件不是支持的图片格式")

    digest = file_sha256(source)
    expected_digest = str(expected_content_sha256 or "").strip().lower()
    if expected_digest and digest != expected_digest:
        raise RuntimeError("图片内容已发生变化，请重新打开后再编辑")
    relative_path = source.relative_to(root).as_posix()
    entry_id = semantic_entry_id(digest, source.parent.name, relative_path)
    if expected_entry_id and str(expected_entry_id) != entry_id:
        raise RuntimeError("图片位置或内容已发生变化，请重新打开后再编辑")

    metadata = reconcile_metadata(root)
    raw_item = metadata.get("images", {}).get(entry_id)
    if not isinstance(raw_item, dict):
        raise RuntimeError("图片语义记录已发生变化，请重新打开后再编辑")
    if str(raw_item.get("relative_path") or "") != relative_path:
        raise RuntimeError("图片位置已发生变化，请重新打开后再编辑")
    return root, source, digest, entry_id, metadata, raw_item


def _assert_image_snapshot_unchanged(
    root: Path, source: Path, expected_digest: str, expected_entry_id: str
) -> None:
    """写入前最后复核文件，缩小外部文件变更造成的竞态窗口。"""
    if not source.is_file():
        raise FileNotFoundError("图片已被删除或移动，请重新打开后再编辑")
    current_digest = file_sha256(source)
    relative_path = source.relative_to(root).as_posix()
    current_entry_id = semantic_entry_id(
        current_digest, source.parent.name, relative_path
    )
    if current_digest != expected_digest or current_entry_id != expected_entry_id:
        raise RuntimeError("图片位置或内容已发生变化，请重新打开后再编辑")


def validate_image_edit_snapshot(
    pack_dir: Path | str,
    image_path: Path | str,
    *,
    expected_content_sha256: str = "",
    expected_entry_id: str = "",
) -> dict[str, Any]:
    """校验单图编辑快照，并返回模型调用所需的当前只读上下文。"""
    root, source, digest, entry_id, _metadata, raw_item = _resolve_image_edit_snapshot(
        pack_dir,
        image_path,
        expected_content_sha256=expected_content_sha256,
        expected_entry_id=expected_entry_id,
    )
    item = SemanticImage.from_dict(raw_item)
    return {
        "root": root,
        "source": source,
        "content_sha256": digest,
        "entry_id": entry_id,
        "item": item,
    }


def _normalize_manual_semantic_inputs(
    caption: str,
    tags: Any,
    visible_text: str,
) -> tuple[str, list[str], str]:
    normalized_caption = str(caption or "").strip()
    normalized_visible_text = str(visible_text or "").strip()
    if not normalized_caption:
        raise ValueError("图片含义不能为空")
    if len(normalized_caption) > 4000:
        raise ValueError("图片含义不能超过 4000 个字符")
    if len(normalized_visible_text) > 4000:
        raise ValueError("图片内文字不能超过 4000 个字符")
    normalized_tags = normalize_tags(tags)
    if len(normalized_tags) > 50 or any(len(tag) > 80 for tag in normalized_tags):
        raise ValueError("普通语义标签最多 50 个，每个不能超过 80 个字符")
    return normalized_caption, normalized_tags, normalized_visible_text


def _apply_manual_semantic_inputs(
    item: SemanticImage,
    *,
    caption: str,
    tags: list[str],
    visible_text: str,
    category_decision: str,
) -> SemanticImage:
    fixed_tag = item.category_tag
    illegal_fixed_tags = [
        tag for tag in tags if is_category_tag(tag) and tag != fixed_tag
    ]
    if illegal_fixed_tags:
        raise ValueError("固定分类标签不能在语义标签中新增或修改；请移动图片分类")
    editable_tags = [
        tag for tag in tags if tag != fixed_tag and not is_category_tag(tag)
    ]

    was_manual = bool(item.manual_override or item.provenance in {"manual", "mixed"})
    if not was_manual:
        item.auto_caption = item.caption
        item.auto_tags = [tag for tag in item.tags if tag != fixed_tag]
        item.auto_visible_text = item.visible_text
        item.auto_category_fit = item.category_fit
        item.auto_category_review_status = item.category_review_status
        item.auto_category_review_reason = item.category_review_reason

    item.manual_caption = caption
    item.manual_tags = editable_tags
    item.manual_visible_text = visible_text
    item.caption = caption
    item.tags = ensure_category_tag(editable_tags, item.category)
    item.visible_text = visible_text
    item.manual_override = True
    item.provenance = "manual"
    decision = str(category_decision or "keep").strip().lower()
    if decision == "match":
        item.category_fit = "match"
        item.category_review_status = "manual_confirmed"
        item.category_review_reason = ""
        item.category_review_context_hash = item.category_context_hash
        item.manual_confirmation_context_hash = item.category_context_hash
    elif decision in {"mismatch", "conflict"}:
        item.category_fit = "conflict"
        item.category_review_status = "manual_rejected"
        item.category_review_reason = "人工确认当前分类不符合，请移动到正确分类"
        item.category_review_context_hash = item.category_context_hash
        item.manual_confirmation_context_hash = item.category_context_hash
    elif decision != "keep":
        raise ValueError("分类确认状态无效")

    item.caption_status = "done"
    item.embedding_status = "pending"
    item.text_hash = text_hash(item.vector_text)
    item.error = None
    item.updated_at = utc_now()
    return item
