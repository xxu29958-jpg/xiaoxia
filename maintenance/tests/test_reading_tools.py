"""Native registered reading tool, owner isolation and public-source policy."""

import copy
import functools
import json
from types import SimpleNamespace

import pytest
from astrbot_plugin_ada_companion.lifecycle import Lifecycle
from astrbot_plugin_ada_companion.main import AdaCompanion
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_ada_companion.public_browser import PublicBrowser
from astrbot_plugin_ada_companion.tool_policy import ToolPolicy
from astrbot_plugin_ada_companion.web_tools import WebTools
from mcp.types import CallToolResult, TextContent

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.provider.register import llm_tools
from astrbot.core.star.context import Context
from maintenance.tests.working_state_support import incoming

QQ, WX = "qq:FriendMessage:owner", "wechat:FriendMessage:owner-wx"


@pytest.mark.asyncio
async def test_registered_tool_continues_after_restart_and_rejects_unlinked_owner(
    tmp_path,
):
    owners = OwnerMemory(tmp_path / "owners.json", [QQ, WX])
    state = SimpleNamespace(owners=owners)
    first = incoming(state, QQ, "读前半段")
    browser = PublicBrowser(tmp_path, {})
    data: dict = {
        "page_id": "initial",
        "url": "https://example.org/reading",
        "title": "Source",
        "text": "前半段。",
        "_source_text": "前半段。后半段有反例。",
    }
    browser.store.save_visit(owners.scope(first), data, "")
    await browser.close()
    plugin = object.__new__(AdaCompanion)
    plugin.lifecycle = Lifecycle()
    restored = PublicBrowser(tmp_path, {})
    plugin.web = WebTools(
        {}, owners, restored, None, SimpleNamespace(plugin=lambda _: None)
    )
    tool = copy.copy(next(t for t in llm_tools.func_list if t.name == "ada_web_notes"))
    tool.handler = functools.partial(AdaCompanion.web_notes, plugin)
    assert {"source_id", "offset", "limit"} <= set(tool.parameters["properties"])

    async def call(origin, **kwargs):
        event = incoming(state, origin, "接着读")
        context = ContextWrapper(
            context=AstrAgentContext(context=object.__new__(Context), event=event)
        )
        results = [
            r async for r in FunctionToolExecutor.execute(tool, context, **kwargs) if r
        ]
        result = results[0]
        assert isinstance(result, CallToolResult)
        content = result.content[0]
        assert isinstance(content, TextContent)
        return json.loads(content.text)

    try:
        result = await call(
            WX, action="read", source_id=data["reading"]["source_id"], limit=20
        )
        assert result["text"] == "后半段有反例。"
        assert result["reading"]["provided_range"]["start"] == 4
        saved = await call(
            WX, action="save", page_id=result["page_id"], source_quote="有反例"
        )
        assert saved["reading"]["source_id"] == data["reading"]["source_id"]
        denied = await call(
            "wechat:FriendMessage:stranger",
            action="read",
            source_id=data["reading"]["source_id"],
        )
        assert not denied["ok"]
        denied = await call(QQ, action="read", source_id="missing")
        assert not denied["ok"] and "unavailable" in denied["error"]
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["read", "list", None])
async def test_saved_source_isolates_private_tools_before_model_receives_it(
    tmp_path, action
):
    owners = OwnerMemory(tmp_path / "owners.json", [QQ, WX])
    event = incoming(SimpleNamespace(owners=owners), QQ, "接着读之前那篇")
    tools = ToolSet(
        [
            FunctionTool(name=name, description="test", parameters={"type": "object"})
            for name in ("ada_web_notes", "ada_core_memory", "execute_shell")
        ]
    )
    policy = ToolPolicy(owners)
    await policy.game_tool_scope(event, SimpleNamespace(func_tool=tools))
    await policy.isolate_web_tools(
        event, tools.get_tool("ada_web_notes"), {"action": action} if action else None
    )
    assert bool(event.get_extra("ada_web_read_active")) is (action == "read")
    assert set(tools.names()) == (
        {"ada_web_notes"}
        if action == "read"
        else {"ada_web_notes", "ada_core_memory", "execute_shell"}
    )
