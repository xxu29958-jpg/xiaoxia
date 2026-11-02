import json
import re
from pathlib import Path

from quart import request

from .. import config as pack_paths


class WebAPIMixinPackView:
    """Pack view; shared runtime resources belong to the facade."""

    @staticmethod
    def _resolve_webui_pack_view_context() -> dict | None:
        managed_pack_id = str(request.args.get("managed_pack_id") or "").strip()
        if not managed_pack_id:
            return None
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", managed_pack_id):
            return None

        pack_dir = (pack_paths.PACKS_DIR / managed_pack_id).resolve()
        packs_root = pack_paths.PACKS_DIR.resolve()
        try:
            pack_dir.relative_to(packs_root)
        except ValueError:
            return None
        if not pack_dir.is_dir():
            return None

        return {
            "pack_id": managed_pack_id,
            "pack_dir": pack_dir,
            "memes_dir": pack_dir / "memes",
            "memes_data_path": pack_dir / "memes_data.json",
            "manifest_path": pack_dir / "manifest.json",
        }

    @staticmethod
    def _scan_pack_emojis(memes_dir: Path) -> dict:
        emojis = {}
        if not memes_dir.is_dir():
            return emojis
        for category_dir in memes_dir.iterdir():
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            files = []
            for file_path in category_dir.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".gif",
                    ".webp",
                }:
                    files.append(file_path.name)
            emojis[category] = files
        return emojis

    @staticmethod
    def _load_pack_descriptions(view_context: dict) -> dict:
        descriptions = {}
        memes_data_path = view_context["memes_data_path"]
        if memes_data_path.is_file():
            try:
                with memes_data_path.open(encoding="utf-8-sig") as file_obj:
                    data = json.load(file_obj)
                if isinstance(data, dict):
                    descriptions.update(
                        {
                            str(key): str(value)
                            for key, value in data.items()
                            if str(key).strip()
                        }
                    )
            except Exception:
                pass

        manifest_path = view_context["manifest_path"]
        if manifest_path.is_file():
            try:
                with manifest_path.open(encoding="utf-8-sig") as file_obj:
                    manifest = json.load(file_obj)
                categories = (
                    manifest.get("categories", {}) if isinstance(manifest, dict) else {}
                )
                if isinstance(categories, dict):
                    for category_name, category_meta in categories.items():
                        key = str(category_name or "").strip()
                        if not key or key in descriptions:
                            continue
                        if isinstance(category_meta, dict):
                            descriptions[key] = str(
                                category_meta.get("description") or "请添加描述"
                            )
                        else:
                            descriptions[key] = str(category_meta or "请添加描述")
            except Exception:
                pass

        return descriptions
