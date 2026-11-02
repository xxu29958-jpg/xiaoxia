import shutil
from datetime import datetime, timezone

from ... import config as pack_paths
from ..semantic.storage import (
    get_pack_semantic_summary,
)
from .storage_common import PackOperationGuard
from .storage_files import _count_images, _load_json, _load_manifest, _save_json
from .storage_registry import (
    _load_registry,
    _load_selection_rules,
    _save_registry,
    _save_selection_rules,
)
from .storage_uninstall import uninstall_pack


def _snapshot_single_empty_pack() -> str | None:
    """快照当前是否仅存在一个空表情包。"""
    if not pack_paths.PACKS_DIR.is_dir():
        return None

    pack_dirs = sorted(path for path in pack_paths.PACKS_DIR.iterdir() if path.is_dir())
    if len(pack_dirs) != 1:
        return None

    only_pack = pack_dirs[0]
    if _load_json(only_pack / "manifest.json", {}).get("user_created"):
        return None
    if _count_images(only_pack / "memes") != 0:
        return None
    return only_pack.name


def _apply_post_install_policy(
    new_pack_id: str,
    previous_single_empty_pack_id: str | None,
    set_as_default: bool,
    operation_guard: PackOperationGuard | None = None,
) -> dict:
    """安装完成后执行策略：必要时移除空包并设置默认包。"""
    result = {
        "removed_empty_pack_id": None,
        "forced_set_default": False,
    }

    normalized_new_pack_id = str(new_pack_id or "").strip()
    if not normalized_new_pack_id:
        return result

    previous_empty_pack_id = str(previous_single_empty_pack_id or "").strip()

    should_cleanup_previous_empty = bool(
        previous_empty_pack_id
        and previous_empty_pack_id != normalized_new_pack_id
        and (pack_paths.PACKS_DIR / previous_empty_pack_id).is_dir()
    )

    if should_cleanup_previous_empty:
        uninstall_pack(previous_empty_pack_id, operation_guard=operation_guard)
        result["removed_empty_pack_id"] = previous_empty_pack_id

    should_set_default = bool(set_as_default) or bool(previous_empty_pack_id)
    if should_set_default and (pack_paths.PACKS_DIR / normalized_new_pack_id).is_dir():
        set_default_pack(normalized_new_pack_id)
        result["forced_set_default"] = not bool(set_as_default)

    return result


def create_local_pack(name: str, description: str = "", operation_guard=None) -> dict:
    """Create a named local pack without changing the current default.

    Args:
        name: User-visible name, between one and 80 characters.
        description: Optional description, at most 500 characters.
        operation_guard: Optional runtime mutation guard.

    Returns:
        Identity and name of the newly registered pack.

    Raises:
        ValueError: The supplied name or description is invalid.
        OSError: The pack or registry cannot be written.
    """
    import secrets

    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("请输入 1 到 80 个字符的表情包名称")
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError("表情包描述不能超过 500 个字符")
    pack_id = f"local-{secrets.token_hex(8)}"
    if operation_guard:
        operation_guard(pack_id, "创建表情包")
    pack = pack_paths.PACKS_DIR / pack_id
    pack.mkdir(parents=True, exist_ok=False)
    registry = _load_registry()
    try:
        (pack / "memes" / "默认分类").mkdir(parents=True)
        _save_json(
            pack / "manifest.json",
            {
                "schema_version": 1,
                "id": pack_id,
                "name": name.strip(),
                "version": "1.0.0",
                "description": description.strip(),
                "user_created": True,
                "categories": {"默认分类": {"description": "请添加描述"}},
            },
        )
        _save_json(pack / "memes_data.json", {"默认分类": "请添加描述"})
        registry["installed_packs"].append(
            {
                "id": pack_id,
                "name": name.strip(),
                "version": "1.0.0",
                "enabled": True,
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _save_registry(registry)
    except Exception:
        shutil.rmtree(pack, ignore_errors=True)
        raise
    return {"pack_id": pack_id, "name": name.strip()}


def get_pack_detail(pack_id: str) -> dict:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        raise ValueError("pack_id 不能为空")

    pack_dir = pack_paths.PACKS_DIR / pack_id
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"表情包 {pack_id} 不存在")

    manifest = _load_manifest(pack_id)
    memes_dir = pack_dir / "memes"
    categories = []
    if memes_dir.is_dir():
        for category_dir in sorted(memes_dir.iterdir(), key=lambda x: x.name):
            if category_dir.is_dir():
                categories.append(
                    {
                        "name": category_dir.name,
                        "image_count": len(
                            [
                                p
                                for p in category_dir.iterdir()
                                if p.is_file()
                                and p.suffix.lower()
                                in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
                            ]
                        ),
                    }
                )

    result = {
        "id": pack_id,
        "manifest": manifest,
        "pack_dir": str(pack_dir),
        "categories": categories,
        "total_images": _count_images(memes_dir),
        "has_semantic_metadata": (pack_dir / "semantic_metadata.json").is_file(),
    }
    result.update(
        get_pack_semantic_summary(pack_dir, result["total_images"], verify_files=False)
    )
    return result


def set_default_pack(pack_id: str) -> dict:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        raise ValueError("pack_id 不能为空")
    if not (pack_paths.PACKS_DIR / pack_id).is_dir():
        raise FileNotFoundError(f"表情包 {pack_id} 不存在")

    selection_rules = _load_selection_rules()
    rules = [
        rule
        for rule in selection_rules.get("rules", [])
        if str(rule.get("scope") or "") != "default"
    ]
    rules.append({"id": "default", "scope": "default", "pack_id": pack_id})
    selection_rules["rules"] = rules
    _save_selection_rules(selection_rules)
    return {"pack_id": pack_id}
