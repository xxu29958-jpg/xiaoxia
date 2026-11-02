"""Simple image requests use the library; explicit online work retains its tools."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_ada_companion.main import AdaCompanion
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_ada_companion.tool_policy import WEB_READ_TOOLS, ToolPolicy

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.tool import FunctionTool, ToolSet
from maintenance.tests.test_browser_url_messages import event as native_event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,local", [("发张猫猫表情包", True), ("去网上找张猫猫表情包", False)]
)
async def test_local_image_does_not_launch_browser_and_next_web_request_stays_usable(
    tmp_path, text, local
):
    event = native_event("aiocqhttp", text)
    event.set_extra("ada_meme_only", True)
    event.set_extra("meme_manager_semantic_mode", "llm")
    owners = OwnerMemory(tmp_path / "owners.json", [event.unified_msg_origin])
    policy = ToolPolicy(owners)
    tools = ToolSet(
        [
            FunctionTool(name=n, description="test", parameters={"type": "object"})
            for n in WEB_READ_TOOLS | {"ada_core_memory", "send_message_to_user"}
        ]
    )
    req = ProviderRequest(
        func_tool=tools.get_light_tool_set(), system_prompt="Original character"
    )
    await policy.game_tool_scope(event, req)
    available = set(req.func_tool.names())
    assert bool(available & WEB_READ_TOOLS) is (not local)
    assert "ada_core_memory" in available
    assert ("send_message_to_user" in available) is (not local)
    assert req.system_prompt.startswith("Original character")
    assert set(tools.names()) == WEB_READ_TOOLS | {
        "ada_core_memory",
        "send_message_to_user",
    }
    req.func_tool.add_tool(
        FunctionTool(
            name="send_message_to_user",
            description="late host tool",
            parameters={"type": "object"},
        )
    )
    event.set_extra("provider_request", req)
    await AdaCompanion.route_library_sticker(None, event, None)
    if local:
        assert req.func_tool is None
    else:
        assert "send_message_to_user" in req.func_tool.names()
