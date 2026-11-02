from __future__ import annotations

import copy
import re
from typing import Any

from .models import (
    SCHEMA_VERSION,
    SemanticImage,
    build_category_tag,
    category_context_hash,
    ensure_category_tag,
    is_category_tag,
    normalize_tags,
    utc_now,
)
from .storage_common import (
    LEGACY_SCHEMA_VERSION,
    LOCAL_EMBEDDING_FIELDS,
    SemanticMetadataCompatibilityError,
)
from .storage_validation import _source_schema_version, _validate_metadata_records


def _legacy_reusable_record(
    source: dict[str, Any], *, preserve_manual: bool
) -> dict[str, Any]:
    """转换一条 v1 记录，但不添加从磁盘推导的分类字段。

    Args:
        source: 一条已校验的 v1 图片记录。
        preserve_manual: 当前路径是否为完全一致的原始相对路径。

    Returns:
        等待分类审核和嵌入处理的 v2 兼容内容记录。
    """
    source = dict(source)
    manual = bool(
        source.get("manual_override") or source.get("provenance") in {"manual", "mixed"}
    )
    if manual and not preserve_manual:
        caption = str(source.get("auto_caption") or "").strip()
        tags = normalize_tags(source.get("auto_tags")) if caption else []
        visible_text = str(source.get("auto_visible_text") or "").strip()
        provenance = "ai"
    else:
        caption = str(source.get("caption") or "").strip()
        tags = normalize_tags(source.get("tags"))
        visible_text = str(source.get("visible_text") or "").strip()
        provenance = str(source.get("provenance") or "ai").strip() or "ai"
    tags = [tag for tag in tags if not is_category_tag(tag)]
    raw_status = str(source.get("caption_status") or "").strip()
    if raw_status == "running":
        caption_status = "pending"
    elif raw_status in {"pending", "done", "failed"}:
        caption_status = raw_status
    else:
        caption_status = "done" if caption and tags else "pending"
    if caption_status == "done" and (not caption or not tags):
        caption_status = "pending"
    error = source.get("error")
    if error is not None:
        error = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(error))[:1000]
    legacy_hash = str(source.get("text_hash") or "").strip()
    return {
        "caption": caption,
        "tags": tags,
        "visible_text": visible_text,
        "caption_status": caption_status,
        "embedding_status": "pending",
        "provenance": provenance,
        "auto_caption": str(source.get("auto_caption") or "").strip(),
        "auto_tags": [
            tag
            for tag in normalize_tags(source.get("auto_tags"))
            if not is_category_tag(tag)
        ],
        "auto_visible_text": str(source.get("auto_visible_text") or "").strip(),
        "manual_caption": (
            str(source.get("manual_caption") or source.get("caption") or "").strip()
            if manual and preserve_manual
            else ""
        ),
        "manual_tags": (
            [
                tag
                for tag in normalize_tags(
                    source.get("manual_tags") or source.get("tags")
                )
                if not is_category_tag(tag)
            ]
            if manual and preserve_manual
            else []
        ),
        "manual_visible_text": (
            str(
                source.get("manual_visible_text") or source.get("visible_text") or ""
            ).strip()
            if manual and preserve_manual
            else ""
        ),
        "manual_override": bool(manual and preserve_manual),
        "vision_model": str(source.get("vision_model") or "").strip(),
        "prompt_version": str(source.get("prompt_version") or "").strip(),
        "text_hash": "",
        "legacy_text_hash": legacy_hash,
        "updated_at": str(source.get("updated_at") or utc_now()),
        "error": error,
        "category_fit": "uncertain",
        "category_review_status": "unchecked",
        "category_review_reason": "",
        "category_review_context_hash": "",
        "manual_confirmation_context_hash": "",
        "suggested_category": "",
    }


def migrate_legacy_metadata(
    data: dict[str, Any],
    scanned_images: list[dict[str, str]],
    category_descriptions: dict[str, str],
    pack_id: str,
) -> dict[str, Any]:
    """以纯函数方式将已知 v1 或无版本元数据迁移为路径范围的 v2 数据。

    Args:
        data: 已解析的 v1 语义元数据。
        scanned_images: 目标表情包当前的磁盘图片扫描结果。
        category_descriptions: 目标表情包当前的分类描述。
        pack_id: 目标表情包标识符。

    Returns:
        内存中的 v2 元数据；调用方决定何时在表情包锁保护下持久化。

    Raises:
        SemanticMetadataCompatibilityError: 来源不是受支持的旧版架构，或包含
            不安全的字段类型。
    """
    if not isinstance(data, dict):
        raise SemanticMetadataCompatibilityError("旧语义文件根节点不是对象")
    source_version = _source_schema_version(data)
    if source_version not in {"missing", "1", LEGACY_SCHEMA_VERSION}:
        raise SemanticMetadataCompatibilityError(
            f"不支持迁移 semantic_metadata.json 版本 {source_version}"
        )
    _validate_metadata_records(data, "旧 semantic_metadata.json")
    raw_images = data.get("images", {})
    legacy_records: list[dict[str, Any]] = []
    for key, raw_value in raw_images.items():
        value = dict(raw_value)
        value["content_sha256"] = str(value.get("content_sha256") or key).lower()
        value["relative_path"] = str(value.get("relative_path") or "").replace(
            "\\", "/"
        )
        legacy_records.append(value)

    images: dict[str, dict[str, Any]] = {}
    for scan in scanned_images:
        digest = scan["content_sha256"]
        relative_path = scan["relative_path"]
        exact = next(
            (
                item
                for item in legacy_records
                if item["content_sha256"] == digest
                and item["relative_path"] == relative_path
            ),
            None,
        )
        candidates = [
            item for item in legacy_records if item["content_sha256"] == digest
        ]
        source = exact or next(
            (
                item
                for item in candidates
                if item.get("caption") and normalize_tags(item.get("tags"))
            ),
            candidates[0] if candidates else {},
        )
        item = _legacy_reusable_record(source, preserve_manual=exact is not None)
        category = scan["category"]
        description = str(category_descriptions.get(category) or "").strip()
        item.update(
            {
                "entry_id": scan["entry_id"],
                "content_sha256": digest,
                "relative_path": relative_path,
                "category": category,
                "category_description": description,
                "category_tag": build_category_tag(category),
                "category_context_hash": category_context_hash(
                    digest, category, description
                ),
                "tags": ensure_category_tag(item.get("tags"), category),
            }
        )
        item["manual_tags"] = [
            tag for tag in item.get("manual_tags", []) if not is_category_tag(tag)
        ]
        item["auto_tags"] = [
            tag for tag in item.get("auto_tags", []) if not is_category_tag(tag)
        ]
        images[scan["entry_id"]] = SemanticImage.from_dict(item).to_dict()

    result = {
        key: copy.deepcopy(value)
        for key, value in data.items()
        if key not in LOCAL_EMBEDDING_FIELDS and key != "images"
    }
    result.update(
        {
            "schema_version": SCHEMA_VERSION,
            "pack_id": str(pack_id or data.get("pack_id") or ""),
            "images": images,
            "file_total": len(scanned_images),
            "unique_total": len(scanned_images),
            "content_unique_total": len(
                {item["content_sha256"] for item in scanned_images}
            ),
            "requires_local_index_rebuild": True,
            "metadata_migration_required": True,
            "migrated_from_schema_version": (
                LEGACY_SCHEMA_VERSION if source_version == "1" else source_version
            ),
            "legacy_index_compatible": False,
            "legacy_semantic_content_count": len(raw_images),
        }
    )
    result.setdefault("generated_at", utc_now())
    return result
