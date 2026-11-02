"""Semantic review applies only exact current evidence to unchanged owner topics."""

import asyncio
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.dialogue import DialogueState
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_ada_companion.policy import DeliveryPolicy
from astrbot_plugin_ada_companion.prompting import ContextBuilder
from astrbot_plugin_ada_companion.thread_review import ThreadReview

from astrbot.core.provider.entities import ProviderRequest
from maintenance.tests.test_browser_url_messages import event

QQ, WX = "qq:FriendMessage:owner", "wechat-companion:FriendMessage:owner"


@pytest.fixture
def review(tmp_path):
    owners = OwnerMemory(tmp_path / "owners.json", [QQ, WX])
    dialogue = DialogueState(tmp_path / "topics.json", linked_sessions=[QQ, WX])
    policy = DeliveryPolicy(tmp_path / "delivery.json", WX)
    return ThreadReview(dialogue, owners, policy)


def topic(review):
    result = review.dialogue.update(
        QQ,
        "明天面试完告诉你",
        action="save",
        title="面试结果",
        content="用户打算面试后聊结果",
        evidence_quote="明天面试完告诉你",
    )
    return result["id"]


def response(updates):
    return SimpleNamespace(completion_text=json.dumps({"resolve": updates}))


@pytest.mark.asyncio
async def test_answer_on_wechat_closes_qq_topic_and_survives_restart(review):
    item_id = topic(review)
    review.models = SimpleNamespace(
        generate=AsyncMock(
            return_value=response([{"id": item_id, "evidence_quote": "过了"}])
        )
    )
    incoming = event("wechat-companion", "过了，咱换个话题")
    await review.process(
        incoming, [{"role": "assistant", "content": "面试结果出来了吗？"}]
    )
    restored = DialogueState(review.dialogue.path, linked_sessions=[QQ, WX])
    assert restored.visible(QQ) == restored.visible(WX) == []
    assert restored.data["sessions"][QQ][0]["resolution_quote"] == "过了"
    assert incoming.get_extra("ada_topics_resolved") == [
        {"id": item_id, "title": "面试结果"}
    ]
    await review.process(incoming, [])
    assert review.models.generate.await_count == 1


@pytest.mark.asyncio
async def test_foreign_id_and_invented_or_old_evidence_cannot_close(review):
    item_id = topic(review)
    review.models = SimpleNamespace(
        generate=AsyncMock(
            return_value=response(
                [
                    {"id": "someone-else", "evidence_quote": "还没"},
                    {"id": item_id, "evidence_quote": "已经结束了"},
                ]
            )
        )
    )
    await review.process(
        event("wechat-companion", "还没"),
        [{"role": "assistant", "content": "已经结束了"}],
    )
    assert len(review.dialogue.visible(QQ)) == 1
    before = review.models.generate.await_count
    await review.process(event("qq", "已经结束了", sender="stranger"), [])
    assert review.models.generate.await_count == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["new_message", "topic_edited", "event_stopped"])
async def test_review_result_is_discarded_if_context_changes_while_waiting(
    review, change
):
    item_id = topic(review)
    incoming = event("wechat-companion", "过了")

    async def generate(**_):
        if change == "new_message":
            review.policy.data["last_user"] += 1
        elif change == "event_stopped":
            incoming.stop_event()
        else:
            review.dialogue.update(
                QQ,
                "我说的是复试，下周还有",
                action="save",
                item_id=item_id,
                title="面试结果",
                content="下周还有复试",
                evidence_quote="下周还有",
            )
        return response([{"id": item_id, "evidence_quote": "过了"}])

    review.models = SimpleNamespace(generate=generate)
    await review.process(incoming, [])
    assert len(review.dialogue.visible(WX)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [TimeoutError(), RuntimeError("provider unavailable"), "invalid-json", "disk"],
)
async def test_optional_review_failure_keeps_conversation_and_can_recover(
    review, fault
):
    item_id = topic(review)
    before = review.dialogue.path.read_bytes()
    model = AsyncMock(
        return_value=response([{"id": item_id, "evidence_quote": "过了"}])
    )
    review.models = SimpleNamespace(generate=model)
    if isinstance(fault, Exception):
        model.side_effect = fault
    elif fault == "invalid-json":
        model.return_value = SimpleNamespace(completion_text="not JSON")
    with (
        patch.object(review.dialogue, "update", side_effect=OSError("disk full"))
        if fault == "disk"
        else nullcontext()
    ):
        await review.process(event("wechat-companion", "过了"), [])
    assert review.dialogue.path.read_bytes() == before
    assert review.last["reason"] == "review_unavailable"
    model.side_effect = None
    model.return_value = response([{"id": item_id, "evidence_quote": "过了"}])
    await review.process(event("wechat-companion", "过了"), [])
    assert review.dialogue.visible(QQ) == []


@pytest.mark.asyncio
async def test_cancelled_review_does_not_turn_into_background_work(review):
    topic(review)
    review.models = SimpleNamespace(
        generate=AsyncMock(side_effect=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await review.process(event("wechat-companion", "过了"), [])
    assert len(review.dialogue.visible(QQ)) == 1


@pytest.mark.asyncio
async def test_closed_topic_reaches_native_request_without_reappearing_as_pending(
    review,
):
    item_id = topic(review)
    review.models = SimpleNamespace(
        generate=AsyncMock(
            return_value=response([{"id": item_id, "evidence_quote": "过了"}])
        )
    )
    context = SimpleNamespace(
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value=None)
        )
    )
    builder = ContextBuilder(
        {},
        review.owners,
        review.dialogue,
        review.policy,
        context,
        SimpleNamespace(memory=lambda: None, plugin=lambda _: None),
    )
    builder.review = review
    request = ProviderRequest(system_prompt="Original character card")
    await builder.prepare(event("wechat-companion", "过了"), request)
    assert review.dialogue.visible(QQ) == []
    assert item_id in request.system_prompt
    assert "[这轮已经聊完的话题" in request.system_prompt
    assert "[还没聊完的事" not in request.system_prompt
    assert request.system_prompt.startswith("Original character card")
