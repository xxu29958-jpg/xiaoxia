import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ... import config as pack_paths
from ..semantic.index import index_is_ready, load_index_manifest
from ..semantic.storage import (
    LEGACY_METADATA_BACKUP_NAME,
    import_metadata_file,
    load_metadata,
    reconcile_metadata,
    reset_local_embedding_state,
    save_metadata,
)
from .protocol import (
    PACK_EXPORT_MODES,
    PACK_TRANSFER_FORMAT,
    PACK_TRANSFER_MANIFEST,
    PACK_TRANSFER_VERSION,
    validate_pack_id,
)
from .storage_common import MIN_FREE_SPACE_RESERVE_BYTES, PackOperationGuard
from .storage_files import (
    _count_images,
    _directory_size,
    _load_manifest,
    _require_free_space,
    _require_regular_tree,
    _safe_nonnegative_int,
    _save_json,
)


def get_pack_export_capabilities(pack_id: str) -> dict:
    pack_id = validate_pack_id(pack_id, "表情包")
    pack_dir = pack_paths.PACKS_DIR / pack_id
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"表情包 {pack_id} 不存在")
    metadata = load_metadata(pack_dir)
    semantic_metadata = (pack_dir / "semantic_metadata.json").is_file()
    try:
        vector_backup_available = bool(
            semantic_metadata
            and index_is_ready(pack_paths.PLUGIN_DATA_DIR, pack_id, metadata=metadata)
        )
    except Exception:
        vector_backup_available = False
    index_manifest = load_index_manifest(pack_paths.PLUGIN_DATA_DIR, pack_id)
    return {
        "pack_id": pack_id,
        "image_count": _count_images(pack_dir / "memes"),
        "semantic_metadata": semantic_metadata,
        "vector_backup_available": vector_backup_available,
        "embedding_provider_id": str(index_manifest.get("embedding_provider_id") or ""),
        "embedding_model": str(index_manifest.get("embedding_model") or ""),
        "embedding_dimension": _safe_nonnegative_int(
            index_manifest.get("embedding_dimension", 0)
        )
        or 0,
    }


def export_pack_archive(
    pack_id: str,
    output_dir: str | None = None,
    *,
    include_semantic: bool = True,
    export_mode: str = "share",
    operation_guard: PackOperationGuard | None = None,
) -> dict:
    pack_id = validate_pack_id(pack_id, "表情包")
    export_mode = str(export_mode or "share").strip().lower()
    if export_mode not in PACK_EXPORT_MODES:
        raise ValueError("导出类型仅支持 share 或 backup")

    pack_dir = pack_paths.PACKS_DIR / pack_id
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"表情包 {pack_id} 不存在")
    _require_regular_tree(pack_dir, "导出表情包")
    if operation_guard:
        operation_guard(pack_id, "导出资源包")

    target_dir = (
        Path(output_dir).expanduser().resolve() if output_dir else pack_paths.BACKUP_DIR
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_base = target_dir / f"{pack_id}_{export_mode}_{timestamp}"
    if export_mode == "backup":
        _require_regular_tree(
            pack_paths.PLUGIN_DATA_DIR / "semantic_indexes" / pack_id,
            "导出向量索引",
        )
        capabilities = get_pack_export_capabilities(pack_id)
        if not capabilities["vector_backup_available"]:
            raise ValueError("当前表情包没有可用的完整向量，暂时不能导出带向量备份")

    pack_paths.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    source_size = _directory_size(pack_dir)
    if export_mode == "backup":
        source_size += _directory_size(
            pack_paths.PLUGIN_DATA_DIR / "semantic_indexes" / pack_id
        )
    temp_device = pack_paths.TEMP_DIR.stat().st_dev
    target_device = target_dir.stat().st_dev
    if temp_device == target_device:
        _require_free_space(
            pack_paths.TEMP_DIR,
            source_size * 2 + MIN_FREE_SPACE_RESERVE_BYTES,
            "导出表情包",
        )
    else:
        _require_free_space(
            pack_paths.TEMP_DIR,
            source_size + MIN_FREE_SPACE_RESERVE_BYTES,
            "创建导出快照",
        )
        _require_free_space(
            target_dir,
            source_size + MIN_FREE_SPACE_RESERVE_BYTES,
            "写入导出文件",
        )
    with tempfile.TemporaryDirectory(
        dir=pack_paths.TEMP_DIR, prefix="pack_export_"
    ) as tmp_dir:
        staging = Path(tmp_dir) / pack_id
        shutil.copytree(pack_dir, staging)
        (staging / ".community-origin.json").unlink(missing_ok=True)
        semantic_file = staging / "semantic_metadata.json"
        vectors_included = False
        if export_mode == "share" and include_semantic:
            (staging / LEGACY_METADATA_BACKUP_NAME).unlink(missing_ok=True)
            if semantic_file.exists():
                portable = reset_local_embedding_state(
                    import_metadata_file(semantic_file), staging
                )
                portable = reconcile_metadata(staging, external_data=portable)
                save_metadata(staging, reset_local_embedding_state(portable))
                (staging / LEGACY_METADATA_BACKUP_NAME).unlink(missing_ok=True)
        elif export_mode == "share" and semantic_file.exists():
            semantic_file.unlink()
        elif export_mode == "backup":
            source_index_dir = pack_paths.PLUGIN_DATA_DIR / "semantic_indexes" / pack_id
            shutil.copytree(source_index_dir, staging / "semantic_index")
            vectors_included = True

        raw_pack_schema_version = _load_manifest(pack_id).get("schema_version", 1)
        try:
            pack_schema_version = int(raw_pack_schema_version or 1)
        except (TypeError, ValueError):
            pack_schema_version = 1
        transfer_info = {
            "format": PACK_TRANSFER_FORMAT,
            "format_version": PACK_TRANSFER_VERSION,
            "export_mode": export_mode,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pack": {
                "id": pack_id,
                "schema_version": pack_schema_version,
            },
            "features": {
                "semantic_metadata": semantic_file.is_file(),
                "vectors": vectors_included,
            },
            "compatibility": {
                "manifest_and_memes_at_archive_root": True,
                "legacy_nonsemantic_import_supported": True,
            },
        }
        _save_json(staging / PACK_TRANSFER_MANIFEST, transfer_info)
        archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=staging)

    return {
        "pack_id": pack_id,
        "archive_path": archive_path,
        "archive_filename": Path(archive_path).name,
        "include_semantic": include_semantic,
        "export_mode": export_mode,
        "semantic_metadata_included": bool(
            transfer_info["features"]["semantic_metadata"]
        ),
        "vectors_included": vectors_included,
    }
