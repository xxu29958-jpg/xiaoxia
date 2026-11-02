from __future__ import annotations

import json
from pathlib import Path

from .models import (
    IMAGE_EXTENSIONS,
    semantic_entry_id,
)
from .storage_common import file_sha256


def load_category_descriptions(pack_dir: Path | str) -> dict[str, str]:
    """读取图包分类描述，优先使用可编辑的 memes_data.json。"""
    root = Path(pack_dir).resolve()
    descriptions: dict[str, str] = {}
    data_path = root / "memes_data.json"
    if data_path.is_file():
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                descriptions.update(
                    {
                        str(key).strip(): str(value or "").strip()
                        for key, value in payload.items()
                        if str(key or "").strip()
                    }
                )
        except (OSError, json.JSONDecodeError):
            pass
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            categories = (
                manifest.get("categories", {}) if isinstance(manifest, dict) else {}
            )
            if isinstance(categories, dict):
                for name, meta in categories.items():
                    category = str(name or "").strip()
                    if not category or category in descriptions:
                        continue
                    description = (
                        meta.get("description") if isinstance(meta, dict) else meta
                    )
                    descriptions[category] = str(description or "请添加描述").strip()
        except (OSError, json.JSONDecodeError):
            pass
    return descriptions


def scan_images(pack_dir: Path | str) -> list[dict[str, str]]:
    """逐文件扫描图片；同内容的不同路径也必须能独立人工修改。"""
    root = Path(pack_dir).resolve()
    memes_root = root / "memes"
    if not memes_root.is_dir():
        return []
    found: list[dict[str, str]] = []
    for path in sorted(memes_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(memes_root.resolve())
        except ValueError:
            continue
        digest = file_sha256(path)
        relative = path.relative_to(root).as_posix()
        category = path.parent.name
        entry_id = semantic_entry_id(digest, category, relative)
        found.append(
            {
                "entry_id": entry_id,
                "content_sha256": digest,
                "relative_path": relative,
                "category": category,
            }
        )
    return found
