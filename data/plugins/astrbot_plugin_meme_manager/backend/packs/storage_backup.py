import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ... import config as pack_paths
from ...config import RUNTIME_SCHEMA_VERSION
from ..semantic.storage import (
    LEGACY_METADATA_BACKUP_NAME,
    import_metadata_file,
    load_metadata,
    reconcile_metadata,
    reset_local_embedding_state,
    save_metadata,
)
from .protocol import (
    validate_pack_directory,
    validate_pack_id,
)
from .storage_archive import _extract_zip_safely
from .storage_common import PackOperationGuard
from .storage_files import (
    _load_manifest,
    _require_regular_tree,
    _restore_file_snapshot,
    _save_json,
)
from .storage_registry import (
    _load_registry,
    _load_selection_rules,
    _normalize_installed_packs,
    _save_registry,
    _save_selection_rules,
    _validate_and_normalize_rules,
)


def export_runtime_backup(
    output_dir: str | None = None,
    operation_guard: PackOperationGuard | None = None,
) -> dict:
    target_dir = (
        Path(output_dir).expanduser().resolve() if output_dir else pack_paths.BACKUP_DIR
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    pack_paths.TEMP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_base = target_dir / f"runtime_backup_{timestamp}"

    with tempfile.TemporaryDirectory(
        dir=pack_paths.TEMP_DIR, prefix="runtime_backup_"
    ) as tmp_dir:
        tmp_root = Path(tmp_dir)
        snapshot_root = tmp_root / "runtime_backup"
        snapshot_root.mkdir(parents=True, exist_ok=True)

        if pack_paths.REGISTRY_PATH.is_file():
            shutil.copy2(pack_paths.REGISTRY_PATH, snapshot_root / "registry.json")
        if pack_paths.SELECTION_RULES_PATH.is_file():
            shutil.copy2(
                pack_paths.SELECTION_RULES_PATH, snapshot_root / "selection_rules.json"
            )
        if pack_paths.COMMUNITY_CACHE_PATH.is_file():
            shutil.copy2(
                pack_paths.COMMUNITY_CACHE_PATH, snapshot_root / "community_cache.json"
            )
        if pack_paths.PACKS_DIR.is_dir():
            if operation_guard:
                for pack_dir in pack_paths.PACKS_DIR.iterdir():
                    if pack_dir.is_dir():
                        operation_guard(pack_dir.name, "导出全量备份")
            shutil.copytree(
                pack_paths.PACKS_DIR, snapshot_root / "packs", dirs_exist_ok=True
            )
            for copied_pack_dir in (snapshot_root / "packs").iterdir():
                semantic_file = copied_pack_dir / "semantic_metadata.json"
                if not copied_pack_dir.is_dir() or not semantic_file.is_file():
                    continue
                (copied_pack_dir / LEGACY_METADATA_BACKUP_NAME).unlink(missing_ok=True)
                portable = reset_local_embedding_state(
                    import_metadata_file(semantic_file), copied_pack_dir
                )
                portable = reconcile_metadata(copied_pack_dir, external_data=portable)
                save_metadata(copied_pack_dir, reset_local_embedding_state(portable))
                (copied_pack_dir / LEGACY_METADATA_BACKUP_NAME).unlink(missing_ok=True)

        archive_path = shutil.make_archive(
            str(archive_base), "zip", root_dir=snapshot_root
        )

    return {"archive_path": archive_path}


def _find_backup_root(extract_root: Path) -> Path:
    direct = extract_root / "registry.json"
    if direct.is_file() or (extract_root / "packs").is_dir():
        return extract_root

    candidates = [child for child in extract_root.iterdir() if child.is_dir()]
    for child in candidates:
        if (child / "registry.json").is_file() or (child / "packs").is_dir():
            return child
    raise ValueError("备份包结构无效，缺少 runtime 根目录")


def import_runtime_backup(
    backup_zip_path: Path,
    overwrite: bool = False,
    operation_guard: PackOperationGuard | None = None,
) -> dict:
    """以支持安全回滚的单个事务校验并恢复运行时备份。"""
    if not backup_zip_path.is_file():
        raise FileNotFoundError("备份压缩包不存在")

    pack_paths.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=pack_paths.TEMP_DIR, prefix="runtime_restore_"
    ) as tmp_dir:
        extract_root = Path(tmp_dir)
        _extract_zip_safely(backup_zip_path, extract_root)
        backup_root = _find_backup_root(extract_root)
        _require_regular_tree(backup_root, "恢复全量备份")

        backup_packs_dir = backup_root / "packs"
        backup_registry = backup_root / "registry.json"
        backup_rules = backup_root / "selection_rules.json"
        backup_community = backup_root / "community_cache.json"

        if not backup_packs_dir.is_dir() and not backup_registry.is_file():
            raise ValueError("备份包中没有可恢复的数据")

        prepared_packs = extract_root / "prepared_packs"
        prepared_packs.mkdir()
        prepared_manifests: dict[str, dict] = {}
        if backup_packs_dir.is_dir():
            for source_pack_dir in sorted(backup_packs_dir.iterdir()):
                if not source_pack_dir.is_dir():
                    continue
                pack_id = validate_pack_id(source_pack_dir.name, "备份表情包")
                prepared_pack_dir = prepared_packs / pack_id
                shutil.copytree(source_pack_dir, prepared_pack_dir)
                prepared_manifests[pack_id] = validate_pack_directory(
                    prepared_pack_dir, context=f"备份表情包 {pack_id}"
                )
                semantic_file = prepared_pack_dir / "semantic_metadata.json"
                if semantic_file.is_file():
                    portable = reset_local_embedding_state(
                        import_metadata_file(semantic_file), prepared_pack_dir
                    )
                    reconciled = reconcile_metadata(
                        prepared_pack_dir, external_data=portable
                    )
                    save_metadata(
                        prepared_pack_dir,
                        reset_local_embedding_state(reconciled),
                    )
                    (prepared_pack_dir / LEGACY_METADATA_BACKUP_NAME).unlink(
                        missing_ok=True
                    )

        current_pack_ids = (
            {path.name for path in pack_paths.PACKS_DIR.iterdir() if path.is_dir()}
            if pack_paths.PACKS_DIR.is_dir()
            else set()
        )
        if overwrite and backup_packs_dir.is_dir():
            for current_pack_id in sorted(current_pack_ids):
                current_semantic_file = (
                    pack_paths.PACKS_DIR / current_pack_id / "semantic_metadata.json"
                )
                if not current_semantic_file.is_file():
                    continue
                current_metadata = load_metadata(pack_paths.PACKS_DIR / current_pack_id)
                if current_metadata.get("metadata_read_only"):
                    raise ValueError(
                        "现有图包语义文件无法安全读取，拒绝全量覆盖恢复："
                        f"{current_metadata.get('metadata_error') or '未知错误'}"
                    )
        backup_pack_ids = set(prepared_manifests)
        available_pack_ids = (
            backup_pack_ids
            if overwrite and backup_packs_dir.is_dir()
            else current_pack_ids | backup_pack_ids
        )

        if backup_registry.is_file():
            try:
                registry_data = json.loads(
                    backup_registry.read_text(encoding="utf-8-sig")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"备份中的 registry.json 无法解析: {exc}") from exc
            if not isinstance(registry_data, dict):
                raise ValueError("备份中的 registry.json 格式无效")
            backup_entries = _normalize_installed_packs(
                registry_data.get("installed_packs", [])
            )
        else:
            backup_entries = []
        current_entries = _load_registry().get("installed_packs", [])
        if overwrite:
            merged_entries = list(backup_entries)
        else:
            # 不覆盖已有表情包时，其本地注册表设置也必须优先于备份中的条目。
            current_entry_ids = {
                str(item.get("id") or "").strip() for item in current_entries
            }
            merged_entries = list(current_entries)
            merged_entries.extend(
                item
                for item in backup_entries
                if str(item.get("id") or "").strip() not in current_entry_ids
            )
        entry_by_id = {
            str(item.get("id") or "").strip(): dict(item)
            for item in merged_entries
            if str(item.get("id") or "").strip() in available_pack_ids
        }
        for pack_id in sorted(available_pack_ids):
            if pack_id in entry_by_id:
                continue
            manifest = prepared_manifests.get(pack_id) or _load_manifest(pack_id)
            entry_by_id[pack_id] = {
                "id": pack_id,
                "name": str(manifest.get("name") or pack_id),
                "version": str(manifest.get("version") or "1.0.0"),
                "enabled": True,
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
        registry_payload = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "installed_packs": list(entry_by_id.values()),
        }

        rules_payload = None
        if backup_rules.is_file():
            try:
                rules_data = json.loads(backup_rules.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"备份中的 selection_rules.json 无法解析: {exc}"
                ) from exc
            if not isinstance(rules_data, dict):
                raise ValueError("备份中的 selection_rules.json 格式无效")
            rules_payload = {
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "rules": _validate_and_normalize_rules(
                    rules_data.get("rules", []), available_pack_ids
                ),
            }
        elif overwrite and backup_packs_dir.is_dir():
            current_rules = _load_selection_rules().get("rules", [])
            rules_payload = {
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "rules": _validate_and_normalize_rules(
                    current_rules, available_pack_ids
                ),
            }

        community_payload = None
        if backup_community.is_file():
            try:
                community_payload = json.loads(
                    backup_community.read_text(encoding="utf-8-sig")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"备份中的 community_cache.json 无法解析: {exc}"
                ) from exc
            if not isinstance(community_payload, dict):
                raise ValueError("备份中的 community_cache.json 格式无效")

        if operation_guard:
            for pack_id in sorted(current_pack_ids):
                operation_guard(pack_id, "恢复全量备份")

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
        community_snapshot = (
            pack_paths.COMMUNITY_CACHE_PATH.read_bytes()
            if pack_paths.COMMUNITY_CACHE_PATH.is_file()
            else None
        )
        semantic_indexes_dir = pack_paths.PLUGIN_DATA_DIR / "semantic_indexes"
        old_packs_dir = extract_root / "old_runtime_packs"
        old_indexes_dir = extract_root / "old_runtime_indexes"
        added_pack_ids: list[str] = []
        moved_index_dirs: dict[str, Path] = {}
        packs_swapped = False
        indexes_swapped = False
        try:
            if overwrite and backup_packs_dir.is_dir():
                if pack_paths.PACKS_DIR.exists():
                    shutil.move(str(pack_paths.PACKS_DIR), str(old_packs_dir))
                shutil.move(str(prepared_packs), str(pack_paths.PACKS_DIR))
                packs_swapped = True
                if semantic_indexes_dir.exists():
                    shutil.move(str(semantic_indexes_dir), str(old_indexes_dir))
                    indexes_swapped = True
                semantic_indexes_dir.mkdir(parents=True, exist_ok=True)
                restored_packs = len(backup_pack_ids)
            else:
                pack_paths.PACKS_DIR.mkdir(parents=True, exist_ok=True)
                restored_packs = 0
                for prepared_pack_dir in sorted(prepared_packs.iterdir()):
                    target_pack_dir = pack_paths.PACKS_DIR / prepared_pack_dir.name
                    if target_pack_dir.exists():
                        continue
                    shutil.move(str(prepared_pack_dir), str(target_pack_dir))
                    added_pack_ids.append(prepared_pack_dir.name)
                    target_index_dir = semantic_indexes_dir / prepared_pack_dir.name
                    if target_index_dir.exists():
                        moved_index = (
                            extract_root / f"old_index_{prepared_pack_dir.name}"
                        )
                        shutil.move(str(target_index_dir), str(moved_index))
                        moved_index_dirs[prepared_pack_dir.name] = moved_index
                    restored_packs += 1

            _save_registry(registry_payload)
            if rules_payload is not None:
                _save_selection_rules(rules_payload)
            if community_payload is not None:
                _save_json(pack_paths.COMMUNITY_CACHE_PATH, community_payload)
        except Exception:
            if packs_swapped:
                shutil.rmtree(pack_paths.PACKS_DIR, ignore_errors=True)
                if old_packs_dir.exists():
                    shutil.move(str(old_packs_dir), str(pack_paths.PACKS_DIR))
            else:
                for pack_id in added_pack_ids:
                    shutil.rmtree(pack_paths.PACKS_DIR / pack_id, ignore_errors=True)
            if indexes_swapped:
                shutil.rmtree(semantic_indexes_dir, ignore_errors=True)
                if old_indexes_dir.exists():
                    shutil.move(str(old_indexes_dir), str(semantic_indexes_dir))
            else:
                for pack_id, moved_index in moved_index_dirs.items():
                    target_index_dir = semantic_indexes_dir / pack_id
                    if moved_index.exists() and not target_index_dir.exists():
                        target_index_dir.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(moved_index), str(target_index_dir))
            _restore_file_snapshot(pack_paths.REGISTRY_PATH, registry_snapshot)
            _restore_file_snapshot(pack_paths.SELECTION_RULES_PATH, rules_snapshot)
            _restore_file_snapshot(pack_paths.COMMUNITY_CACHE_PATH, community_snapshot)
            raise

        return {
            "restored_packs": restored_packs,
            "runtime_dir": str(pack_paths.PLUGIN_DATA_DIR),
        }
