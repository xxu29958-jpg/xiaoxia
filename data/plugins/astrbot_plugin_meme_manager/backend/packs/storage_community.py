from datetime import datetime, timezone
from urllib.parse import quote

import requests

from ... import config as pack_paths
from .protocol import (
    validate_community_index,
)
from .storage_files import _load_json, _save_json
from .storage_network import _http_get_with_optional_acceleration


def fetch_and_cache_community_index(
    index_url: str,
    github_accelerator_url: str = "",
) -> dict:
    index_url = str(index_url or "").strip()
    if not index_url:
        raise ValueError("index_url 不能为空")

    response = _http_get_with_optional_acceleration(
        index_url,
        timeout=20,
        github_accelerator_url=github_accelerator_url,
    )
    if response.status_code != 200:
        raise ValueError(f"下载社区索引失败，状态码: {response.status_code}")

    try:
        index_data = response.json()
    except Exception as exc:
        raise ValueError(f"社区索引不是有效 JSON: {exc}") from exc

    index_data = validate_community_index(index_data)
    for entry in index_data["packs"]:
        if str(entry.get("version") or "").strip():
            continue
        source = entry["source"]
        manifest_url = (
            f"https://raw.githubusercontent.com/{quote(source['repo'], safe='/')}/"
            f"{quote(source['ref'], safe='')}/{quote(source['subpath'], safe='/')}/manifest.json"
        )
        try:
            manifest_response = _http_get_with_optional_acceleration(
                manifest_url, timeout=10, github_accelerator_url=github_accelerator_url
            )
            try:
                if manifest_response.status_code != 200:
                    raise ValueError("无法读取远端版本")
                manifest = manifest_response.json()
                if (
                    manifest.get("id") != entry["id"]
                    or not str(manifest.get("version") or "").strip()
                ):
                    raise ValueError("远端版本信息无效")
                entry["version"] = str(manifest["version"]).strip()
            finally:
                manifest_response.close()
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            entry["version_unavailable"] = True

    cache_payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_url": index_url,
        "index": index_data,
    }
    _save_json(pack_paths.COMMUNITY_CACHE_PATH, cache_payload)
    return cache_payload


def load_cached_community_index() -> dict:
    cache_data = _load_json(pack_paths.COMMUNITY_CACHE_PATH, {})
    if not isinstance(cache_data, dict) or not cache_data:
        raise FileNotFoundError("社区索引缓存不存在，请先拉取索引")
    index_data = cache_data.get("index")
    if not isinstance(index_data, dict):
        raise ValueError("社区索引缓存格式无效")
    return cache_data


def find_cached_pack_entry(pack_id: str) -> dict:
    pack_id = str(pack_id or "").strip()
    if not pack_id:
        raise ValueError("pack_id 不能为空")

    cache_data = load_cached_community_index()
    packs = cache_data.get("index", {}).get("packs", [])
    if not isinstance(packs, list):
        raise ValueError("社区索引缓存格式无效")

    for entry in packs:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or "").strip() == pack_id:
            return entry
    raise FileNotFoundError(f"缓存索引中未找到 pack_id={pack_id} 的条目")
