"""Real headless Chrome DOM plus native management mutation and reload readback."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_ada_companion.management_api import ManagementApi
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from playwright.async_api import async_playwright

from maintenance.tests.working_state_support import save

QQ = "qq:FriendMessage:owner"


@pytest.mark.asyncio
async def test_goal_action_keeps_version_and_unavailable_state_is_not_empty(
    tmp_path, make_dialogue
):
    owners = OwnerMemory(tmp_path / "owners.json", [QQ])
    state = make_dialogue(tmp_path / "dialogue.json", owners)
    saved = await save(
        state,
        QQ,
        "以后继续练完整歌曲",
        action="save",
        kind="goal",
        title="练歌",
        content="继续练完整歌曲",
        evidence_quote="继续练完整歌曲",
    )
    assert saved["ok"], saved
    snapshot = await state.snapshot(QQ)
    api = ManagementApi(SimpleNamespace(dialogue=state, owner_memory=owners, target=QQ))
    script = (
        Path(__file__).resolve().parents[2]
        / "data/plugins/astrbot_plugin_ada_companion/pages/manage/conversation.js"
    ).read_text(encoding="utf-8")
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(
                '<div id="conversation-state"></div><div id="topics"></div><div id="discoveries"></div>'
            )
            await page.add_script_tag(
                content=script.replace(
                    "export function renderConversation", "function renderConversation"
                )
            )
            await page.evaluate(
                "data => renderConversation(data, (action, args) => { window.action = {action, ...args}; })",
                {
                    "topics": snapshot["items"],
                    "working_state": snapshot,
                },
            )
            assert await page.locator("#topics strong").inner_text() == "目标 · 练歌"
            await page.get_by_role("button", name="结束目标").click()
            payload = await page.evaluate("window.action")
            assert payload["expected"]["version"] == saved["version"]
            await api.apply(payload)
            restored = await state.snapshot(QQ)
            await page.evaluate(
                "data => renderConversation(data, () => {})",
                {"topics": restored["items"], "working_state": restored},
            )
            assert (
                "没有待续话题或进行中的目标"
                in await page.locator("#topics").inner_text()
            )
            await page.evaluate(
                "renderConversation({working_state: {ok:false, error:'working_memory_unavailable'}}, () => {})"
            )
            assert "暂时无法读取" in await page.locator("#topics").inner_text()
            assert "没有待续" not in await page.locator("#topics").inner_text()
            await page.evaluate(
                "data => renderConversation(data, () => {})",
                {
                    "topics": snapshot["items"],
                    "working_state": {"ok": True, "next_offset": 1, "total": 2},
                },
            )
            assert (
                "当前显示 1 项，共 2 项" in await page.locator("#topics").inner_text()
            )
            assert json.dumps(payload, ensure_ascii=False).find("version") > 0
        finally:
            await browser.close()
