"""Verify unsolicited reactions pass through native response decoration."""

import hashlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_meme_manager.main import MemeSender
from astrbot_plugin_meme_manager.meme_usage import MemeUsage
from astrbot_plugin_meme_manager.mixins.event_handlers import (
    LLM_REQUEST_ORIGIN_EXTRA_KEY,
)

from astrbot.api.provider import LLMResponse
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)
from maintenance.tests.test_browser_url_messages import event as native_event
from maintenance.tests.test_proactive_memes import flow as flow


@pytest.fixture
def sender(flow):
    sender = object.__new__(MemeSender)
    sender.__dict__.update(flow.library.__dict__)
    sender.config = {}
    sender.trigger_scope = "only_chat_llm"
    sender.emotion_llm_enabled = True
    sender.emotion_llm_context_turns = 3
    sender.emotion_llm_inject_persona = False
    sender.filter_all_tags = False
    sender.remove_invalid_alternative_markup = False
    sender.convert_static_to_gif = False
    sender.enable_mixed_message = True
    sender.mixed_message_probability = 100
    sender.send_image_as_base64 = False
    sender.streaming_compatibility = True
    sender.content_cleanup_rule = ""
    sender._resolve_persona_id = lambda **kwargs: "Ada"
    sender._build_meme_quantity_prompt = lambda: "One relevant image, no narration."
    sender._resolve_runtime_pack_context = lambda **kwargs: {
        "pack_dir": flow.folder,
        "pack_id": "test",
    }
    sender._get_runtime_memes_dir_for_event = lambda event: str(flow.folder)
    return sender


def message(flow, text, *, channel="aiocqhttp", image_only=False):
    event = native_event(channel, text)
    event.message_obj.message_id = str(len(flow.decision_requests))
    values = {
        LLM_REQUEST_ORIGIN_EXTRA_KEY: "chat",
        "meme_manager_semantic_active": True,
        "meme_manager_semantic_mode": "llm",
        "meme_manager_semantic_verified_pack_id": "test",
        "meme_manager_runtime_pack_id": "test",
        "ada_meme_only": image_only,
        "turnflow_user_text": text,
        "meme_manager_scope": flow.plugin.owner_memory.shared_scope,
    }
    for key, value in values.items():
        event.set_extra(key, value)
    return event


async def decorate(sender, event, text="今天值得无声蹦跶两下。"):
    response = LLMResponse(role="assistant", completion_text=text)
    await sender._resp_impl(event, response)
    event.set_result(
        MessageEventResult(
            chain=[Plain(response.completion_text)],
            result_content_type=ResultContentType.LLM_RESULT,
        )
    )
    await sender._on_decorating_result_impl(event)
    return response, [x for x in event.get_result().chain if isinstance(x, Image)]


@pytest.mark.asyncio
@pytest.mark.parametrize("image_only", [False, True])
async def test_ordinary_reply_can_attach_real_image_without_main_model_tool_call(
    flow, sender, image_only
):
    text = "发张开心表情包" if image_only else "哈哈哈，今天居然准点下班！"
    flow.reaction_action = "requested" if image_only else "react"
    event = message(flow, text, image_only=image_only)
    event.message_str = (
        "<message_batch><item><text>" + text + "</text></item></message_batch>"
    )
    response, images = await decorate(sender, event)
    assert "meme:" not in response.completion_text
    assert response.completion_text == ("" if image_only else "今天值得无声蹦跶两下。")
    assert len(images) == 1
    assert Path(await images[0].convert_to_file_path()).read_bytes() == flow.image_bytes
    assert len(flow.visual_request["image_urls"]) == 1
    assert "Current user" in flow.visual_request["prompt"]
    selection = json.loads(flow.visual_request["prompt"])
    assert text in selection["expression"]
    assert "message_batch" not in selection["expression"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["ordinary", "same_mood", "unavailable", "invalid", "stale_evidence"]
)
async def test_no_emotional_beat_or_failed_decision_never_searches_or_changes_text(
    flow, sender, failure
):
    event = message(flow, "哈哈也是")
    if failure == "ordinary":
        flow.reaction_action = "skip"
    elif failure == "same_mood":
        flow.reaction_shift = False
    elif failure == "unavailable":
        flow.context.llm_generate.side_effect = TimeoutError("decision unavailable")
    else:
        flow.context.llm_generate.side_effect = None
        raw = (
            "broken"
            if failure == "invalid"
            else '{"action":"react","shift":true,"evidence":"previous event only"}'
        )
        from types import SimpleNamespace

        flow.context.llm_generate.return_value = SimpleNamespace(completion_text=raw)
    response, images = await decorate(sender, event, "那就这么定了。")
    assert response.completion_text == "那就这么定了。"
    assert images == []
    flow.search.assert_not_awaited()
    assert not flow.library.meme_usage.status(flow.plugin.owner_memory.shared_scope)[
        "recent"
    ]


@pytest.mark.asyncio
async def test_shared_cadence_survives_restart_and_skips_two_following_turns(
    flow, sender, monkeypatch
):
    _, images = await decorate(sender, message(flow, "终于过了！"))
    assert len(images) == 1
    scope = flow.plugin.owner_memory.shared_scope
    sender.meme_usage = MemeUsage(sender.meme_usage.path)
    # Another approved, genuinely different image cannot evade the shared cadence.
    from PIL import Image as PillowImage

    PillowImage.new("RGB", (32, 32), "green").save(flow.image_path)
    flow.metadata["images"]["a" * 64]["content_sha256"] = hashlib.sha256(
        flow.image_path.read_bytes()
    ).hexdigest()
    start = time.time()
    monkeypatch.setattr(time, "time", lambda: start + 301)
    for channel in ("weixin_oc", "aiocqhttp"):
        _, images = await decorate(
            sender, message(flow, "哈哈继续庆祝", channel=channel)
        )
        assert not images
    assert flow.search.await_count == 1
    _, images = await decorate(
        sender, message(flow, "等等，居然还中了奖！", channel="weixin_oc")
    )
    assert len(images) == 1
    assert len(sender.meme_usage.status(scope)["recent"]) == 2


@pytest.mark.asyncio
async def test_requested_image_bypasses_automatic_spacing_but_not_pause(flow, sender):
    scope = flow.plugin.owner_memory.shared_scope
    assert sender.meme_usage.claim(scope, "b" * 64)
    flow.reaction_action = "requested"
    event = message(flow, "再发张猫猫表情", image_only=True)
    response, images = await decorate(sender, event)
    assert len(images) == 1
    assert response.completion_text == ""
    await sender._resp_impl(event, response)
    assert len(flow.decision_requests) == 1
    sender.meme_usage.set_enabled(scope, False)
    _, images = await decorate(sender, message(flow, "发张表情包"))
    assert not images


@pytest.mark.asyncio
async def test_unmatched_library_request_has_a_visible_result(flow, sender):
    flow.reaction_action = "requested"
    sender.select_context_meme = AsyncMock(return_value=None)
    event = message(flow, "来一个猫猫嫌弃的表情，只发图。", image_only=True)
    event.set_extra("ada_library_sticker", True)
    response, images = await decorate(sender, event, "<p></p>")
    assert "图库里暂时没有合适的" in response.completion_text
    assert not images


@pytest.mark.asyncio
async def test_tool_markers_and_streaming_cannot_bypass_decision(flow, sender):
    event = message(flow, "早啊")
    event.set_extra("meme_manager_semantic_mode", "tool")
    event.set_extra(
        "meme_manager_semantic_candidates",
        {
            flow.candidate_id: {
                "content_sha256": flow.digest,
                "relative_path": "reaction.gif",
                "entry_id": "a" * 64,
                "id": flow.candidate_id,
            }
        },
    )
    flow.reaction_action = "skip"
    response, images = await decorate(
        sender, event, "早。\n&&" + flow.candidate_id + "&&"
    )
    assert response.completion_text == "早。"
    assert not images
    event.send = AsyncMock()
    # A late stale ID must also be rejected at actual streaming dispatch.
    event.set_extra("meme_manager_semantic_selected_ids", [flow.candidate_id])
    await sender._send_memes_streaming(event)
    event.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrieval_uses_intended_expression_not_story_topic(flow, sender):
    generate = flow.context.llm_generate.side_effect

    async def choose_expression(**kwargs):
        result = await generate(**kwargs)
        if "image_urls" not in kwargs:
            value = json.loads(result.completion_text)
            value["query"] = "惊喜 开心 庆祝"
            result.completion_text = json.dumps(value)
        return result

    flow.context.llm_generate.side_effect = choose_expression
    _, images = await decorate(
        sender, message(flow, "面试终于录用了"), "这下终于松口气了，恭喜啊！"
    )
    assert len(images) == 1
    assert flow.search.await_args.args[3] == "惊喜 开心 庆祝"
    assert "面试终于录用了" in flow.visual_request["prompt"]
