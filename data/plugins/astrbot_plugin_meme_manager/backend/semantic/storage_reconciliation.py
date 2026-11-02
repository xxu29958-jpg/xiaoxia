from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .models import (
    IMAGE_EXTENSIONS,
    SCHEMA_VERSION,
    SemanticImage,
    build_category_tag,
    build_semantic_text,
    category_context_hash,
    ensure_category_tag,
    is_category_tag,
    normalize_tags,
    semantic_caption_is_complete,
    text_hash,
    utc_now,
)
from .storage_common import (
    LEGACY_SCHEMA_VERSION,
    PORTABLE_IMAGE_FIELDS,
    PORTABLE_METADATA_FIELDS,
    SemanticMetadataCompatibilityError,
    safe_relative_path,
)
from .storage_metadata import _normalize_image_records, load_metadata, save_metadata
from .storage_migration import migrate_legacy_metadata
from .storage_scan import load_category_descriptions, scan_images
from .storage_validation import _source_schema_version, _validate_metadata_records


def reset_local_embedding_state(
    data: dict[str, Any], pack_dir: Path | str | None = None
) -> dict[str, Any]:
    """移除本机向量状态，并在提供图包目录时迁移旧语义数据。

    Args:
        data: 已解析的语义元数据。
        pack_dir: v1 或无版本数据迁移所需的目标表情包目录。

    Returns:
        已清除本地向量状态、可移植的当前架构元数据。

    Raises:
        SemanticMetadataCompatibilityError: 旧版数据缺少目标表情包上下文、
            包含不安全字段，或使用不受支持的未来版本。
    """
    if not isinstance(data, dict):
        raise SemanticMetadataCompatibilityError("语义元数据根节点不是对象")
    source_version = _source_schema_version(data)
    if source_version in {"missing", "1", LEGACY_SCHEMA_VERSION}:
        if pack_dir is None:
            raise SemanticMetadataCompatibilityError(
                "旧语义数据需要图包目录才能迁移，拒绝清空或覆盖"
            )
        data = migrate_legacy_metadata(
            data,
            scan_images(pack_dir),
            load_category_descriptions(pack_dir),
            Path(pack_dir).name,
        )
    elif source_version == SCHEMA_VERSION:
        _validate_metadata_records(data, "语义元数据")
    else:
        raise SemanticMetadataCompatibilityError(
            f"不支持 semantic_metadata.json 版本 {source_version}"
        )
    payload = {
        key: copy.deepcopy(data[key]) for key in PORTABLE_METADATA_FIELDS if key in data
    }
    images = data.get("images", {})
    normalized_images: dict[str, dict[str, Any]] = {}
    if isinstance(images, dict):
        for entry_id, value in images.items():
            if not isinstance(value, dict):
                continue
            item = {
                key: copy.deepcopy(value[key])
                for key in PORTABLE_IMAGE_FIELDS
                if key in value
            }
            item["embedding_status"] = "pending"
            item["error"] = None
            normalized_images[str(entry_id)] = item
    payload["images"] = normalized_images
    payload["requires_local_index_rebuild"] = True
    return payload


def reconcile_metadata(
    pack_dir: Path | str,
    external_data: dict[str, Any] | None = None,
    *,
    prefer_external_manual: bool = False,
) -> dict[str, Any]:
    """合并磁盘与语义记录，并按具体文件路径隔离人工内容和向量状态。"""
    root = Path(pack_dir).resolve()
    descriptions = load_category_descriptions(root)
    existing = load_metadata(root)
    if existing.get("metadata_read_only"):
        raise SemanticMetadataCompatibilityError(
            str(existing.get("metadata_error") or "语义元数据处于只读故障状态")
        )
    scanned = scan_images(root)
    normalized_external_data: dict[str, Any] = {}
    external_migrated = False
    if external_data is not None:
        if not isinstance(external_data, dict):
            raise SemanticMetadataCompatibilityError("外部语义元数据根节点不是对象")
        external_version = _source_schema_version(external_data)
        if external_version in {"missing", "1", LEGACY_SCHEMA_VERSION}:
            normalized_external_data = migrate_legacy_metadata(
                external_data,
                scanned,
                descriptions,
                root.name,
            )
            external_migrated = True
        elif external_version == SCHEMA_VERSION:
            _validate_metadata_records(external_data, "外部 semantic_metadata.json")
            normalized_external_data = external_data
        else:
            raise SemanticMetadataCompatibilityError(
                f"不支持外部 semantic_metadata.json 版本 {external_version}"
            )
    external_images = _normalize_image_records(
        normalized_external_data.get("images", {}),
        descriptions,
    )
    local_images = existing.get("images", {})
    scanned_by_id = {item["entry_id"]: item for item in scanned}

    def records_for_content(
        source: dict[str, dict[str, Any]], digest: str
    ) -> list[dict[str, Any]]:
        return [
            item
            for item in source.values()
            if isinstance(item, dict)
            and str(item.get("content_sha256") or "").lower() == digest
        ]

    def choose_previous(entry_id: str, digest: str) -> tuple[dict[str, Any], bool]:
        exact_local = local_images.get(entry_id)
        exact_external = external_images.get(entry_id)
        manual_exact_candidates = (
            (exact_external, exact_local)
            if prefer_external_manual
            else (exact_local, exact_external)
        )
        for candidate in manual_exact_candidates:
            if isinstance(candidate, dict) and (
                candidate.get("provenance") in {"manual", "mixed"}
                or candidate.get("manual_override")
            ):
                return candidate, True
        for candidate in (exact_local, exact_external):
            if isinstance(candidate, dict) and (
                candidate.get("caption") and candidate.get("tags")
            ):
                return candidate, True
        for candidate in (exact_local, exact_external):
            if isinstance(candidate, dict):
                return candidate, True
        # 移动/重命名时可复用内容描述作为起点，但分类判断、固定标签、人工
        # 确认和向量一律失效，下一轮必须携带新分类重新识别。
        local_content_candidates = records_for_content(local_images, digest)
        external_content_candidates = records_for_content(external_images, digest)
        content_candidates = local_content_candidates + external_content_candidates
        manual_content_candidates = (
            external_content_candidates + local_content_candidates
            if prefer_external_manual
            else content_candidates
        )
        manual_candidates = [
            item
            for item in manual_content_candidates
            if item.get("provenance") in {"manual", "mixed"}
            or item.get("manual_override")
        ]
        reusable = manual_candidates or [
            item
            for item in content_candidates
            if item.get("caption") and item.get("tags")
        ]
        if not reusable:
            return {}, False
        candidate = dict(reusable[0])
        source_entry_id = str(candidate.get("entry_id") or "")
        if source_entry_id in scanned_by_id:
            # 来源记录仍在磁盘上，说明这是另一个分类中新出现的重复图，而不是
            # 原图被移动/分类被重命名。人工内容只属于来源条目，不能把保护状态
            # 复制到新分类，否则视觉模型无法按新分类重新生成语义。
            was_manual = bool(
                candidate.get("manual_override")
                or candidate.get("provenance") in {"manual", "mixed"}
            )
            candidate["manual_override"] = False
            candidate["manual_caption"] = ""
            candidate["manual_tags"] = []
            candidate["manual_visible_text"] = ""
            candidate["provenance"] = "ai"
            if was_manual:
                candidate["caption"] = str(candidate.get("auto_caption") or "")
                candidate["tags"] = normalize_tags(candidate.get("auto_tags"))
                candidate["visible_text"] = str(
                    candidate.get("auto_visible_text") or ""
                )
                candidate["category_fit"] = str(
                    candidate.get("auto_category_fit") or "uncertain"
                )
                candidate["category_review_status"] = str(
                    candidate.get("auto_category_review_status") or "unchecked"
                )
                candidate["category_review_reason"] = str(
                    candidate.get("auto_category_review_reason") or ""
                )
        return candidate, False

    images: dict[str, dict[str, Any]] = {}
    for entry_id, scan in scanned_by_id.items():
        digest = scan["content_sha256"]
        previous, exact_context = choose_previous(entry_id, digest)
        item = dict(previous)
        category = scan["category"]
        description = str(descriptions.get(category, "")).strip()
        current_context = category_context_hash(digest, category, description)
        previous_context = str(item.get("category_context_hash") or "")
        item.update(
            {
                "entry_id": entry_id,
                "content_sha256": digest,
                "relative_path": scan["relative_path"],
                "category": category,
                "category_description": description,
                "category_tag": build_category_tag(category),
                "category_context_hash": current_context,
            }
        )
        item.setdefault("caption", "")
        source_tags = [
            tag for tag in normalize_tags(item.get("tags")) if not is_category_tag(tag)
        ]
        item["tags"] = ensure_category_tag(source_tags, category)
        for tag_field in ("manual_tags", "auto_tags"):
            item[tag_field] = [
                tag
                for tag in normalize_tags(item.get(tag_field))
                if not is_category_tag(tag)
            ]
        item.setdefault("visible_text", "")
        item.setdefault(
            "caption_status",
            "done" if item.get("caption") and item.get("tags") else "pending",
        )
        item.setdefault("embedding_status", "pending")
        item.setdefault("provenance", "ai")
        item.setdefault("auto_tags", item.get("tags", []))
        item.setdefault("manual_tags", [])
        item.setdefault("manual_override", False)
        item.setdefault("prompt_version", "meme-semantic-v1")
        item.setdefault("error", None)
        item.setdefault("category_fit", "uncertain")
        item.setdefault("category_review_status", "unchecked")
        item.setdefault("category_review_reason", "")
        item.setdefault("category_review_context_hash", "")
        item.setdefault("manual_confirmation_context_hash", "")
        if semantic_caption_is_complete(item):
            item["caption_status"] = "done"
        context_changed = bool(previous_context and previous_context != current_context)
        if not exact_context or context_changed:
            item["embedding_status"] = "pending"
            item["category_review_status"] = "unchecked"
            item["category_fit"] = "uncertain"
            item["category_review_reason"] = ""
            item["category_review_context_hash"] = ""
            item["manual_confirmation_context_hash"] = ""
            item["text_hash"] = ""
            item["error"] = None
            if not semantic_caption_is_complete(item):
                item["caption_status"] = "pending"
        if item.get("caption_status") == "done" and (
            not str(item.get("caption") or "").strip() or not item.get("tags")
        ):
            item["caption_status"] = "pending"
            item["embedding_status"] = "pending"
        item["updated_at"] = utc_now()
        current_text = build_semantic_text(
            item.get("caption", ""),
            item.get("tags", []),
            item.get("visible_text", ""),
            item.get("category", ""),
            item.get("category_description", ""),
        )
        calculated_hash = text_hash(current_text)
        if item.get("text_hash") and item["text_hash"] != calculated_hash:
            item["embedding_status"] = "pending"
        item["text_hash"] = calculated_hash if item.get("caption") else ""
        normalized_item = SemanticImage.from_dict(item).to_dict()
        images[entry_id] = normalized_item
    # 仅保留本次外部导入中缺图的记录供用户排查；本地已删除、移动或重命名
    # 产生的旧记录必须移除，否则旧分类标签会进入下一次索引队列。
    for source in (external_images,):
        for entry_id, value in source.items():
            if entry_id in images or not isinstance(value, dict):
                continue
            item = dict(value)
            if safe_relative_path(root, item.get("relative_path", "")) is None:
                item["relative_path"] = ""
            item["caption_status"] = "pending"
            item["embedding_status"] = "pending"
            item["error"] = "图片不存在或内容哈希不匹配"
            item["updated_at"] = utc_now()
            images[str(entry_id)] = SemanticImage.from_dict(item).to_dict()
    result = dict(existing)
    if external_migrated:
        result["imported_from_schema_version"] = str(
            normalized_external_data.get("migrated_from_schema_version") or "1.0"
        )
    result["schema_version"] = SCHEMA_VERSION
    result["pack_id"] = root.name
    result["images"] = images
    result["file_total"] = (
        sum(
            1
            for path in (root / "memes").rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        if (root / "memes").is_dir()
        else 0
    )
    result["unique_total"] = len(scanned_by_id)
    result["content_unique_total"] = len(
        {scan["content_sha256"] for scan in scanned_by_id.values()}
    )
    result["reused_duplicate_files"] = max(
        0,
        result["file_total"]
        - len(
            {
                (scan["content_sha256"], scan["category"])
                for scan in scanned_by_id.values()
            }
        ),
    )
    result["cross_category_duplicate_entries"] = max(
        0,
        len(
            {
                (scan["content_sha256"], scan["category"])
                for scan in scanned_by_id.values()
            }
        )
        - result["content_unique_total"],
    )
    result["requires_local_index_rebuild"] = bool(
        set(local_images) != set(images)
        or any(
            item.get("embedding_status") != "done"
            for entry_id, item in images.items()
            if entry_id in scanned_by_id and isinstance(item, dict)
        )
    )
    return result


def import_metadata_file(path: Path | str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("语义元数据文件不存在")
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("images", {}), dict):
        raise ValueError("语义元数据格式无效")
    return data


def invalidate_semantic_metadata(pack_dir: Path | str) -> dict[str, Any]:
    """文件或分类变更后立即刷新元数据，使旧标签、确认和向量失效。"""
    metadata = reconcile_metadata(pack_dir)
    save_metadata(pack_dir, metadata)
    return metadata
