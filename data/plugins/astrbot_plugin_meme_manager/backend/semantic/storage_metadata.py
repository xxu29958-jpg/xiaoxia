from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    IMAGE_EXTENSIONS,
    REVIEW_CATEGORY,
    SCHEMA_VERSION,
    SemanticImage,
    build_category_tag,
    category_context_hash,
    ensure_category_tag,
    is_category_tag,
    normalize_tags,
    semantic_caption_is_complete,
    semantic_entry_id,
    utc_now,
)
from .storage_common import (
    LEGACY_SCHEMA_VERSION,
    SemanticMetadataCompatibilityError,
    _create_legacy_backup,
    _write_json_atomic,
    metadata_path,
)
from .storage_migration import migrate_legacy_metadata
from .storage_scan import load_category_descriptions, scan_images
from .storage_validation import (
    _is_sha256,
    _metadata_fault,
    _source_schema_version,
    _validate_metadata_records,
)


def _normalize_image_records(
    images: Any, category_descriptions: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """规范 v2 记录，并统一改用“内容加分类”的稳定键。"""
    if not isinstance(images, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, raw_value in images.items():
        if not isinstance(raw_value, dict):
            continue
        value = dict(raw_value)
        digest = str(value.get("content_sha256") or "").lower()
        if not _is_sha256(digest) and _is_sha256(key):
            # 允许 v2 文件仍以内容哈希作字典键；正式条目键不能反推图片内容。
            digest = str(key).lower()
        if not _is_sha256(digest):
            continue
        category = str(value.get("category") or "").strip()
        relative_path = str(value.get("relative_path") or "").replace("\\", "/")
        description = str(
            category_descriptions.get(category, value.get("category_description") or "")
        ).strip()
        entry_id = semantic_entry_id(digest, category, relative_path)
        old_context = str(value.get("category_context_hash") or "")
        new_context = category_context_hash(digest, category, description)
        context_changed = bool(old_context and old_context != new_context)
        source_tags = [
            tag for tag in normalize_tags(value.get("tags")) if not is_category_tag(tag)
        ]
        for tag_field in ("manual_tags", "auto_tags"):
            value[tag_field] = [
                tag
                for tag in normalize_tags(value.get(tag_field))
                if not is_category_tag(tag)
            ]
        value.update(
            {
                "entry_id": entry_id,
                "content_sha256": digest,
                "relative_path": relative_path,
                "category": category,
                "category_description": description,
                "category_tag": build_category_tag(category),
                "category_context_hash": new_context,
                "tags": ensure_category_tag(source_tags, category),
            }
        )
        value.setdefault(
            "caption_status",
            "done" if value.get("caption") and value.get("tags") else "pending",
        )
        if semantic_caption_is_complete(value):
            value["caption_status"] = "done"
        value.setdefault("embedding_status", "pending")
        if context_changed:
            # 分类名/描述变化只让旧确认和向量失效。已有描述属于可移植内容，
            # 普通语义化不得因此再次产生视觉模型费用。
            value["embedding_status"] = "pending"
            value["category_review_status"] = "unchecked"
            value["category_fit"] = "uncertain"
            value["category_review_reason"] = ""
            value["category_review_context_hash"] = ""
            value["manual_confirmation_context_hash"] = ""
            value["text_hash"] = ""
            if not semantic_caption_is_complete(value):
                value["caption_status"] = "pending"
        elif not old_context:
            # v2 导入数据若缺少上下文指纹，保留描述但重新做分类符合判断。
            value.setdefault("category_review_status", "unchecked")
            value.setdefault("category_review_reason", "")
            value.setdefault("category_review_context_hash", "")
            value.setdefault("manual_confirmation_context_hash", "")
            if value.get("caption_status") == "done":
                value["embedding_status"] = "pending"
                value["text_hash"] = ""
        item = SemanticImage.from_dict(value).to_dict()
        normalized[entry_id] = item
    return normalized


def load_metadata(pack_dir: Path | str, *, scan_legacy: bool = True) -> dict[str, Any]:
    """Load metadata, optionally deferring legacy image reconciliation.

    Args:
        pack_dir: Pack directory.
        scan_legacy: Whether to read image contents to map legacy records.
            Disable for page queries; explicit tasks perform the migration.

    Returns:
        Normalized metadata or a migration-required snapshot without image
        records when legacy scanning is disabled.
    """
    path = metadata_path(pack_dir)
    if not path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "pack_id": Path(pack_dir).name,
            "generated_at": utc_now(),
            "images": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return _metadata_fault(
            pack_dir,
            f"semantic_metadata.json 无法解析，原文件已保持不变：{exc}",
        )
    if not isinstance(data, dict):
        return _metadata_fault(
            pack_dir,
            "semantic_metadata.json 根节点不是对象，原文件已保持不变",
        )
    loaded_schema_version = _source_schema_version(data)
    if loaded_schema_version in {"missing", "1", LEGACY_SCHEMA_VERSION}:
        try:
            return migrate_legacy_metadata(
                data,
                scan_images(pack_dir) if scan_legacy else [],
                load_category_descriptions(pack_dir),
                Path(pack_dir).name,
            )
        except SemanticMetadataCompatibilityError as exc:
            return _metadata_fault(
                pack_dir,
                str(exc),
                source_schema_version=loaded_schema_version,
            )
    if loaded_schema_version != SCHEMA_VERSION:
        return _metadata_fault(
            pack_dir,
            (
                f"不支持 semantic_metadata.json 版本 {loaded_schema_version}；"
                "请升级程序后再处理，原文件已保持不变"
            ),
            source_schema_version=loaded_schema_version,
        )
    try:
        _validate_metadata_records(data, "semantic_metadata.json")
    except SemanticMetadataCompatibilityError as exc:
        return _metadata_fault(
            pack_dir,
            str(exc),
            source_schema_version=loaded_schema_version,
        )
    category_descriptions = load_category_descriptions(pack_dir)
    normalized = _normalize_image_records(data.get("images"), category_descriptions)
    data["schema_version"] = SCHEMA_VERSION
    data["pack_id"] = str(data.get("pack_id") or Path(pack_dir).name)
    data.setdefault("generated_at", utc_now())
    data["images"] = normalized
    return data


def semantic_metadata_is_complete(
    pack_dir: Path | str,
    data: dict[str, Any] | None = None,
    *,
    require_embeddings: bool = False,
) -> bool:
    """判断当前每张图片是否都有完整的语义元数据。

    Args:
        pack_dir: 表情包根目录。
        data: 之前已加载的元数据（如有）。
        require_embeddings: 是否还要求每张图片都已完成本地嵌入。

    Returns:
        仅当磁盘内容、扫描快照和已完成记录完全匹配时返回 True。
    """
    root = Path(pack_dir).resolve()
    memes_root = root / "memes"
    metadata_file = metadata_path(root)
    image_paths = (
        [
            path
            for path in sorted(memes_root.rglob("*"))
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if memes_root.is_dir()
        else []
    )

    metadata = data if isinstance(data, dict) else load_metadata(root)
    images = metadata.get("images", {})
    complete = False
    if metadata_file.is_file() and image_paths and isinstance(images, dict) and images:
        scanned = scan_images(root)
        current_entry_ids = {str(item.get("entry_id") or "") for item in scanned}
        try:
            recorded_file_total = int(metadata.get("file_total"))
            recorded_unique_total = int(metadata.get("unique_total"))
        except (TypeError, ValueError):
            recorded_file_total = -1
            recorded_unique_total = -1
        complete = bool(
            current_entry_ids
            and set(images) == current_entry_ids
            and recorded_file_total == len(image_paths)
            and recorded_unique_total == len(current_entry_ids)
            and all(
                isinstance(images.get(entry_id), dict)
                and semantic_caption_is_complete(images[entry_id])
                and (
                    not require_embeddings
                    or images[entry_id].get("category") == REVIEW_CATEGORY
                    or images[entry_id].get("embedding_status") == "done"
                )
                for entry_id in current_entry_ids
            )
        )
    return complete


def save_metadata(pack_dir: Path | str, data: dict[str, Any]) -> Path:
    """使用同目录临时文件 + replace，避免断电留下半份 JSON。"""
    target = metadata_path(pack_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data or {})
    if payload.get("metadata_read_only"):
        raise SemanticMetadataCompatibilityError(
            str(payload.get("metadata_error") or "语义元数据处于只读故障状态")
        )
    legacy_source: bytes | None = None
    if target.is_file():
        try:
            legacy_candidate = target.read_bytes()
            existing_data = json.loads(legacy_candidate.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SemanticMetadataCompatibilityError(
                f"现有 semantic_metadata.json 无法解析，拒绝覆盖：{exc}"
            ) from exc
        if not isinstance(existing_data, dict):
            raise SemanticMetadataCompatibilityError(
                "现有 semantic_metadata.json 根节点不是对象，拒绝覆盖"
            )
        existing_version = _source_schema_version(existing_data)
        if existing_version in {"missing", "1", LEGACY_SCHEMA_VERSION}:
            _validate_metadata_records(existing_data, "现有旧语义文件")
            legacy_source = legacy_candidate
        elif existing_version == SCHEMA_VERSION:
            _validate_metadata_records(existing_data, "现有 semantic_metadata.json")
        else:
            raise SemanticMetadataCompatibilityError(
                f"现有 semantic_metadata.json 版本 {existing_version} 不受支持，拒绝覆盖"
            )
    migrated = bool(payload.pop("metadata_migration_required", False))
    payload.pop("metadata_read_only", None)
    payload.pop("metadata_error", None)
    payload.pop("source_schema_version", None)
    payload["schema_version"] = SCHEMA_VERSION
    payload["pack_id"] = str(payload.get("pack_id") or target.parent.name)
    payload.setdefault("generated_at", utc_now())
    if migrated:
        payload.setdefault("metadata_migrated_at", utc_now())
    _validate_metadata_records(payload, "待保存语义数据")
    payload["images"] = _normalize_image_records(
        payload.get("images"), load_category_descriptions(target.parent)
    )
    if legacy_source is not None:
        _create_legacy_backup(target, legacy_source)
    _write_json_atomic(target, payload)
    return target
