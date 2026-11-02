from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

"""语义元数据的扫描、校验和原子保存。"""


LEGACY_SCHEMA_VERSION = "1.0"


LEGACY_METADATA_BACKUP_NAME = "semantic_metadata.pre-v2.backup.json"


LOCAL_EMBEDDING_FIELDS = frozenset(
    {
        "embedding_provider_id",
        "embedding_model",
        "embedding_dimension",
        "verified_embedding_dimension",
        "embedding_verified_dimension",
        "embedding_dimension_verified",
        "dimension_verified",
        "verified_dimension",
        "index_dimension",
        "index_embedding_dimension",
        "embedding_signature",
        "embedding",
        "embeddings",
        "vector",
        "vectors",
        "faiss_id",
    }
)


PORTABLE_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "pack_id",
        "generated_at",
        "file_total",
        "unique_total",
        "content_unique_total",
        "reused_duplicate_files",
        "cross_category_duplicate_entries",
        "migrated_from_schema_version",
        "metadata_migrated_at",
        "imported_from_schema_version",
    }
)


PORTABLE_IMAGE_FIELDS = frozenset(
    {
        "content_sha256",
        "relative_path",
        "entry_id",
        "category",
        "category_description",
        "category_tag",
        "category_context_hash",
        "category_fit",
        "category_review_status",
        "category_review_reason",
        "category_review_context_hash",
        "manual_confirmation_context_hash",
        "suggested_category",
        "reclassification_status",
        "reclassified_from_category",
        "reclassified_to_category",
        "reclassification_reason",
        "reclassified_at",
        "reclassification_history",
        "caption",
        "tags",
        "visible_text",
        "caption_status",
        "provenance",
        "auto_caption",
        "auto_tags",
        "auto_visible_text",
        "auto_category_fit",
        "auto_category_review_status",
        "auto_category_review_reason",
        "manual_caption",
        "manual_tags",
        "manual_visible_text",
        "manual_override",
        "vision_model",
        "prompt_version",
        "text_hash",
        "legacy_text_hash",
        "updated_at",
    }
)


class SemanticMetadataCompatibilityError(ValueError):
    """语义元数据必须保持只读时抛出。

    Args:
        message: 面向用户的兼容性或数据损坏说明。
    """


def metadata_path(pack_dir: Path | str) -> Path:
    return Path(pack_dir).resolve() / "semantic_metadata.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """通过同目录临时文件写入 JSON。

    Args:
        path: 目标 JSON 路径。
        payload: 要持久化的 JSON 对象。

    Raises:
        OSError: 无法写入临时文件或替换目标文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _create_legacy_backup(path: Path, content: bytes) -> Path:
    """创建首次写入 v2 数据前使用的稳定一次性备份。

    Args:
        path: 现有旧版语义元数据路径。
        content: 迁移前读取的精确源字节。

    Returns:
        稳定备份路径；已有备份永远不会被覆盖。

    Raises:
        OSError: 无法安全写入新备份。
    """
    backup_path = path.with_name(LEGACY_METADATA_BACKUP_NAME)
    try:
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return backup_path
    try:
        with os.fdopen(fd, "wb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def safe_relative_path(pack_dir: Path | str, relative_path: str) -> Path | None:
    """将相对路径安全地解析到资源包内，拒绝绝对路径和 .. 穿越。"""
    try:
        root = Path(pack_dir).resolve()
        raw_path = Path(str(relative_path or ""))
        if not raw_path.parts or raw_path.is_absolute() or ".." in raw_path.parts:
            return None
        candidate = (root / raw_path).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return candidate


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
