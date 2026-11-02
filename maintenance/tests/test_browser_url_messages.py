"""Native browser/MCP journey; only remote page transport is replaced."""

import asyncio
import base64
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.image_discovery import ImageDiscovery
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_ada_companion.public_browser import PublicBrowser
from astrbot_plugin_ada_companion.web_notebook import BackpackError, WebNotebook
from astrbot_plugin_ada_companion.web_tools import WebTools

from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata


def event(channel, text, sender="owner"):
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "bot"
    message.sender = MessageMember(sender, "Owner")
    message.message = [Plain(text)]
    message.message_str = text
    message.message_id = "browser-regression"
    return AstrMessageEvent(
        text, message, PlatformMetadata(channel, "test", channel), sender
    )


@pytest.fixture
def web(tmp_path):
    owners = OwnerMemory(
        tmp_path / "owners.json",
        [f"{c}:FriendMessage:owner" for c in ("aiocqhttp", "weixin_oc")],
    )
    return WebTools(
        {},
        owners,
        PublicBrowser(tmp_path / "browser", {"browser_headless": True}),
        None,
        SimpleNamespace(plugin=lambda name: None),
    )


@pytest.mark.asyncio
async def test_native_interaction_capture_bookmark_restart_and_cross_channel_scope(web):
    browser = web.public_browser
    current = event("aiocqhttp", "走，去你刚推荐的小网站。")
    scope = web.owners.scope(current)
    state = await browser._session(scope, None)
    html = """<!doctype html><title>Interactive fixture</title><h1>Public playground</h1>
<button onclick="document.querySelector('output').textContent=++window.score">Add point</button>
<output>0</output><input aria-label="Game nickname"><script>window.score=0;
window.addEventListener('keydown',e=>{if(e.key==='ArrowRight')document.querySelector('output').textContent=++window.score});</script>
<a href="/next">Next page</a><div style="height:1800px">Scroll area</div>
<style>@font-face{font-family:Slow;src:url('/never.woff2')}h1{font-family:Slow,serif}</style>
<a href="https://raw.example/dog-happy.jpg" download>Original happy dog</a>
<img data-original="https://images.example/lazy-cat.webp" alt="Lazy cat">
<a href="https://github.com/example/pack/blob/main/dog.jpg">Image detail page</a>"""
    # Retain real MCP, Chromium, JavaScript, screenshots, controls and persistence.
    # Replace only the remote website responses, not the browser implementation.
    code = (
        'async (page) => { await page.context().route("https://fixture.example/**", route => route.request().url().endsWith(".woff2") ? new Promise(() => {}) : route.fulfill({status:200,contentType:"text/html",body:'
        + json.dumps(html)
        + "})); }"
    )
    try:
        await browser._call(state, "browser_run_code_unsafe", {"code": code})
        opened = await web.browse(current, "open", "https://fixture.example/")
        assert isinstance(opened, CallToolResult)
        data = json.loads(opened.content[0].text)
        assert data["ok"] and data["http_status"] == 200
        assert "Public playground" in data["text"]
        assets = {item["url"] for item in data["images"]}
        assert "https://raw.example/dog-happy.jpg" in assets
        assert "https://images.example/lazy-cat.webp" in assets
        assert "https://github.com/example/pack/blob/main/dog.jpg" not in assets
        with Image.open(io.BytesIO(base64.b64decode(opened.content[1].data))) as shot:
            assert shot.size == (1200, 860) and shot.format == "JPEG"
        target = re.search(
            r'button "Add point".*?\[ref=([^]]+)', data["elements"]
        ).group(1)
        clicked = await browser.act(scope, "click", link_id=target)
        assert "Add point 1 Next page" in clicked["text"]
        moved = await browser.act(scope, "key", options={"key": "ArrowRight"})
        assert "Add point 2 Next page" in moved["text"]
        target = re.search(
            r'textbox "Game nickname".*?\[ref=([^]]+)', moved["elements"]
        ).group(1)
        typed = await browser.act(
            scope, "type", link_id=target, options={"text": "Ada"}
        )
        assert "Ada" in typed["elements"]
        scrolled = await browser.act(scope, "scroll")
        assert scrolled["scroll_y"] > 0
        await browser.act(scope, "open", "https://fixture.example/next")
        back = await browser.act(scope, "back")
        assert back["url"] == "https://fixture.example/"
        saved = browser.store.bookmarks(scope, "save", back["page_id"], "A real game")
        await browser.close()
        reloaded = WebNotebook(browser.store.folder.parent)
        wx_scope = web.owners.scope(event("weixin_oc", "我的收藏"))
        assert wx_scope == scope
        assert (
            reloaded.bookmarks(wx_scope, "list")["bookmarks"][0]["id"]
            == saved["bookmark"]["id"]
        )
        assert not reloaded.bookmarks("stranger", "list")["bookmarks"]
    finally:
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "sender"),
    [
        ("http://127.0.0.1:6185/", "owner"),
        ("https://169.254.169.254/", "owner"),
        ("file:///C:/secret", "owner"),
        ("https://example.com/", "stranger"),
    ],
)
async def test_private_targets_and_other_senders_rejected_before_mcp(web, url, sender):
    result = json.loads(
        await web.browse(event("aiocqhttp", "打开网站", sender), "open", url)
    )
    assert not result["ok"]
    assert not web.public_browser.sessions


@pytest.mark.asyncio
async def test_discovery_prefers_actual_image_detail_over_matching_commit_history(web):
    browser = web.public_browser
    current = event("weixin_oc", "找开心的狗狗表情")
    scope = web.owners.scope(current)
    state = await browser._session(scope, None)
    source = "https://github.com/test/pack/tree/main/dogs"
    detail = "https://github.com/test/pack/blob/main/happy-dog.jpg"
    history = "https://github.com/test/pack/commits/main/happy-dog.jpg"
    listing = f'<main><h1>Dogs</h1><a href="{history}">开心狗狗图片提交记录</a><a href="{detail}">happy-dog.jpg</a></main>'
    picture = '<main><h1>Happy dog</h1><img data-original="https://images.example/happy-dog.jpg" alt="开心狗狗"></main>'
    code = (
        'async (page) => { await page.context().route("https://github.com/test/**", route => route.fulfill({status:200,contentType:"text/html",body:route.request().url().includes("/blob/") ? '
        + json.dumps(picture)
        + " : "
        + json.dumps(listing)
        + "})); }"
    )
    try:
        await browser._call(state, "browser_run_code_unsafe", {"code": code})
        result = await ImageDiscovery(web).find(current, source, "狗 开心", 2)
        data = json.loads(result.content[0].text)
        assert data["ok"] and data["pages_visited"] == 2
        assert data["candidates"][0]["source_url"] == detail
        assert data["candidates"][0]["url"] == "https://images.example/happy-dog.jpg"
        assert browser.status(scope)["current_url"] == detail
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_browser_redirect_to_private_address_never_reaches_listener(web):
    received = []

    async def sentinel(reader, writer):
        received.append(True)
        writer.close()

    server = await asyncio.start_server(sentinel, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    browser = web.public_browser
    state = await browser._session("redirect-probe", None)
    code = (
        'async (page) => { await page.context().route("https://redirect.example/**", route => route.fulfill({status:302,headers:{location:"https://127.0.0.1:'
        + str(port)
        + '/private"}})); }'
    )
    try:
        await browser._call(state, "browser_run_code_unsafe", {"code": code})
        with pytest.raises(BackpackError):
            await browser.act("redirect-probe", "open", "https://redirect.example/")
        assert received == []
        assert state["snapshot"] == {}
    finally:
        await browser.close()
        server.close()
        await server.wait_closed()
