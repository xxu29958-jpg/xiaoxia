"""Shared phone model controls and native fallback behavior."""

import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.model_controls import ModelControls
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from test_browser_url_messages import event

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_main_agent import _select_provider
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.provider.provider import Provider


class ChatProvider(Provider):
    def __init__(self, provider_id, failure=None):
        super().__init__({"id": provider_id, "model": "fixture-model"}, {})
        self.failure = failure
        self.requests = []

    def get_current_key(self):
        return "fixture"

    def set_key(self, key):
        pass

    async def get_models(self):
        return ["fixture-model"]

    async def text_chat(self, **kwargs):
        self.requests.append({**kwargs, "contexts": deepcopy(kwargs["contexts"])})
        if self.failure:
            raise self.failure
        first = kwargs["contexts"][0]
        assert (
            first["content"] if isinstance(first, dict) else first.content
        ) == "same memory"
        return LLMResponse(
            role="assistant",
            completion_text="Memory and tools continued on the backup.",
        )

    async def text_chat_stream(self, **kwargs):
        self.requests.append(kwargs)
        yield LLMResponse(
            role="assistant", completion_text="Already visible.", is_chunk=True
        )
        raise ConnectionError("stream interrupted")


@pytest.mark.asyncio
async def test_model_choice_survives_reload_and_both_channels_use_native_selection(
    tmp_path,
):
    owners = OwnerMemory(
        tmp_path / "owners.json",
        ["aiocqhttp:FriendMessage:owner", "weixin_oc:FriendMessage:owner"],
    )
    providers = {
        pid: ChatProvider(pid)
        for pid in ("chatgpt-companion", "deepseek-companion", "deepseek-backup")
    }
    context = SimpleNamespace(get_provider_by_id=providers.get)
    controls = ModelControls(tmp_path, owners, context)
    command = event("aiocqhttp", "切换模型DeepSeek")
    command.send = AsyncMock()
    assert await controls.receive(command)
    assert "QQ 和微信" in command.send.call_args.args[0].get_plain_text()
    reloaded = ModelControls(tmp_path, owners, context)
    for channel in ("aiocqhttp", "weixin_oc"):
        message = event(channel, "还记得吗")
        assert not await reloaded.receive(message)
        assert (
            await _select_provider(message, context) is providers["deepseek-companion"]
        )
    unknown = event("aiocqhttp", "切换模型 ChatGPT", "stranger")
    assert not await reloaded.receive(unknown)
    assert reloaded.selected() == "deepseek-companion"
    unavailable = event("weixin_oc", "切换模型 深度")
    unavailable.send = AsyncMock()
    await reloaded.receive(unavailable)
    assert reloaded.selected() == "deepseek-companion"
    assert "还没有配置好" in unavailable.send.call_args.args[0].get_plain_text()


@pytest.mark.asyncio
async def test_unreadable_choice_is_preserved_and_command_reports_failure(tmp_path):
    path = tmp_path / "model-choice.json"
    path.write_text("{broken")
    controls = ModelControls(
        tmp_path,
        OwnerMemory(tmp_path / "owners.json", ["aiocqhttp:FriendMessage:owner"]),
        SimpleNamespace(get_provider_by_id=lambda _: None),
    )
    message = event("aiocqhttp", "切换模型 ChatGPT")
    message.send = AsyncMock()
    assert await controls.receive(message)
    assert path.read_text() == "{broken"
    assert "没有切换" in message.send.call_args.args[0].get_plain_text()


@pytest.mark.asyncio
async def test_native_quota_failure_preserves_history_and_memory():
    primary = ChatProvider("chatgpt-companion", RuntimeError("429 quota exhausted"))
    backup = ChatProvider("deepseek-backup")
    backup.provider_config["modalities"] = ["text", "image", "tool_use"]
    runner = ToolLoopAgentRunner()
    request = ProviderRequest(
        prompt="Continue chatting",
        system_prompt="same memory",
        contexts=[{"role": "user", "content": "Earlier chat"}],
        func_tool=ToolSet(
            tools=[
                FunctionTool(
                    name="read_owner_preference",
                    description="Read the linked owner's shared preference",
                    parameters={"type": "object", "properties": {}},
                )
            ]
        ),
    )
    await runner.reset(
        provider=primary,
        request=request,
        run_context=ContextWrapper(context=None),
        tool_executor=None,
        agent_hooks=BaseAgentRunHooks(),
        streaming=False,
        fallback_providers=[backup],
    )
    async for _ in runner.step_until_done(2):
        pass
    response = runner.get_final_llm_resp()
    assert response.completion_text == "Memory and tools continued on the backup."
    messages = []
    for provider in (primary, backup):
        messages.append(
            [
                (item["role"], item["content"])
                if isinstance(item, dict)
                else (item.role, item.content)
                for item in provider.requests[0]["contexts"]
            ]
        )
    assert messages[0] == messages[1]
    assert messages[1][0] == ("system", "same memory")
    assert backup.requests[0]["func_tool"].names() == ["read_owner_preference"]
    assert runner.provider is backup


@pytest.mark.asyncio
async def test_partial_stream_is_not_replayed_on_another_provider():
    primary = ChatProvider("chatgpt-companion")
    backup = ChatProvider("deepseek-backup")
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=primary,
        request=ProviderRequest(prompt="hi", system_prompt="same memory"),
        run_context=ContextWrapper(context=None),
        tool_executor=None,
        agent_hooks=BaseAgentRunHooks(),
        streaming=True,
        fallback_providers=[backup],
    )
    responses = [r async for r in runner._iter_llm_responses_with_fallback()]
    assert (
        "".join(r.completion_text for r in responses if r.is_chunk)
        == "Already visible."
    )
    assert responses[-1].role == "err"
    assert not backup.requests


@pytest.mark.asyncio
async def test_proactive_error_response_uses_independent_backup(tmp_path):
    context = SimpleNamespace(
        get_provider_by_id=lambda _: object(),
        llm_generate=AsyncMock(
            side_effect=[
                LLMResponse(role="err", completion_text="quota exhausted"),
                LLMResponse(role="assistant", completion_text='{"send":false}'),
            ]
        ),
    )
    controls = ModelControls(tmp_path, None, context)
    result = await controls.generate(prompt="same request", system_prompt="same memory")
    assert result.completion_text == '{"send":false}'
    calls = context.llm_generate.call_args_list
    assert [call.kwargs["chat_provider_id"] for call in calls] == [
        "chatgpt-companion",
        "deepseek-backup",
    ]
    assert all(call.kwargs["system_prompt"] == "same memory" for call in calls)
