import shutil
from datetime import datetime, timezone

from ... import config as pack_paths
from ...config import DEFAULT_PACK_ID
from .storage_common import PackOperationGuard
from .storage_files import _load_manifest
from .storage_registry import (
    _create_empty_pack,
    _current_default_pack_id,
    _load_registry,
    _load_selection_rules,
    _save_registry,
    _save_selection_rules,
)


def uninstall_pack(
    pack_id: str, operation_guard: PackOperationGuard | None = None
) -> dict:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        raise ValueError("pack_id 不能为空")

    previous_default_pack_id = _current_default_pack_id()

    pack_dir = pack_paths.PACKS_DIR / pack_id
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"表情包 {pack_id} 不存在")
    if operation_guard:
        operation_guard(pack_id, "卸载资源包")

    shutil.rmtree(pack_dir)
    shutil.rmtree(
        pack_paths.PLUGIN_DATA_DIR / "semantic_indexes" / pack_id, ignore_errors=True
    )

    registry = _load_registry()
    registry["installed_packs"] = [
        item
        for item in registry["installed_packs"]
        if str(item.get("id") or "") != pack_id
    ]

    existing_pack_ids = (
        {path.name for path in pack_paths.PACKS_DIR.iterdir() if path.is_dir()}
        if pack_paths.PACKS_DIR.is_dir()
        else set()
    )

    if not existing_pack_ids:
        created_pack_id = _create_empty_pack(DEFAULT_PACK_ID)
        existing_pack_ids.add(created_pack_id)
    else:
        created_pack_id = ""

    normalized_installed = []
    seen_pack_ids = set()
    for item in registry["installed_packs"]:
        installed_pack_id = str(item.get("id") or "").strip()
        if (
            not installed_pack_id
            or installed_pack_id not in existing_pack_ids
            or installed_pack_id in seen_pack_ids
        ):
            continue
        normalized_installed.append(item)
        seen_pack_ids.add(installed_pack_id)

    for missing_pack_id in sorted(existing_pack_ids):
        if missing_pack_id in seen_pack_ids:
            continue
        manifest = _load_manifest(missing_pack_id)
        normalized_installed.append(
            {
                "id": missing_pack_id,
                "name": str(manifest.get("name") or missing_pack_id),
                "version": str(manifest.get("version") or "1.0.0"),
                "enabled": True,
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    registry["installed_packs"] = normalized_installed
    _save_registry(registry)

    selection_rules = _load_selection_rules()
    next_default_pack_id = ""
    if (
        previous_default_pack_id
        and previous_default_pack_id != pack_id
        and (pack_paths.PACKS_DIR / previous_default_pack_id).is_dir()
    ):
        next_default_pack_id = previous_default_pack_id
    elif normalized_installed:
        next_default_pack_id = str(normalized_installed[0].get("id") or "").strip()
    if not next_default_pack_id:
        next_default_pack_id = DEFAULT_PACK_ID

    normalized_rules = []
    for rule in selection_rules.get("rules", []):
        if not isinstance(rule, dict):
            continue
        scope = str(rule.get("scope") or "").strip().lower()
        rule_pack_id = str(rule.get("pack_id") or "").strip()
        if not rule_pack_id or rule_pack_id == pack_id:
            continue
        if scope == "default":
            continue
        if not (pack_paths.PACKS_DIR / rule_pack_id).is_dir():
            continue
        normalized_rules.append(rule)

    normalized_rules.append(
        {"id": "default", "scope": "default", "pack_id": next_default_pack_id}
    )
    selection_rules["rules"] = normalized_rules
    _save_selection_rules(selection_rules)

    return {
        "pack_id": pack_id,
        "switched_default_to": next_default_pack_id,
        "auto_created_empty_pack": bool(created_pack_id),
        "created_pack_id": created_pack_id or None,
    }
