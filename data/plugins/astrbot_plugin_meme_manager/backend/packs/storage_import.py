import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ... import config as pack_paths
from ..semantic.index import index_is_ready
from ..semantic.storage import (
    import_metadata_file,
    load_metadata,
    reconcile_metadata,
    reset_local_embedding_state,
    save_metadata,
)
from .protocol import (
    validate_pack_directory,
)
from .storage_archive import (
    _extract_zip_safely,
    _find_import_root,
    _prepare_import_pack,
)
from .storage_catalog import _apply_post_install_policy, _snapshot_single_empty_pack
from .storage_common import PackOperationGuard
from .storage_files import (
    _index_bundle_details,
    _load_json,
    _load_manifest,
    _require_regular_tree,
    _restore_file_snapshot,
    _safe_nonnegative_int,
    _save_json,
)
from .storage_registry import _load_registry, _save_registry


@dataclass(frozen=True)
class PreparedArchive:
    """Named result of the prepare archive import phase."""

    pack_id: str
    prepared_pack_dir: Path
    prepared_index_dir: Path
    target_pack_dir: Path
    detected_format: str
    transfer_info: dict
    manual_data_preserved: bool
    vectors_restored: bool
    vector_warning: str


def _allocate_pack_id(base_pack_id: str) -> str:
    base = str(base_pack_id or "").strip()
    if not base:
        raise ValueError("pack_id 不能为空")
    if not (pack_paths.PACKS_DIR / base).exists():
        return base
    index = 2
    while True:
        candidate = f"{base}-{index}"
        if not (pack_paths.PACKS_DIR / candidate).exists():
            return candidate
        index += 1


def import_pack_archive(
    zip_path: Path,
    overwrite: bool = False,
    set_as_default: bool = False,
    operation_guard: PackOperationGuard | None = None,
    suggested_pack_id: str | None = None,
    embedding_provider_id: str = "",
    embedding_model: str = "",
    embedding_dimension: int = 0,
    preserve_existing_manual: bool = True,
    community_origin: dict | None = None,
) -> dict:
    if not zip_path.is_file():
        raise FileNotFoundError("压缩包不存在")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("文件不是有效的 zip 压缩包")

    previous_single_empty_pack_id = _snapshot_single_empty_pack()
    vectors_restored = False
    vector_warning = ""

    pack_paths.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=pack_paths.TEMP_DIR, prefix="pack_import_"
    ) as tmp_dir:
        workspace = Path(tmp_dir)
        prepared = _prepare_archive_import(
            community_origin=community_origin,
            embedding_dimension=embedding_dimension,
            embedding_model=embedding_model,
            embedding_provider_id=embedding_provider_id,
            overwrite=overwrite,
            preserve_existing_manual=preserve_existing_manual,
            suggested_pack_id=suggested_pack_id,
            vector_warning=vector_warning,
            vectors_restored=vectors_restored,
            workspace=workspace,
            zip_path=zip_path,
        )
        target_index_dir = (
            pack_paths.PLUGIN_DATA_DIR / "semantic_indexes" / prepared.pack_id
        )
        old_pack_dir = workspace / "replaced_pack"
        old_index_dir = workspace / "replaced_index"
        target_existed = prepared.target_pack_dir.exists()
        if target_existed and not overwrite:
            raise FileExistsError(
                f"表情包 {prepared.pack_id} 已存在，请重新检查后再导入"
            )

        # 在首次修改运行时文件前捕获全部回滚来源。快照失败时，必须让已安装
        # 表情包和索引严格保持原位。
        registry_snapshot = (
            pack_paths.REGISTRY_PATH.read_bytes()
            if pack_paths.REGISTRY_PATH.is_file()
            else None
        )
        rules_snapshot = (
            pack_paths.SELECTION_RULES_PATH.read_bytes()
            if pack_paths.SELECTION_RULES_PATH.is_file()
            else None
        )
        previous_empty_snapshot = workspace / "previous_empty_pack"
        previous_empty_index_snapshot = workspace / "previous_empty_index"
        previous_empty_id = str(previous_single_empty_pack_id or "").strip()
        previous_empty_dir = (
            pack_paths.PACKS_DIR / previous_empty_id if previous_empty_id else None
        )
        previous_empty_index = (
            pack_paths.PLUGIN_DATA_DIR / "semantic_indexes" / previous_empty_id
            if previous_empty_id
            else None
        )
        if (
            previous_empty_dir is not None
            and previous_empty_id != prepared.pack_id
            and previous_empty_dir.is_dir()
        ):
            shutil.copytree(previous_empty_dir, previous_empty_snapshot)
        if previous_empty_index is not None and previous_empty_index.is_dir():
            shutil.copytree(previous_empty_index, previous_empty_index_snapshot)

        old_pack_moved = False
        old_index_moved = False
        new_pack_installed = False
        try:
            if target_existed and overwrite:
                if operation_guard:
                    operation_guard(prepared.pack_id, "覆盖安装资源包")
                shutil.move(str(prepared.target_pack_dir), str(old_pack_dir))
                old_pack_moved = True
            if target_index_dir.exists():
                target_index_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target_index_dir), str(old_index_dir))
                old_index_moved = True

            pack_paths.PACKS_DIR.mkdir(parents=True, exist_ok=True)
            prepared.prepared_pack_dir.rename(prepared.target_pack_dir)
            new_pack_installed = True
            if prepared.vectors_restored:
                target_index_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(prepared.prepared_index_dir, target_index_dir)

            registry = _load_registry()
            installed = registry["installed_packs"]
            manifest = _load_manifest(prepared.pack_id)
            registry_entry_replaced = False
            for item in installed:
                if str(item.get("id") or "") != prepared.pack_id:
                    continue
                item.update(
                    {
                        "id": prepared.pack_id,
                        "name": str(manifest.get("name") or prepared.pack_id),
                        "version": str(manifest.get("version") or "1.0.0"),
                        "enabled": True,
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                registry_entry_replaced = True
                break

            if not registry_entry_replaced:
                installed.append(
                    {
                        "id": prepared.pack_id,
                        "name": str(manifest.get("name") or prepared.pack_id),
                        "version": str(manifest.get("version") or "1.0.0"),
                        "enabled": True,
                        "installed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            _save_registry(registry)

            post_install = _apply_post_install_policy(
                new_pack_id=prepared.pack_id,
                previous_single_empty_pack_id=previous_single_empty_pack_id,
                set_as_default=set_as_default,
                operation_guard=operation_guard,
            )
        except Exception:
            if new_pack_installed:
                shutil.rmtree(prepared.target_pack_dir, ignore_errors=True)
            if prepared.vectors_restored or old_index_moved:
                shutil.rmtree(target_index_dir, ignore_errors=True)
            if old_pack_moved and old_pack_dir.exists():
                shutil.move(str(old_pack_dir), str(prepared.target_pack_dir))
            if old_index_moved and old_index_dir.exists():
                target_index_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_index_dir), str(target_index_dir))
            if previous_empty_dir is not None and previous_empty_snapshot.is_dir():
                if not previous_empty_dir.exists():
                    shutil.copytree(previous_empty_snapshot, previous_empty_dir)
            if (
                previous_empty_index is not None
                and previous_empty_index_snapshot.is_dir()
                and not previous_empty_index.exists()
            ):
                previous_empty_index.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(previous_empty_index_snapshot, previous_empty_index)
            _restore_file_snapshot(pack_paths.REGISTRY_PATH, registry_snapshot)
            _restore_file_snapshot(pack_paths.SELECTION_RULES_PATH, rules_snapshot)
            raise

        return {
            "pack_id": prepared.pack_id,
            "name": str(manifest.get("name") or prepared.pack_id),
            "version": str(manifest.get("version") or "1.0.0"),
            "overwritten": bool(overwrite and target_existed),
            "detected_format": prepared.detected_format,
            "export_mode": str(prepared.transfer_info.get("export_mode") or "share"),
            "semantic_metadata": (
                prepared.target_pack_dir / "semantic_metadata.json"
            ).is_file(),
            "vectors_restored": prepared.vectors_restored,
            "vector_warning": prepared.vector_warning or None,
            "manual_data_preserved": prepared.manual_data_preserved,
            **post_install,
        }


def _prepare_archive_import(
    *,
    community_origin,
    embedding_dimension,
    embedding_model,
    embedding_provider_id,
    overwrite,
    preserve_existing_manual,
    suggested_pack_id,
    vector_warning,
    vectors_restored,
    workspace,
    zip_path,
):
    """Prepare archive import."""
    extract_root = workspace / "extract"
    extract_root.mkdir()
    _extract_zip_safely(zip_path, extract_root)
    pack_root, detected_format = _find_import_root(extract_root)
    prepared_pack_dir = workspace / "prepared"
    normalized_manifest, transfer_info = _prepare_import_pack(
        pack_root,
        detected_format,
        prepared_pack_dir,
        suggested_pack_id or zip_path.stem,
    )
    original_pack_id = str(normalized_manifest.get("id") or "").strip()
    if community_origin is not None:
        _save_json(prepared_pack_dir / ".community-origin.json", community_origin)
    pack_id = original_pack_id if overwrite else _allocate_pack_id(original_pack_id)
    if pack_id != original_pack_id:
        normalized_manifest["id"] = pack_id
        current_name = str(normalized_manifest.get("name") or original_pack_id)
        normalized_manifest["name"] = f"{current_name} ({pack_id})"
    _save_json(prepared_pack_dir / "manifest.json", normalized_manifest)

    compatibility_metadata = prepared_pack_dir / "memes_data.json"
    if not compatibility_metadata.is_file():
        descriptions = {
            str(category): str(
                metadata.get("description") or "请添加描述"
                if isinstance(metadata, dict)
                else metadata or "请添加描述"
            )
            for category, metadata in normalized_manifest.get("categories", {}).items()
        }
        _save_json(compatibility_metadata, descriptions)
    _require_regular_tree(prepared_pack_dir, "导入表情包")
    validate_pack_directory(prepared_pack_dir, context=f"导入包 {pack_id}")

    semantic_file = prepared_pack_dir / "semantic_metadata.json"
    declared_vectors = bool(transfer_info.get("features", {}).get("vectors", False))
    source_index_dir = pack_root / "semantic_index"
    source_index_manifest, bundled_index_path = _index_bundle_details(source_index_dir)
    vector_files_present = bundled_index_path is not None
    restore_candidate = bool(
        transfer_info.get("export_mode") == "backup"
        and declared_vectors
        and vector_files_present
        and semantic_file.is_file()
    )
    expected_dimension = _safe_nonnegative_int(embedding_dimension) or 0
    expected_signature_available = bool(
        str(embedding_provider_id or "").strip() and expected_dimension > 0
    )
    archive_dimension = _safe_nonnegative_int(
        source_index_manifest.get("embedding_dimension", 0)
    )
    signature_matches = bool(
        expected_signature_available
        and str(source_index_manifest.get("embedding_provider_id") or "")
        == str(embedding_provider_id or "")
        and str(source_index_manifest.get("embedding_model") or "")
        == str(embedding_model or "")
        and archive_dimension == expected_dimension
    )
    wants_vector_restore = bool(restore_candidate and signature_matches)
    if restore_candidate and not expected_signature_available:
        vector_warning = "当前未提供本机向量模型信息，已保留语义描述并放弃压缩包向量。"
    elif restore_candidate and not signature_matches:
        vector_warning = "压缩包向量模型或维度与本机不一致，已保留语义描述并等待重建。"

    if semantic_file.is_file():
        # 无论新旧包都先按图片内容复核路径和哈希，避免错误记录指向其他文件。
        imported_data = import_metadata_file(semantic_file)
        if wants_vector_restore:
            reconciled = reconcile_metadata(
                prepared_pack_dir, external_data=imported_data
            )
            reconciled["pack_id"] = pack_id
            save_metadata(prepared_pack_dir, reconciled)
        else:
            portable_data = reset_local_embedding_state(
                imported_data, prepared_pack_dir
            )
            reconciled = reconcile_metadata(
                prepared_pack_dir, external_data=portable_data
            )
            reconciled = reset_local_embedding_state(reconciled)
            reconciled["pack_id"] = pack_id
            save_metadata(prepared_pack_dir, reconciled)

    target_pack_dir = pack_paths.PACKS_DIR / pack_id
    manual_data_preserved = False
    existing_metadata = None
    if overwrite and target_pack_dir.is_dir():
        existing_metadata_path = target_pack_dir / "semantic_metadata.json"
        if existing_metadata_path.is_file():
            existing_metadata = load_metadata(target_pack_dir)
            if existing_metadata.get("metadata_read_only"):
                raise ValueError(
                    "现有图包语义文件无法安全读取，拒绝覆盖以保护人工内容："
                    f"{existing_metadata.get('metadata_error') or '未知错误'}"
                )
    if preserve_existing_manual and existing_metadata is not None:
        manual_data_preserved = any(
            isinstance(item, dict)
            and (
                item.get("manual_override")
                or item.get("provenance") in {"manual", "mixed"}
            )
            for item in existing_metadata.get("images", {}).values()
        )
        if manual_data_preserved:
            merged = reconcile_metadata(
                prepared_pack_dir,
                external_data=existing_metadata,
                prefer_external_manual=True,
            )
            merged["pack_id"] = pack_id
            save_metadata(prepared_pack_dir, reset_local_embedding_state(merged))
            wants_vector_restore = False
            vector_warning = (
                "已保留现有图包的人工语义；为避免错配，压缩包向量已放弃并等待重建。"
            )

    prepared_index_dir = workspace / "prepared_index"
    if wants_vector_restore:
        shutil.copytree(source_index_dir, prepared_index_dir)
        index_manifest_path = prepared_index_dir / "index_manifest.json"
        index_manifest = _load_json(index_manifest_path, {})
        if isinstance(index_manifest, dict):
            index_manifest["pack_id"] = pack_id
            _save_json(index_manifest_path, index_manifest)

        validation_runtime = workspace / "vector_validation"
        validation_index_dir = validation_runtime / "semantic_indexes" / pack_id
        validation_index_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prepared_index_dir, validation_index_dir)
        try:
            vectors_restored = index_is_ready(
                validation_runtime,
                pack_id,
                metadata=load_metadata(prepared_pack_dir),
                embedding_provider_id=str(embedding_provider_id or ""),
                embedding_model=str(embedding_model or ""),
                embedding_dimension=expected_dimension,
            )
        except Exception:
            vectors_restored = False
        if not vectors_restored:
            vector_warning = "向量索引校验未通过，已保留语义描述并改为待重建状态。"
            portable = reset_local_embedding_state(load_metadata(prepared_pack_dir))
            portable["pack_id"] = pack_id
            save_metadata(prepared_pack_dir, portable)
    elif declared_vectors and not vector_warning:
        vector_warning = "压缩包缺少完整向量索引，已按无向量包导入。"

    return PreparedArchive(
        pack_id=pack_id,
        prepared_pack_dir=prepared_pack_dir,
        prepared_index_dir=prepared_index_dir,
        target_pack_dir=target_pack_dir,
        detected_format=detected_format,
        transfer_info=transfer_info,
        manual_data_preserved=manual_data_preserved,
        vectors_restored=vectors_restored,
        vector_warning=vector_warning,
    )
