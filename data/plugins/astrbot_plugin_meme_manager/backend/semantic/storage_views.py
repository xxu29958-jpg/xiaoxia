from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import (
    IMAGE_EXTENSIONS,
    build_category_tag,
    is_category_tag,
    normalize_tags,
    semantic_entry_id,
)
from .storage_common import file_sha256, metadata_path
from .storage_metadata import load_metadata, semantic_metadata_is_complete


def get_pack_semantic_summary(
    pack_dir: Path | str, image_count: int | None = None, *, verify_files: bool = True
) -> dict[str, Any]:
    """Summarize semantic progress from the saved metadata.

    Args:
        pack_dir: Pack directory.
        image_count: Known image count, or None to count directory entries.
        verify_files: Whether to hash images and verify the saved snapshot.
            Page queries disable this and report saved progress only.

    Returns:
        Progress and snapshot status for the pack.
    """
    root = Path(pack_dir).resolve()
    if image_count is None:
        memes_root = root / "memes"
        current_file_total = (
            sum(
                1
                for path in memes_root.rglob("*")
                if path.is_file()
                and path.suffix.lower() in IMAGE_EXTENSIONS.union({".webp"})
            )
            if memes_root.is_dir()
            else 0
        )
    else:
        current_file_total = max(0, int(image_count))

    metadata_file = root / "semantic_metadata.json"
    if not metadata_file.is_file():
        return {
            "semantic_status": "none",
            "semantic_caption_done": 0,
            "semantic_caption_total": 0,
            "semantic_caption_failed": 0,
            "semantic_file_total": current_file_total,
            "semantic_snapshot_matches": False,
            "semantic_files_changed": False,
        }

    metadata = load_metadata(root, scan_legacy=verify_files)
    if metadata.get("metadata_read_only"):
        return {
            "semantic_status": "error",
            "semantic_caption_done": 0,
            "semantic_caption_total": 0,
            "semantic_caption_failed": 0,
            "semantic_file_total": current_file_total,
            "semantic_snapshot_matches": False,
            "semantic_files_changed": False,
            "semantic_metadata_read_only": True,
            "semantic_metadata_error": str(metadata.get("metadata_error") or ""),
        }
    images = [
        item for item in metadata.get("images", {}).values() if isinstance(item, dict)
    ]
    if metadata.get("metadata_migration_required") and not int(
        metadata.get("legacy_semantic_content_count", 0) or 0
    ):
        return {
            "semantic_status": "none",
            "semantic_caption_done": 0,
            "semantic_caption_total": 0,
            "semantic_caption_failed": 0,
            "semantic_file_total": current_file_total,
            "semantic_snapshot_matches": False,
            "semantic_files_changed": False,
            "semantic_metadata_migration_required": True,
            "semantic_metadata_migrated_from": str(
                metadata.get("migrated_from_schema_version") or ""
            ),
        }
    caption_done = sum(
        1
        for item in images
        if item.get("caption_status") == "done"
        and str(item.get("caption") or "").strip()
        and item.get("tags")
    )
    caption_failed = sum(1 for item in images if item.get("caption_status") == "failed")
    try:
        recorded_unique_total = int(metadata.get("unique_total", len(images)))
    except (TypeError, ValueError):
        recorded_unique_total = len(images)
    semantic_total = max(0, recorded_unique_total, len(images))

    raw_snapshot_total = metadata.get("file_total")
    if raw_snapshot_total is None:
        # 外部语义文件没有文件数快照时，不能仅凭“现有记录都完成”
        # 就断言整个图包已完成；至少要求记录数能覆盖当前文件数。
        snapshot_file_total = semantic_total
    else:
        try:
            snapshot_file_total = max(0, int(raw_snapshot_total))
        except (TypeError, ValueError):
            snapshot_file_total = -1
    snapshot_matches = snapshot_file_total == current_file_total
    all_records_done = bool(images) and caption_done == len(images)
    completion_candidate = (
        current_file_total > 0
        and semantic_total > 0
        and snapshot_matches
        and all_records_done
        and caption_done >= semantic_total
    )
    strictly_complete = bool(
        completion_candidate
        and (not verify_files or semantic_metadata_is_complete(root, metadata))
    )
    if strictly_complete:
        semantic_status = "complete"
    elif images:
        semantic_status = "partial"
    else:
        semantic_status = "none"

    return {
        "semantic_status": semantic_status,
        "semantic_caption_done": caption_done,
        "semantic_caption_total": semantic_total,
        "semantic_caption_failed": caption_failed,
        "semantic_file_total": current_file_total,
        "semantic_snapshot_matches": snapshot_matches,
        "semantic_files_changed": bool(
            not snapshot_matches or (completion_candidate and not strictly_complete)
        ),
        "semantic_metadata_migration_required": bool(
            metadata.get("metadata_migration_required")
        ),
        "semantic_metadata_migrated_from": str(
            metadata.get("migrated_from_schema_version") or ""
        ),
    }


def get_image_semantic_detail(
    pack_dir: Path | str, image_path: Path | str
) -> dict[str, Any]:
    """按当前文件路径读取语义，并报告同内容的其他文件但不共享修改。"""
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
        raise FileNotFoundError("图片不存在")

    digest = file_sha256(source)
    relative_path = source.relative_to(root).as_posix()
    category = source.parent.name
    entry_id = semantic_entry_id(digest, category, relative_path)
    same_content_paths: list[str] = []
    source_size = source.stat().st_size
    if memes_root.is_dir():
        for candidate in sorted(memes_root.rglob("*")):
            if (
                candidate == source
                or not candidate.is_file()
                or candidate.suffix.lower() not in IMAGE_EXTENSIONS
            ):
                continue
            try:
                candidate.resolve().relative_to(memes_root)
                if candidate.stat().st_size != source_size:
                    continue
                if file_sha256(candidate) == digest:
                    same_content_paths.append(candidate.relative_to(root).as_posix())
            except (OSError, RuntimeError, ValueError):
                continue

    empty_result = {
        "status": "none",
        "entry_id": entry_id,
        "content_sha256": digest,
        "relative_path": relative_path,
        "caption": "",
        "tags": [],
        "editable_tags": [],
        "visible_text": "",
        "caption_status": "pending",
        "embedding_status": "pending",
        "error": "",
        "embedding_error": "",
        "category": category,
        "category_tag": build_category_tag(category),
        "fixed_category_tags": [build_category_tag(category)],
        "category_review_status": "unchecked",
        "category_review_reason": "",
        "reclassification_status": "",
        "reclassified_from_category": "",
        "reclassified_to_category": "",
        "reclassification_reason": "",
        "reclassified_at": "",
        "can_confirm_category": False,
        "can_edit_semantic": True,
        "manual_override": False,
        "manual_modified": False,
        "generation_source": "automatic",
        "can_restore_auto": False,
        "same_content_paths": same_content_paths,
        "same_content_count": len(same_content_paths),
        "edit_scope": "current_path",
        "shared_update": False,
    }
    metadata = load_metadata(root)
    images = metadata.get("images", {})
    if not images:
        return empty_result

    item = images.get(entry_id)
    if not isinstance(item, dict):
        return empty_result

    caption = str(item.get("caption") or "").strip()
    tags = normalize_tags(item.get("tags"))
    caption_status = str(item.get("caption_status") or "pending")
    if caption_status == "done" and caption and tags:
        status = "complete"
    elif caption_status == "failed":
        status = "failed"
    else:
        status = "pending"
    category_tag = str(item.get("category_tag") or build_category_tag(category))
    editable_tags = [tag for tag in tags if not is_category_tag(tag)]
    manual_modified = bool(
        item.get("manual_override") or item.get("provenance") in {"manual", "mixed"}
    )
    error = str(item.get("error") or "").strip()
    embedding_status = str(item.get("embedding_status") or "pending")
    return {
        "status": status,
        "entry_id": entry_id,
        "content_sha256": digest,
        "relative_path": relative_path,
        "caption": caption,
        "tags": tags,
        "editable_tags": editable_tags,
        "visible_text": str(item.get("visible_text") or "").strip(),
        "caption_status": caption_status,
        "embedding_status": embedding_status,
        "error": error,
        "embedding_error": error if embedding_status == "failed" else "",
        "category": str(item.get("category") or category),
        "category_description": str(item.get("category_description") or ""),
        "category_tag": category_tag,
        "fixed_category_tags": [category_tag] if category_tag else [],
        "category_review_status": str(
            item.get("category_review_status") or "unchecked"
        ),
        "category_review_reason": str(item.get("category_review_reason") or ""),
        "reclassification_status": str(item.get("reclassification_status") or ""),
        "reclassified_from_category": str(item.get("reclassified_from_category") or ""),
        "reclassified_to_category": str(item.get("reclassified_to_category") or ""),
        "reclassification_reason": str(item.get("reclassification_reason") or ""),
        "reclassified_at": str(item.get("reclassified_at") or ""),
        "can_confirm_category": bool(
            item.get("caption_status") == "done"
            and item.get("category_review_status") == "needs_review"
        ),
        "can_edit_semantic": True,
        "manual_override": manual_modified,
        "manual_modified": manual_modified,
        "generation_source": "manual" if manual_modified else "automatic",
        "can_restore_auto": manual_modified,
        "same_content_paths": same_content_paths,
        "same_content_count": len(same_content_paths),
        "edit_scope": "current_path",
        "shared_update": False,
    }


def metadata_items(
    pack_dir: Path | str, status: str | None = None
) -> list[dict[str, Any]]:
    data = load_metadata(pack_dir, scan_legacy=False)
    items = list(data.get("images", {}).values())
    if status:
        status = str(status).strip().lower()
        predicates = {
            "all": lambda item: True,
            "pending": lambda item: (
                item.get("caption_status") == "pending"
                or item.get("embedding_status") == "pending"
            ),
            "running": lambda item: (
                item.get("caption_status") == "running"
                or item.get("embedding_status") == "running"
            ),
            "failed": lambda item: (
                item.get("caption_status") == "failed"
                or item.get("embedding_status") == "failed"
            ),
            "caption_failed": lambda item: item.get("caption_status") == "failed",
            "embedding_failed": lambda item: item.get("embedding_status") == "failed",
            "completed": lambda item: (
                item.get("caption_status") == "done"
                and item.get("embedding_status") == "done"
            ),
            "caption_done": lambda item: item.get("caption_status") == "done",
            "embedding_done": lambda item: item.get("embedding_status") == "done",
            "reclassified": lambda item: bool(item.get("reclassification_status")),
        }
        predicate = predicates.get(status)
        if predicate is None:
            items = [
                item
                for item in items
                if item.get("caption_status") == status
                or item.get("embedding_status") == status
            ]
        else:
            items = [item for item in items if predicate(item)]

    def item_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
        statuses = {
            str(item.get("caption_status") or ""),
            str(item.get("embedding_status") or ""),
        }
        # 进行中的项目始终排在第一页，随后是待处理/失败，最后才是已完成项目。
        if "running" in statuses:
            priority = 0
        elif "pending" in statuses:
            priority = 1
        elif "failed" in statuses:
            priority = 2
        elif statuses == {"done"}:
            priority = 3
        else:
            priority = 4
        return (
            priority,
            str(item.get("updated_at") or ""),
            str(item.get("relative_path") or ""),
        )

    return sorted(items, key=item_sort_key)


def get_category_review_overview(pack_dir: Path | str) -> dict[str, Any]:
    """返回主页审核筛选所需的逐文件状态与统计。"""
    root = Path(pack_dir).resolve()
    empty_statistics = {
        "auto_match": 0,
        "needs_review": 0,
        "manual_confirmed": 0,
        "manual_rejected": 0,
        "unchecked": 0,
        "total": 0,
        "reclassified": 0,
    }
    semantic_status = str(
        get_pack_semantic_summary(root).get("semantic_status") or "none"
    )
    if semantic_status == "none":
        # 未开始语义化的普通图包、以及已废弃的旧版空元数据都不属于分类审核
        # 范围。这里提前返回还能避免为整包图片计算哈希后伪造“尚未检查”。
        return {
            "available": False,
            "semantic_status": "none",
            "items": [],
            "statistics": empty_statistics,
        }

    # 主页读取必须保持只读；后台语义任务可能正持有同一份元数据，GET 接口
    # 如果在此 reconcile + save 会用旧快照覆盖刚完成的模型结果。
    metadata = load_metadata(root) if metadata_path(root).is_file() else {"images": {}}
    images = metadata.get("images", {})
    items: list[dict[str, Any]] = []
    memes_root = root / "memes"
    if memes_root.is_dir():
        for path in sorted(memes_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                path.resolve().relative_to(memes_root.resolve())
            except ValueError:
                continue
            digest = file_sha256(path)
            category = path.parent.name
            relative_path = path.relative_to(root).as_posix()
            entry_id = semantic_entry_id(digest, category, relative_path)
            record = images.get(entry_id, {})
            status = str(record.get("category_review_status") or "unchecked")
            items.append(
                {
                    "entry_id": entry_id,
                    "relative_path": relative_path,
                    "category": category,
                    "filename": path.name,
                    "category_tag": str(
                        record.get("category_tag") or build_category_tag(category)
                    ),
                    "category_review_status": status,
                    "category_review_reason": str(
                        record.get("category_review_reason") or ""
                    ),
                    "reclassification_status": str(
                        record.get("reclassification_status") or ""
                    ),
                    "reclassified_from_category": str(
                        record.get("reclassified_from_category") or ""
                    ),
                    "reclassified_to_category": str(
                        record.get("reclassified_to_category") or ""
                    ),
                    "reclassification_reason": str(
                        record.get("reclassification_reason") or ""
                    ),
                    "reclassified_at": str(record.get("reclassified_at") or ""),
                    "caption_status": str(record.get("caption_status") or "pending"),
                }
            )
    statistics = {
        status: sum(1 for item in items if item.get("category_review_status") == status)
        for status in (
            "auto_match",
            "needs_review",
            "manual_confirmed",
            "manual_rejected",
            "unchecked",
        )
    }
    statistics["total"] = len(items)
    statistics["reclassified"] = sum(
        1 for item in items if item.get("reclassification_status")
    )
    return {
        "available": bool(items),
        "semantic_status": semantic_status,
        "items": items,
        "statistics": statistics,
    }
