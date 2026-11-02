"""The spoken reply retains memory and becomes the actual native history."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.chat_replyer import ChatReplyer
from astrbot_plugin_ada_companion.conversation_style import _visible_text
from astrbot_plugin_ada_companion.dialogue import DialogueState
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

from astrbot.core.agent.message import Message, TextPart
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from maintenance.tests.test_browser_url_messages import event


@pytest.mark.asyncio
async def test_native_turn_keeps_memory_tools_and_saves_only_spoken_text(tmp_path):
    incoming = event("webchat", "我现在喝什么？", "astrbot")
    request = ProviderRequest(
        system_prompt="Original persona and tool instructions",
        contexts=[
            {"role": "user", "content": "把乌龙改成热可可"},
            {
                "role": "assistant",
                "content": "tool planning",
                "tool_calls": [{"id": "tool"}],
            },
            {"role": "tool", "content": "internal tool schema"},
            {"role": "assistant", "content": "记住了"},
        ],
        extra_user_content_parts=[
            TextPart(text="<MemoryCompanion-Context>热可可</MemoryCompanion-Context>")
        ],
    )
    incoming.set_extra("provider_request", request)
    incoming.set_extra("selected_provider", "deepseek-companion")
    context = SimpleNamespace(
        persona_manager=SimpleNamespace(
            resolve_selected_persona=AsyncMock(
                return_value=("Ada", {"prompt": "Ada card"}, None, False)
            )
        ),
        llm_generate=AsyncMock(
            return_value=LLMResponse(
                role="assistant",
                completion_text='{"reply":"热可可嘛，你刚改的。\\n&&meme:123456abcdef&&","topic_updates":[],"reaction":null}',
            )
        ),
    )
    replyer = ChatReplyer(
        context,
        {"chat_replyer_enabled": True},
        OwnerMemory(tmp_path / "owners.json", []),
        None,
        DialogueState(tmp_path / "dialogue.json"),
    )
    await replyer.prepare(incoming, request)
    await replyer.prepare(incoming, request)
    response = context.llm_generate.return_value
    await replyer.compose(incoming, response)
    assert response.completion_text == "热可可嘛，你刚改的。\n&&meme:123456abcdef&&"
    context.llm_generate.assert_not_awaited()
    assert request.system_prompt.startswith("Original persona and tool instructions")
    assert request.system_prompt.count("[本轮消息格式]") == 1
    assert "热可可" in request.extra_user_content_parts[0].text
    assert request.contexts[1]["tool_calls"] == [{"id": "tool"}]
    assert incoming.get_extra("ada_reply_reaction") == {"action": "skip"}
    run = SimpleNamespace(
        messages=[Message(role="assistant", content=[TextPart(text="old draft")])]
    )
    replyer.sync_history(incoming, run)
    assert run.messages[-1].content[-1].text == response.completion_text


@pytest.mark.asyncio
async def test_error_and_image_only_preserve_the_completed_response(tmp_path):
    incoming = event("webchat", "你好", "astrbot")
    incoming.set_extra("provider_request", ProviderRequest())
    incoming.set_extra("selected_provider", "deepseek-companion")
    context = SimpleNamespace(
        persona_manager=SimpleNamespace(
            resolve_selected_persona=AsyncMock(
                return_value=("Ada", {"prompt": "Ada card"}, None, False)
            )
        ),
        llm_generate=AsyncMock(side_effect=ConnectionError()),
    )
    replyer = ChatReplyer(
        context,
        {"chat_replyer_enabled": True},
        OwnerMemory(tmp_path / "owners.json", []),
        None,
        DialogueState(tmp_path / "dialogue.json"),
    )
    response = LLMResponse(role="assistant", completion_text="在呢")
    await replyer.prepare(incoming, incoming.get_extra("provider_request"))
    await replyer.compose(incoming, response)
    assert response.completion_text == "在呢" and not replyer.last["ok"]
    incoming.set_extra("ada_meme_only", True)
    incoming.set_extra("ada_native_reply", False)
    request = ProviderRequest(system_prompt="Native image reply")
    await replyer.prepare(incoming, request)
    await replyer.compose(incoming, response)
    context.llm_generate.assert_not_awaited()
    assert request.system_prompt == "Native image reply"


@pytest.mark.asyncio
async def test_natural_topic_is_shared_then_resolved_after_restart(tmp_path):
    qq = event("aiocqhttp", "明天下午去试音，回来告诉你结果。")
    wechat = event("weixin_oc", "试音通过了，已经选上了。")
    owners = OwnerMemory(
        tmp_path / "owners.json", [qq.unified_msg_origin, wechat.unified_msg_origin]
    )
    path = tmp_path / "dialogue.json"
    dialogue = DialogueState(path, linked_sessions=owners.owners)
    context = SimpleNamespace(
        persona_manager=SimpleNamespace(
            resolve_selected_persona=AsyncMock(
                return_value=("Ada", {"prompt": "Ada card"}, None, False)
            )
        ),
        llm_generate=AsyncMock(
            return_value=LLMResponse(
                role="assistant",
                completion_text=json.dumps(
                    {
                        "reply": "那我等你回来讲。",
                        "topic_updates": [
                            {
                                "action": "save",
                                "title": "试音",
                                "content": "明天下午试音，回来聊结果。",
                                "evidence_quote": "明天下午去试音",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        ),
    )
    for current in (qq, wechat):
        current.set_extra("provider_request", ProviderRequest())
        current.set_extra("selected_provider", "chatgpt-companion")
    replyer = ChatReplyer(
        context, {"chat_replyer_enabled": True}, owners, None, dialogue
    )
    await replyer.prepare(qq, qq.get_extra("provider_request"))
    response = context.llm_generate.return_value
    await replyer.compose(qq, response)
    assert response.completion_text == "那我等你回来讲。"
    restored = DialogueState(path, linked_sessions=owners.owners)
    pending = restored.visible(wechat.unified_msg_origin)
    assert len(pending) == 1 and pending[0]["title"] == "试音"
    context.llm_generate.return_value = LLMResponse(
        role="assistant",
        completion_text=json.dumps(
            {
                "reply": "行啊，拿下了！",
                "topic_updates": [
                    {
                        "action": "resolve",
                        "item_id": pending[0]["id"],
                        "evidence_quote": "已经选上了",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    replyer.dialogue = restored
    await replyer.prepare(wechat, wechat.get_extra("provider_request"))
    response = context.llm_generate.return_value
    await replyer.compose(wechat, response)
    assert response.completion_text == "行啊，拿下了！"
    assert not DialogueState(path, linked_sessions=owners.owners).visible(
        qq.unified_msg_origin
    )
    assert "topic_updates" not in response.completion_text
    context.llm_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_reply_reaction_needs_no_additional_model_and_respects_cadence():
    from astrbot_plugin_meme_manager.reaction_decision import decide_reaction

    context = SimpleNamespace(llm_generate=AsyncMock())
    choice = {
        "action": "react",
        "shift": True,
        "evidence": "终于拿到录取",
        "query": "庆祝 开心",
    }
    kwargs = {
        "text": "可以啊！",
        "current_user": "终于拿到录取通知了！",
        "choice": choice,
    }
    assert await decide_reaction(context, "unused", **kwargs)
    assert (
        await decide_reaction(context, "unused", automatic_allowed=False, **kwargs)
        is None
    )
    assert (
        await decide_reaction(context, "unused", text="早", choice={"action": "skip"})
        is None
    )
    context.llm_generate.assert_not_awaited()


def test_transcript_contains_human_words_instead_of_transport_metadata():
    assert (
        _visible_text(
            {
                "role": "user",
                "content": "<message_batch><item><text>我回来啦 &amp; 还带了奶茶</text></item></message_batch> <system_reminder>Current datetime: today</system_reminder>",
            }
        )
        == "我回来啦 & 还带了奶茶"
    )
    assert (
        _visible_text(
            {
                "role": "user",
                "content": "[Image from tool 'browser', path='private/path']",
            }
        )
        == ""
    )
