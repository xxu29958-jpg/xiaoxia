import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit, urljoin, unquote

import pytest

from astrbot.dashboard.plugin_page_auth import PluginPageAuth
from astrbot.dashboard.services.plugin_page_service import PluginPageService

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "pages/app"


@pytest.mark.asyncio
async def test_shared_assets_resolve_through_astrbot_page_service(monkeypatch):
    service = PluginPageService(SimpleNamespace())
    monkeypatch.setattr(service, "get_plugin_root_dir", lambda plugin: ROOT)
    plugin = SimpleNamespace()
    assert [page.name for page in await service.discover_plugin_pages(plugin)] == [
        "app"
    ]
    shared_urls = {}
    prefix = service.build_plugin_page_view_content_path("meme_manager", "app", "test-token")
    for source in APP.rglob("*"):
        if source.suffix not in {".html", ".css", ".js"}:
            continue
        relative = source.relative_to(APP).as_posix()
        content = source.read_text("utf-8")
        if source.suffix == ".html":
            processed = service.process_plugin_page_html(content, theme=None, extra_query_params={"asset_token": "test-token"})
            urls = re.findall(r"(?:src|href)=[\"']([^\"']+)", processed)
        elif source.suffix == ".css":
            urls = re.findall(r"url\([\"']?([^\)\"']+)", content)
        else:
            urls = re.findall(r"(?:from\s*|import\s*\()[\"']([^\"']+)", content)
        for url in urls:
            if url.startswith("/api/plugin/page/bridge-sdk.js"):
                assert parse_qs(urlsplit(url).query)["asset_token"] == ["test-token"]
                continue
            if not url.startswith("."):
                continue
            resolved_url = urljoin(prefix + relative, url)
            assert resolved_url.startswith(prefix), resolved_url
            asset = unquote(urlsplit(resolved_url).path.removeprefix(prefix))
            resolved = await service.resolve_plugin_page_file(plugin, "app", asset)
            assert resolved.is_relative_to(APP)
            assert resolved.is_file(), asset
            if asset.startswith("shared/"):
                shared_urls.setdefault(asset, set()).add(resolved_url)
    assert {"shared/ui.js", "shared/ui.css"} <= set(shared_urls)
    assert all(len(urls) == 1 for urls in shared_urls.values())
    with pytest.raises(ValueError):
        await service.resolve_plugin_page_file(plugin, "app", "../_conf_schema.json")


def test_fonts_and_shared_scripts_have_no_delivery_copies():
    for name in ("ui.js", "ui.css", "fa.min.css"):
        assert list((ROOT / "pages").rglob(name)) == [APP / "shared" / name]
    fonts = list((ROOT / "pages").rglob("*.woff2"))
    assert len(fonts) == 4
    assert all(path.parent == APP / "shared/webfonts" for path in fonts)


def test_frontend_collection_catalog_and_rules():
    import subprocess
    workspace = ROOT.parents[2]
    result = subprocess.run(["node", "--test", "meme-inbox.test.mjs", "meme-catalog.test.mjs"], cwd=workspace / "maintenance/ui", capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
