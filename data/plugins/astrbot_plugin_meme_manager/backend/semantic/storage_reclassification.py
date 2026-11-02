from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .models import (
    IMAGE_EXTENSIONS,
    REVIEW_CATEGORY,
    REVIEW_CATEGORY_DESCRIPTION,
    SemanticImage,
    build_category_tag,
    normalize_tags,
    semantic_entry_id,
    text_hash,
    utc_now,
)
from .storage_common import _write_json_atomic, file_sha256, safe_relative_path
from .storage_metadata import save_metadata
from .storage_scan import load_category_descriptions, scan_images


def _is_safe_category_key(category: str) -> bool:
    value = str(category or "").strip()
    return bool(
        value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and Path(value).name == value
    )


def _ensure_pack_category(pack_dir: Path, category: str, description: str) -> None:
    """创建安全的表情包分类并同步其元数据。

    Args:
        pack_dir: 表情包根目录。
        category: 要创建的准确分类键。
        description: 易读的分类描述。

    Raises:
        ValueError: 分类键不安全。
        OSError: 无法写入目录或元数据文件。
    """
    if not _is_safe_category_key(category):
        raise ValueError("自动复核分类名称无效")
    memes_root = pack_dir / "memes"
    (memes_root / category).mkdir(parents=True, exist_ok=True)

    descriptions = load_category_descriptions(pack_dir)
    if descriptions.get(category) != description:
        descriptions[category] = description
        _write_json_atomic(pack_dir / "memes_data.json", descriptions)

    manifest_path = pack_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("schema_version", 1)
    manifest.setdefault("id", pack_dir.name)
    manifest.setdefault("name", f"Meme Pack {pack_dir.name}")
    manifest.setdefault("version", "1.0.0")
    manifest.setdefault("description", "Runtime-managed meme pack")
    manifest.setdefault("tags", ["runtime"])
    categories = manifest.get("categories")
    if not isinstance(categories, dict):
        categories = {}
        manifest["categories"] = categories
    expected = {"description": description}
    if categories.get(category) != expected:
        categories[category] = expected
        _write_json_atomic(manifest_path, manifest)


def apply_conflict_reclassifications(
    pack_dir: Path | str, data: dict[str, Any]
) -> dict[str, Any]:
    """移动明确的分类冲突项并持久化其审计记录。

    Args:
        pack_dir: 表情包根目录。
        data: 当前 v2 架构的语义元数据。

    Returns:
        已移动、移入已有分类、移入审核分类和已跳过项目的数量。

    Raises:
        OSError: 文件移动或元数据写入失败。
        ValueError: 生成的目标路径超出表情包目录。
    """
    root = Path(pack_dir).resolve()
    memes_root = (root / "memes").resolve()
    images = data.get("images")
    if not isinstance(images, dict):
        return {"moved": 0, "to_existing": 0, "to_review": 0, "skipped": 0}

    descriptions = load_category_descriptions(root)
    existing_categories = (
        {
            path.name
            for path in memes_root.iterdir()
            if path.is_dir() and _is_safe_category_key(path.name)
        }
        if memes_root.is_dir()
        else set()
    )
    original_images = copy.deepcopy(images)
    completed_moves: list[tuple[Path, Path]] = []
    result = {"moved": 0, "to_existing": 0, "to_review": 0, "skipped": 0}

    try:
        for old_entry_id, raw_item in list(images.items()):
            if not isinstance(raw_item, dict):
                continue
            item = SemanticImage.from_dict(raw_item)
            if (
                item.caption_status != "done"
                or item.category_fit != "conflict"
                or item.category_review_status == "manual_confirmed"
            ):
                continue
            if item.manual_override or item.provenance in {"manual", "mixed"}:
                result["skipped"] += 1
                continue

            source = safe_relative_path(root, item.relative_path)
            if (
                source is None
                or not source.is_file()
                or source.parent.name != item.category
                or file_sha256(source) != item.content_sha256
            ):
                result["skipped"] += 1
                continue

            suggested = str(item.suggested_category or "").strip()
            use_existing = bool(
                suggested
                and suggested != item.category
                and suggested != REVIEW_CATEGORY
                and suggested in existing_categories
            )
            target_category = suggested if use_existing else REVIEW_CATEGORY
            if item.category == target_category:
                result["skipped"] += 1
                continue
            if target_category == REVIEW_CATEGORY:
                _ensure_pack_category(
                    root, REVIEW_CATEGORY, REVIEW_CATEGORY_DESCRIPTION
                )
                descriptions[REVIEW_CATEGORY] = REVIEW_CATEGORY_DESCRIPTION
                existing_categories.add(REVIEW_CATEGORY)

            target_dir = (memes_root / target_category).resolve()
            try:
                target_dir.relative_to(memes_root)
            except ValueError as exc:
                raise ValueError("自动重分类目标路径无效") from exc
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            suffix = source.suffix
            stem = source.stem
            index = 1
            while target.exists():
                target = target_dir / f"{stem}_{index}{suffix}"
                index += 1
            source.rename(target)
            completed_moves.append((target, source))

            moved_at = utc_now()
            reason = str(item.category_review_reason or "图片与原分类明显不符").strip()
            move_status = "auto_reclassified" if use_existing else "moved_to_review"
            history_item = {
                "from_category": item.category,
                "to_category": target_category,
                "reason": reason,
                "status": move_status,
                "at": moved_at,
            }
            target_relative_path = target.relative_to(root).as_posix()
            new_entry_id = semantic_entry_id(
                item.content_sha256, target_category, target_relative_path
            )
            existing_target = images.get(new_entry_id)
            keep_existing_target = bool(
                isinstance(existing_target, dict)
                and new_entry_id != old_entry_id
                and (
                    existing_target.get("manual_override")
                    or existing_target.get("provenance") in {"manual", "mixed"}
                    or (
                        existing_target.get("caption_status") == "done"
                        and existing_target.get("caption")
                        and existing_target.get("tags")
                    )
                )
            )
            if keep_existing_target:
                moved_item = SemanticImage.from_dict(existing_target)
            else:
                moved_value = dict(raw_item)
                previous_fixed_tag = build_category_tag(item.category)
                moved_value.update(
                    {
                        "relative_path": target_relative_path,
                        "category": target_category,
                        "category_description": str(
                            descriptions.get(target_category) or ""
                        ),
                        "category_context_hash": "",
                        "category_review_status": "unchecked",
                        "category_review_context_hash": "",
                        "manual_confirmation_context_hash": "",
                        "category_tag": "",
                        "tags": [
                            tag
                            for tag in normalize_tags(raw_item.get("tags"))
                            if tag != previous_fixed_tag
                        ],
                        "auto_tags": [
                            tag
                            for tag in normalize_tags(raw_item.get("auto_tags"))
                            if tag != previous_fixed_tag
                        ],
                        "manual_tags": [
                            tag
                            for tag in normalize_tags(raw_item.get("manual_tags"))
                            if tag != previous_fixed_tag
                        ],
                    }
                )
                moved_item = SemanticImage.from_dict(moved_value)

            moved_item.reclassification_history.append(history_item)
            moved_item.reclassification_history = moved_item.reclassification_history[
                -20:
            ]
            moved_item.reclassification_status = move_status
            moved_item.reclassified_from_category = item.category
            moved_item.reclassified_to_category = target_category
            moved_item.reclassification_reason = reason
            moved_item.reclassified_at = moved_at
            moved_item.suggested_category = suggested if use_existing else ""
            if moved_item.category_review_status != "manual_confirmed":
                moved_item.category_fit = "uncertain"
                moved_item.category_review_status = "needs_review"
                moved_item.category_review_reason = (
                    f"已从 {item.category} 自动移至 {target_category}：{reason}"
                )[:500]
                moved_item.category_review_context_hash = (
                    moved_item.category_context_hash
                )
                moved_item.manual_confirmation_context_hash = ""
            moved_item.embedding_status = "pending"
            moved_item.text_hash = text_hash(moved_item.vector_text)
            moved_item.updated_at = moved_at

            images.pop(old_entry_id, None)
            images[new_entry_id] = moved_item.to_dict()
            result["moved"] += 1
            if use_existing:
                result["to_existing"] += 1
            else:
                result["to_review"] += 1

        if result["moved"]:
            scanned = scan_images(root)
            scanned_entry_ids = {item["entry_id"] for item in scanned}
            data["file_total"] = sum(
                1
                for path in memes_root.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            )
            data["unique_total"] = len(scanned_entry_ids)
            data["content_unique_total"] = len(
                {item["content_sha256"] for item in scanned}
            )
            data["reused_duplicate_files"] = max(
                0,
                data["file_total"]
                - len(
                    {(entry["content_sha256"], entry["category"]) for entry in scanned}
                ),
            )
            data["cross_category_duplicate_entries"] = max(
                0, data["unique_total"] - data["content_unique_total"]
            )
            data["requires_local_index_rebuild"] = True
            data["last_reclassification_at"] = utc_now()
            save_metadata(root, data)
    except Exception:
        for target, source in reversed(completed_moves):
            if target.is_file() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.rename(source)
        data["images"] = original_images
        raise
    return result
