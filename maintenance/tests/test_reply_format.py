"""Carry a reply format through the native runner and Codex wire request."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_codex_oauth_plug.oauth_plug_openai_codex.provider import (
    ProviderOAuthPlugOpenAICodex,
)

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "spoken_reply",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
            "additionalProperties": True,
        },
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("include_model", [False, True])
@pytest.mark.parametrize("response_format", [None, FORMAT])
async def test_native_format_reaches_streaming_and_nonstreaming_requests(
    streaming, include_model, response_format
):
    response = LLMResponse(role="assistant", completion_text='{"reply":"在呢。"}')
    send = AsyncMock(return_value=response)

    async def stream(**kwargs):
        yield await send(**kwargs)

    provider = SimpleNamespace(
        provider_config={"id": "chat"}, text_chat=send, text_chat_stream=stream
    )
    request = ProviderRequest(
        prompt="在吗？", model="chat-model", response_format=response_format
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=request,
        run_context=ContextWrapper(context=None),
        tool_executor=None,
        agent_hooks=BaseAgentRunHooks(),
        streaming=streaming,
    )
    results = [
        result
        async for result in runner._iter_llm_responses(include_model=include_model)
    ]
    assert results == [response]
    sent = send.call_args.kwargs
    assert ("model" in sent) == include_model
    if response_format is None:
        assert "response_format" not in sent
    else:
        assert sent["response_format"] == FORMAT


@pytest.mark.asyncio
async def test_codex_format_keeps_text_options_and_function_tools():
    provider = object.__new__(ProviderOAuthPlugOpenAICodex)
    provider.model_name = "gpt-6-luna"
    provider.provider_config = {"custom_extra_body": {"text": {"verbosity": "low"}}}
    provider.client = SimpleNamespace(base_url=SimpleNamespace(host="chatgpt.com"))
    tools = ToolSet(
        tools=[
            FunctionTool(
                name="lookup_topic",
                description="Read a topic",
                parameters={"type": "object", "properties": {}},
            )
        ]
    )
    payload, _ = await provider._prepare_chat_payload(
        prompt="继续刚才的话题。", response_format=FORMAT
    )
    params = provider._build_responses_params(
        payload, tools, oauth_web_search="disabled"
    )
    assert params["text"] == {
        "verbosity": "low",
        "format": {"type": "json_schema", **FORMAT["json_schema"]},
    }
    assert params["tools"][0]["name"] == "lookup_topic"
    assert "response_format" not in params
    assert provider.provider_config["custom_extra_body"]["text"] == {"verbosity": "low"}
    plain, _ = await provider._prepare_chat_payload(prompt="普通请求")
    plain_params = provider._build_responses_params(
        plain, None, oauth_web_search="disabled"
    )
    assert plain_params["text"] == {"verbosity": "low"}
