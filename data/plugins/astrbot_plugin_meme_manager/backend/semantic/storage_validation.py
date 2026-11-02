from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import (
    SCHEMA_VERSION,
)
from .storage_common import SemanticMetadataCompatibilityError


def _is_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _source_schema_version(data: dict[str, Any]) -> str:
    """返回规范化后的语义架构版本。

    Args:
        data: 已解析的语义元数据对象。

    Returns:
        没有版本号的旧文件返回 ``missing``，否则返回规范化后的版本字符串。
    """
    raw_version = data.get("schema_version")
    if "schema_version" not in data or raw_version is None or raw_version == "":
        return "missing"
    return str(raw_version).strip()


def _validate_metadata_records(data: dict[str, Any], source_label: str) -> None:
    """拒绝原本会被静默丢弃的不合法字段结构。

    Args:
        data: 已解析的语义元数据对象。
        source_label: 错误消息中使用的易读来源名称。

    Raises:
        SemanticMetadataCompatibilityError: 受支持的架构包含不安全字段类型或
            无效记录哈希。
    """
    images = data.get("images", {})
    if "pack_id" in data and not isinstance(data.get("pack_id"), (str, type(None))):
        raise SemanticMetadataCompatibilityError(
            f"{source_label} 的 pack_id 字段类型错误，已保持原文件不变"
        )
    if not isinstance(images, dict):
        raise SemanticMetadataCompatibilityError(
            f"{source_label} 的 images 字段不是对象，已保持原文件不变"
        )
    for key, value in images.items():
        if not isinstance(value, dict):
            raise SemanticMetadataCompatibilityError(
                f"{source_label} 的图片记录 {key!s} 不是对象，已保持原文件不变"
            )
        digest = value.get("content_sha256") or key
        if not _is_sha256(digest):
            raise SemanticMetadataCompatibilityError(
                f"{source_label} 的图片记录 {key!s} 缺少有效内容哈希，已保持原文件不变"
            )
        for field in ("tags", "auto_tags", "manual_tags"):
            raw_tags = value.get(field)
            if raw_tags is not None and not isinstance(
                raw_tags, (str, list, tuple, set)
            ):
                raise SemanticMetadataCompatibilityError(
                    f"{source_label} 的 {field} 字段类型错误，已保持原文件不变"
                )
        if "manual_override" in value and not isinstance(
            value.get("manual_override"), bool
        ):
            raise SemanticMetadataCompatibilityError(
                f"{source_label} 的 manual_override 字段类型错误，已保持原文件不变"
            )
        for field in (
            "relative_path",
            "category",
            "caption",
            "visible_text",
            "caption_status",
            "embedding_status",
            "provenance",
            "vision_model",
            "prompt_version",
            "text_hash",
            "updated_at",
            "error",
        ):
            if field in value and not isinstance(value.get(field), (str, type(None))):
                raise SemanticMetadataCompatibilityError(
                    f"{source_label} 的 {field} 字段类型错误，已保持原文件不变"
                )


def _metadata_fault(
    pack_dir: Path | str,
    message: str,
    *,
    source_schema_version: str = "",
) -> dict[str, Any]:
    """在不修改源文件的情况下构建只读状态对象。

    Args:
        pack_dir: 表情包根目录。
        message: 面向用户的失败说明。
        source_schema_version: 不受支持或格式错误的源版本。

    Returns:
        供状态页面使用、结构与元数据一致的只读错误对象。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "pack_id": Path(pack_dir).name,
        "generated_at": "",
        "images": {},
        "metadata_read_only": True,
        "metadata_error": message,
        "source_schema_version": source_schema_version,
        "requires_local_index_rebuild": True,
    }
