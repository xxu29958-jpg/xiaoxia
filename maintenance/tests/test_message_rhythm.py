"""Native response delivery: semantics, media order, interruption and uncertainty."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.message_rhythm import prepare_bubbles

from astrbot.core.message.components import Image, Plain, Reply
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.pipeline.respond.stage import RespondStage
from maintenance.tests.test_browser_url_messages import event


def reply(chain, *, channel="aiocqhttp"):
    incoming = event(channel, "随便聊聊")
    incoming.set_extra("ada_context_prepared", True)
    incoming.set_result(
        MessageEventResult(
            chain=chain, result_content_type=ResultContentType.LLM_RESULT
        )
    )
    incoming.send = AsyncMock()
    return incoming


async def stage():
    sender = RespondStage()
    await sender.initialize(
        SimpleNamespace(
            astrbot_config={
                "platform_settings": {
                    "reply_with_mention": False,
                    "reply_with_quote": False,
                    "segmented_reply": {
                        "enable": False,
                        "only_llm_result": True,
                        "interval_method": "random",
                        "log_base": 2.6,
                    },
                }
            }
        )
    )
    return sender


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["aiocqhttp", "weixin_oc"])
async def test_semantic_bubbles_preserve_image_and_quote_order(channel):
    image = Image.fromBase64("aGVsbG8=")
    incoming = reply(
        [Reply(id="one", sender_id=123), Plain("还挺会挑。\n\n这次算你赢。"), image],
        channel=channel,
    )
    prepare_bubbles(incoming)
    trace = []
    incoming.send.side_effect = lambda chain: trace.append(chain.chain)

    async def delay(seconds):
        assert 0 <= seconds <= 0.2
        trace.append("pause")

    with (
        patch(
            "astrbot.core.pipeline.respond.segments.asyncio.sleep", side_effect=delay
        ),
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ) as hook,
    ):
        await (await stage()).process(incoming)
    assert isinstance(trace[0][0], Reply)
    assert trace[0][1].text == "还挺会挑。"
    assert trace[1] == trace[3] == "pause"
    assert trace[2][0].text == "这次算你赢。"
    assert trace[4] == [image]
    assert incoming.get_extra("reply_delivery") == {"status": "sent", "sent": 3}
    hook.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["network", "new_turn_during_pause", "guard_declined"]
)
async def test_partial_reply_never_sends_tail_or_reports_whole_reply_as_sent(failure):
    incoming = reply([Plain("第一句。\n\n第二句。\n\n第三句。")])
    prepare_bubbles(incoming)
    delivered = []

    async def send(chain):
        if delivered:
            if failure == "network":
                raise ConnectionError("unknown remote result")
            if failure == "guard_declined":
                incoming.stop_event()
                return
        delivered.append(chain.get_plain_text())

    async def pause(_):
        if failure == "new_turn_during_pause":
            incoming.stop_event()

    incoming.send.side_effect = send
    with (
        patch(
            "astrbot.core.pipeline.respond.segments.asyncio.sleep", side_effect=pause
        ),
        patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ) as hook,
    ):
        await (await stage()).process(incoming)
    assert delivered == ["第一句。"]
    assert incoming.get_extra("reply_delivery")["sent"] == 1
    assert incoming.get_extra("reply_delivery")["status"] != "sent"
    hook.assert_not_awaited()
    assert incoming.get_result() is None


@pytest.mark.parametrize(
    "text",
    [
        "请运行：\n\n```python\nprint('ok')\n```",
        "第一步\n\n1. 打开设置\n2. 保存",
        "在这里：\n\nhttps://example.com/",
        "正文" * 200 + "\n\n结尾",
    ],
)
def test_structured_answers_are_not_split(text):
    incoming = reply([Plain(text)])
    prepare_bubbles(incoming)
    assert [x.text for x in incoming.get_result().chain] == [text]
    assert not incoming.get_extra("segmented_reply_prepared")


@pytest.mark.asyncio
async def test_image_only_and_unrelated_plugin_results_stay_native():
    for chain, prepared in (
        ([Image.fromBase64("aGVsbG8=")], True),
        ([Plain("完整\n\n回复")], False),
    ):
        incoming = reply(chain)
        incoming.set_extra("ada_context_prepared", prepared)
        prepare_bubbles(incoming)
        with patch(
            "astrbot.core.pipeline.respond.stage.call_event_hook",
            new=AsyncMock(return_value=False),
        ):
            await (await stage()).process(incoming)
        assert incoming.send.await_count == 1
        assert incoming.send.await_args.args[0].chain == chain
