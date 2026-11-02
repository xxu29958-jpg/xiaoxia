"""Exercise scheduled visual selection through actual phone adapter encoding."""

import base64
import hashlib
import json
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image as PillowImage

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_meme_manager.meme_usage import MemeUsage
from astrbot_plugin_meme_manager.visual_selection import MemeVisualSelection

from maintenance.tests import test_contact_channels as channels

QQ, WX = channels.QQ, channels.WX


@pytest.fixture
def flow():
    case = channels.ContactChannelTests()
    case.setUp()
    path = case.folder / "reaction.gif"
    PillowImage.new("RGB", (32, 32), "red").save(
        path,
        save_all=True,
        append_images=[PillowImage.new("RGB", (32, 32), "blue")],
        duration=100,
        loop=0,
    )
    case.image_bytes = path.read_bytes()
    case.image_path = path
    case.digest = hashlib.sha256(case.image_bytes).hexdigest()
    case.candidate_id = "meme:" + "a" * 12
    case.metadata = {
        "images": {
            "a" * 64: {
                "relative_path": "reaction.gif",
                "category": "happy",
                "content_sha256": case.digest,
                "caption": "A fallible caption",
                "tags": [],
            }
        }
    }
    library = MemeVisualSelection()
    library.semantic_enabled = True
    library.semantic_vision_provider_id = "vision"
    library.semantic_top_k = 5
    library.semantic_min_score = 0.84
    library.context = case.context
    library.meme_usage = MemeUsage(case.folder / "meme_usage.db")
    library._resolve_embedding_provider = lambda pack: object()
    case.plugin.services.plugin = lambda name: library
    case.library = library
    case.search = AsyncMock(return_value={"candidates": [{"id": case.candidate_id}]})
    case.reaction_action = "react"
    case.reaction_shift = True
    case.decision_requests = []

    async def generate(**kwargs):
        if kwargs["chat_provider_id"] == "vision":
            if "image_urls" not in kwargs:
                case.decision_requests.append(kwargs)
                payload = json.loads(kwargs["prompt"])
                evidence = (
                    payload["current_user"]
                    if case.reaction_action == "requested"
                    else payload["planned_reply"]
                )
                return SimpleNamespace(
                    completion_text=json.dumps(
                        {
                            "action": case.reaction_action,
                            "shift": case.reaction_shift,
                            "evidence": evidence,
                        }
                    ),
                )
            case.visual_request = kwargs
            return SimpleNamespace(
                role="assistant", completion_text=json.dumps({"id": case.candidate_id})
            )
        return SimpleNamespace(
            role="assistant",
            completion_text=json.dumps(
                {
                    "send": True,
                    "text": "那个取消周一的提案，我又投了一票。",
                    "meme_query": "轻松搞怪",
                }
            ),
        )

    case.context.llm_generate.side_effect = generate
    patches = [
        patch(
            "astrbot_plugin_meme_manager.visual_selection.resolve_pack_context",
            return_value={"pack_id": "test", "pack_dir": case.folder},
        ),
        patch("astrbot_plugin_meme_manager.visual_selection.search_memes", case.search),
        *[
            patch(
                f"astrbot_plugin_meme_manager.{module}.load_metadata",
                return_value=case.metadata,
            )
            for module in (
                "visual_selection",
                "backend.semantic.query",
                "backend.semantic.preview",
            )
        ],
    ]
    for patcher in patches:
        patcher.start()
    try:
        yield case
    finally:
        for patcher in reversed(patches):
            patcher.stop()
        case.doCleanups()


def attach_adapter(case, channel, *, fail_text=False, fail_image=False):
    """Keep routing and media conversion real; replace remote I/O only.

    Args:
        case: An isolated owner/service fixture.
        channel: Native QQ or WeChat destination.
        fail_text: Simulate a failed WeChat text acknowledgement.
        fail_image: Simulate an uncertain image acknowledgement.

    Returns:
        Captured native outgoing payloads and uploaded bytes.
    """
    from astrbot.core.platform.platform import PlatformStatus
    from astrbot.core.star.context import Context

    captured = {"messages": [], "uploads": []}
    origin = QQ if channel == "qq" else WX
    if channel == "qq":
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
            AiocqhttpAdapter,
        )

        adapter = object.__new__(AiocqhttpAdapter)

        async def send_private_msg(**kwargs):
            captured["messages"].append(kwargs)
            if fail_image:
                raise TimeoutError("ack lost")

        adapter.bot = SimpleNamespace(send_private_msg=send_private_msg)
        kind = "aiocqhttp"
    else:
        from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import (
            WeixinOCAdapter,
        )

        adapter = object.__new__(WeixinOCAdapter)
        adapter.token = "isolated-test-token"
        adapter.account_id = "wechat-bot"
        adapter.api_timeout_ms = 5000
        adapter._context_tokens = {"owner-wx": "test-context"}
        adapter._cache_recent_message = Mock()

        async def request_json(method, endpoint, **kwargs):
            if endpoint.endswith("getuploadurl"):
                return {"upload_param": "test-upload"}
            payload = kwargs["payload"]["msg"]
            captured["messages"].append(payload)
            if fail_text and payload["item_list"][0]["type"] == 1:
                return {"ret": -1, "errmsg": "rejected"}
            if fail_image and payload["item_list"][0]["type"] == 2:
                raise TimeoutError("ack lost")
            return {"ret": 0}

        async def upload(*args):
            captured["uploads"].append(args[-1].read_bytes())
            return "test-cdn-query"

        adapter.client = SimpleNamespace(
            request_json=request_json,
            upload_to_cdn=upload,
            aes_padded_size=lambda n: (n // 16 + 1) * 16,
        )
        kind = "weixin_oc"
    adapter._status = PlatformStatus.RUNNING
    adapter.meta = lambda: SimpleNamespace(id=origin.split(":", 1)[0], name=kind)
    case.platforms[origin.split(":", 1)[0]] = adapter
    case.context.platform_manager = SimpleNamespace(platform_insts=[adapter])
    case.context.get_config = lambda **kwargs: {}
    case.context.send_message = MethodType(Context.send_message, case.context)
    case.plugin.policy.set_channels([channel])
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["qq", "wechat"])
async def test_actual_image_bytes_reach_native_adapter_and_shared_record(flow, channel):
    captured = attach_adapter(flow, channel)
    with patch("astrbot.core.platform.platform.Metric.upload", new_callable=AsyncMock):
        result = await flow.plugin.initiative.proactive(manual=True)
    assert result["reason"] == "sent"
    if channel == "qq":
        message = captured["messages"][0]
        assert message["user_id"] == 12345678
        assert len(message["message"]) == 2
        assert (
            base64.b64decode(message["message"][1]["data"]["file"].split("//", 1)[1])
            == flow.image_bytes
        )
    else:
        assert captured["uploads"] == [flow.image_bytes]
        assert [m["item_list"][0]["type"] for m in captured["messages"]] == [1, 2]
        assert all(m["to_user_id"] == "owner-wx" for m in captured["messages"])
    assert result["has_image"] is True
    assert flow.visual_request["image_urls"][0].startswith("data:image/jpeg;base64,")
    assert "meme:" not in json.dumps(captured["messages"])
    assert (
        flow.bridge.record_visible_turn.await_args.kwargs["metadata"]["has_image"]
        is True
    )
    restored = flow.load_plugin()
    assert restored.policy.data["deliveries"][-1]["meme_sha256"] == flow.digest


@pytest.mark.asyncio
async def test_preview_and_unavailable_library_keep_delivery_state_untouched(flow):
    before = flow.plugin.policy.path.read_bytes()
    result = await flow.plugin.initiative.proactive(preview=True)
    assert result["has_image"] is True
    assert flow.plugin.policy.path.read_bytes() == before
    flow.context.send_message.assert_not_awaited()
    flow.search.side_effect = TimeoutError("embedding unavailable")
    result = await flow.plugin.initiative.proactive(manual=True)
    assert result["reason"] == "sent"
    assert result["has_image"] is False
    assert len(flow.context.send_message.await_args.args[1].chain) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["unseen_id", "file_replaced", "duplicate", "disabled"]
)
async def test_invalid_or_repeated_image_is_never_attached(flow, failure):
    generate = flow.context.llm_generate.side_effect

    async def altered(**kwargs):
        result = await generate(**kwargs)
        if kwargs["chat_provider_id"] == "vision" and "image_urls" in kwargs:
            if failure == "unseen_id":
                result.completion_text = '{"id":"meme:bbbbbbbbbbbb"}'
            if failure == "file_replaced":
                flow.image_path.write_bytes(b"unseen replacement")
                flow.metadata["images"]["a" * 64]["content_sha256"] = hashlib.sha256(
                    b"unseen replacement"
                ).hexdigest()
        return result

    flow.context.llm_generate.side_effect = altered
    if failure == "duplicate":
        flow.plugin.policy.data["deliveries"] = [
            {"at": channels.NOW - 3600, "text": "Previous", "meme_sha256": flow.digest}
        ]
    if failure == "disabled":
        flow.library.semantic_enabled = False
    before = flow.plugin.policy.path.read_bytes()
    result = await flow.plugin.initiative.proactive(preview=True)
    assert result["has_image"] is False
    assert result["send"] is True
    assert flow.plugin.policy.path.read_bytes() == before
    flow.context.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_during_visual_selection_discards_both_text_and_image(flow):
    generate = flow.context.llm_generate.side_effect

    async def interrupted(**kwargs):
        if kwargs["chat_provider_id"] == "vision" and "image_urls" in kwargs:
            flow.plugin.policy.data["last_user"] += 1
        return await generate(**kwargs)

    flow.context.llm_generate.side_effect = interrupted
    result = await flow.plugin.initiative.proactive(manual=True)
    assert result["reason"] == "context_changed"
    flow.context.send_message.assert_not_awaited()
    assert flow.plugin.policy.data["deliveries"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["text_rejected", "image_ack_lost"])
async def test_partial_wechat_send_is_uncertain_without_duplicate_retry(flow, failure):
    captured = attach_adapter(
        flow,
        "wechat",
        fail_text=failure == "text_rejected",
        fail_image=failure == "image_ack_lost",
    )
    result = await flow.plugin.initiative.proactive(manual=True)
    assert result["reason"] == "delivery_uncertain"
    expected = [1] if failure == "text_rejected" else [1, 2]
    assert [m["item_list"][0]["type"] for m in captured["messages"]] == expected
    flow.plugin = flow.load_plugin()
    again = await flow.plugin.initiative.proactive(manual=True)
    assert again["reason"] == "manual_cooldown"
    assert len(captured["messages"]) == len(expected)
    flow.bridge.record_visible_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_proactive_text_survives_when_normal_reply_claims_image_during_selection(
    flow,
):
    generate = flow.context.llm_generate.side_effect
    scope = flow.plugin.owner_memory.shared_scope

    async def normal_reply_wins(**kwargs):
        result = await generate(**kwargs)
        if "image_urls" in kwargs:
            assert flow.library.meme_usage.claim(scope, "b" * 64, automatic=True)
        return result

    flow.context.llm_generate.side_effect = normal_reply_wins
    result = await flow.plugin.initiative.proactive(manual=True)
    assert result["reason"] == "sent"
    assert result["has_image"] is False
    assert len(flow.context.send_message.await_args.args[1].chain) == 1
    assert len(flow.library.meme_usage.status(scope)["recent"]) == 1


@pytest.mark.asyncio
async def test_scheduled_message_cannot_invent_a_human_image_request(flow):
    flow.reaction_action = "requested"
    result = await flow.plugin.initiative.proactive(manual=True)
    assert result["reason"] == "sent"
    assert result["has_image"] is False
    flow.search.assert_not_awaited()
