"""Exercise page routes against Quart and durable SQLite storage."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quart import Quart

from .package_bootstrap import bootstrap_package

bootstrap_package()
from astrbot_plugin_memory_companion.core.config import ConfigView
from astrbot_plugin_memory_companion.core.models import MemoryRecord
from astrbot_plugin_memory_companion.core.store import MemoryStore
from astrbot_plugin_memory_companion.page_api import PluginPageApi
from astrbot_plugin_memory_companion.page_api_support import THEME_NAME_TO_KEY, UI_VIEW_ENDPOINTS


@pytest.fixture
def page(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.initialize()
    service = SimpleNamespace(store=store, config=ConfigView({}))
    api = PluginPageApi(SimpleNamespace(service=service))
    app = Quart(__name__)
    for path, handler, methods, _description in api._route_specs():
        app.add_url_rule(path, endpoint=path, view_func=handler, methods=methods)
    yield app.test_client(), service, api
    store.close()


@pytest.mark.asyncio
async def test_category_lifecycle_and_visibility_filter_persisted_records(page):
    client, service, _ = page
    for ident, kind, lifecycle, visibility, source in [
        ("profile", "user_profile", "stable_memory", "private_pair", "astrbot_plugin_memory_companion"),
        ("preference", "user_preference", "current_window", "private", "astrbot_plugin_memory_companion"),
        ("public", "user_profile", "stable_memory", "public", "external"),
        ("archive", "user_profile", "stable_memory", "private_pair", "bot_personal_bridge"),
    ]:
        await service.store.insert_memory(MemoryRecord(
            id=ident, memory_type=kind, content=ident, scope="private",
            visibility=visibility, lifecycle=lifecycle, source_plugin=source,
        ))
    response = await client.get("/memories?memory_type=profile&lifecycle=stable&visibility=private")
    payload = await response.get_json()
    assert response.status_code == 200
    assert [item["id"] for item in payload["memories"]] == ["profile"]
    external = await (await client.get("/memories?scope=external")).get_json()
    assert [item["id"] for item in external["memories"]] == ["public"]
    detail = await (await client.get("/memory?id=preference")).get_json()
    assert detail["memory"]["content"] == "preference"


@pytest.mark.asyncio
async def test_failed_queries_are_errors_without_private_exception_and_recover(page):
    client, service, _ = page
    for endpoint, method in [
        ("timeline", "recent_timeline"), ("relations", "list_relationships"),
        ("graph", "list_knowledge_edges"), ("threads", "list_cross_window_threads"),
        ("logs", "recent_injection_logs"),
    ]:
        original = getattr(service.store, method)
        setattr(service.store, method, AsyncMock(side_effect=OSError("private path and token")))
        response = await client.get("/" + endpoint)
        assert response.status_code == 500
        assert await response.get_json() == {"success": False, "error": endpoint + "_unavailable"}
        setattr(service.store, method, original)
        response = await client.get("/" + endpoint)
        assert response.status_code == 200
        assert (await response.get_json())["items"] == []


@pytest.mark.asyncio
async def test_configured_palette_and_declared_views_have_real_consumers(page):
    client, service, api = page
    schema = json.loads((Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text("utf-8"))
    assert set(schema["appearance"]["items"]["theme"]["options"]) == set(THEME_NAME_TO_KEY)
    for name, key in THEME_NAME_TO_KEY.items():
        service.config = ConfigView({"appearance": {"theme": name}})
        payload = await (await client.get("/ui-preferences")).get_json()
        assert payload["palette"] == key
    routes = {path: (handler, methods) for path, handler, methods, _ in api._route_specs()}
    for endpoints in UI_VIEW_ENDPOINTS.values():
        assert endpoints
        for endpoint in endpoints:
            assert callable(routes[endpoint][0])
    assert routes["/user-memory-summary"][1] == ["GET"]
