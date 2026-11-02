from ... import config as pack_paths
from ...config import (
    DEFAULT_CATEGORY_DESCRIPTIONS,
    DEFAULT_PACK_ID,
    RUNTIME_SCHEMA_VERSION,
)
from ..semantic.storage import (
    get_pack_semantic_summary,
)
from .storage_files import _count_images, _load_json, _load_manifest, _save_json


def _is_legacy_pack(pack_id: str, manifest: dict) -> bool:
    tags = {
        str(item or "").strip().lower()
        for item in manifest.get("tags", [])
        if str(item or "").strip()
    }
    return bool(tags.intersection({"legacy", "converted"}))


def _normalize_installed_packs(installed_packs) -> list[dict]:
    if not isinstance(installed_packs, list):
        return []
    normalized = []
    for item in installed_packs:
        if isinstance(item, dict):
            normalized.append(item)
    return normalized


def _load_registry() -> dict:
    registry = _load_json(
        pack_paths.REGISTRY_PATH,
        {"schema_version": RUNTIME_SCHEMA_VERSION, "installed_packs": []},
    )
    registry["schema_version"] = RUNTIME_SCHEMA_VERSION
    registry["installed_packs"] = _normalize_installed_packs(
        registry.get("installed_packs", [])
    )
    return registry


def _save_registry(registry: dict) -> None:
    registry["schema_version"] = RUNTIME_SCHEMA_VERSION
    registry["installed_packs"] = _normalize_installed_packs(
        registry.get("installed_packs", [])
    )
    _save_json(pack_paths.REGISTRY_PATH, registry)


def _load_selection_rules() -> dict:
    selection_rules = _load_json(
        pack_paths.SELECTION_RULES_PATH,
        {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "rules": [
                {"id": "default", "scope": "default", "pack_id": DEFAULT_PACK_ID}
            ],
        },
    )
    if not isinstance(selection_rules, dict):
        selection_rules = {}
    rules = selection_rules.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    selection_rules["schema_version"] = RUNTIME_SCHEMA_VERSION
    selection_rules["rules"] = [rule for rule in rules if isinstance(rule, dict)]
    return selection_rules


def _save_selection_rules(selection_rules: dict) -> None:
    selection_rules["schema_version"] = RUNTIME_SCHEMA_VERSION
    rules = selection_rules.get("rules", [])
    if not isinstance(rules, list):
        rules = []
    selection_rules["rules"] = [rule for rule in rules if isinstance(rule, dict)]
    _save_json(pack_paths.SELECTION_RULES_PATH, selection_rules)


def _current_default_pack_id() -> str:
    selection_rules = _load_selection_rules()
    for rule in reversed(selection_rules.get("rules", [])):
        if str(rule.get("scope") or "") == "default":
            pack_id = str(rule.get("pack_id") or "").strip()
            if pack_id:
                return pack_id
    return DEFAULT_PACK_ID


def _create_empty_pack(pack_id: str) -> str:
    pack_id = str(pack_id or "").strip() or DEFAULT_PACK_ID
    pack_dir = pack_paths.PACKS_DIR / pack_id
    memes_dir = pack_dir / "memes"
    empty_category = "empty"
    category_descriptions = {
        empty_category: str(
            DEFAULT_CATEGORY_DESCRIPTIONS.get(empty_category) or "请添加描述"
        )
    }

    pack_dir.mkdir(parents=True, exist_ok=True)
    (memes_dir / empty_category).mkdir(parents=True, exist_ok=True)
    _save_json(pack_dir / "memes_data.json", category_descriptions)
    _save_json(
        pack_dir / "manifest.json",
        {
            "schema_version": 1,
            "id": pack_id,
            "name": f"Runtime Empty Pack ({pack_id})",
            "version": "1.0.0",
            "description": "Auto-created empty meme pack",
            "tags": ["runtime", "auto-created"],
            "categories": {
                empty_category: {
                    "description": category_descriptions[empty_category],
                }
            },
        },
    )

    return pack_id


def list_installed_packs() -> list[dict]:
    registry = _load_registry()
    default_pack_id = _current_default_pack_id()
    packs = []
    for item in registry["installed_packs"]:
        pack_id = str(item.get("id") or "").strip()
        if not pack_id:
            continue
        pack_dir = pack_paths.PACKS_DIR / pack_id
        if not pack_dir.is_dir():
            continue
        manifest = _load_manifest(pack_id)
        memes_dir = pack_dir / "memes"
        image_count = _count_images(memes_dir)
        has_semantic_metadata = (pack_dir / "semantic_metadata.json").is_file()
        is_legacy_pack = _is_legacy_pack(pack_id, manifest)
        pack_data = {
            "id": pack_id,
            "name": str(item.get("name") or manifest.get("name") or pack_id),
            "version": str(item.get("version") or manifest.get("version") or "0.0.0"),
            "enabled": bool(item.get("enabled", True)),
            "installed_at": item.get("installed_at"),
            "is_default": pack_id == default_pack_id,
            "image_count": image_count,
            "category_count": (
                len([d for d in memes_dir.iterdir() if d.is_dir()])
                if memes_dir.is_dir()
                else 0
            ),
            "has_semantic_metadata": has_semantic_metadata,
            "is_legacy_pack": is_legacy_pack,
            "supports_vector_rebuild": bool(
                has_semantic_metadata and not is_legacy_pack
            ),
        }
        pack_data.update(
            get_pack_semantic_summary(pack_dir, image_count, verify_files=False)
        )
        packs.append(pack_data)
    return packs


def get_selection_rules() -> dict:
    selection_rules = _load_selection_rules()
    rules = selection_rules.get("rules", [])
    default_pack_id = _current_default_pack_id()
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "rules": rules,
        "default_pack_id": default_pack_id,
    }


def _validate_and_normalize_rules(
    rules: list[dict], available_pack_ids: set[str] | None = None
) -> list[dict]:
    if not isinstance(rules, list) or not rules:
        raise ValueError("rules 不能为空")

    normalized = []
    default_count = 0
    scope_target_set = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"第 {index + 1} 条规则格式无效")

        rule_id = str(rule.get("id") or "").strip()
        scope = str(rule.get("scope") or "").strip().lower()
        pack_id = str(rule.get("pack_id") or "").strip()
        target = str(rule.get("target") or "").strip()

        if not rule_id:
            raise ValueError(f"第 {index + 1} 条规则缺少 id")
        if scope not in {"persona", "session", "default"}:
            raise ValueError(f"第 {index + 1} 条规则 scope 非法")
        if not pack_id:
            raise ValueError(f"第 {index + 1} 条规则缺少 pack_id")
        pack_exists = (
            pack_id in available_pack_ids
            if available_pack_ids is not None
            else (pack_paths.PACKS_DIR / pack_id).is_dir()
        )
        if not pack_exists:
            raise ValueError(f"第 {index + 1} 条规则引用的 pack 不存在: {pack_id}")

        normalized_rule = {"id": rule_id, "scope": scope, "pack_id": pack_id}
        if scope in {"persona", "session"}:
            if not target:
                raise ValueError(f"第 {index + 1} 条规则缺少 target")
            scope_target_key = (scope, target)
            if scope_target_key in scope_target_set:
                raise ValueError(
                    f"第 {index + 1} 条规则与前序规则冲突: {scope} 目标 {target} 重复"
                )
            scope_target_set.add(scope_target_key)
            normalized_rule["target"] = target
        if scope == "default":
            default_count += 1

        normalized.append(normalized_rule)

    if default_count != 1:
        raise ValueError("必须且仅能存在一条 default 规则")
    if normalized[-1].get("scope") != "default":
        raise ValueError("default 规则必须位于最后")

    rule_ids = [rule["id"] for rule in normalized]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("规则 id 不能重复")

    return normalized


def save_selection_rules(rules: list[dict]) -> dict:
    normalized = _validate_and_normalize_rules(rules)
    payload = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "rules": normalized,
    }
    _save_selection_rules(payload)
    return payload
