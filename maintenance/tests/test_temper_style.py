"""Temper cues keep replies personal, bounded, and out of stored history."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_ada_companion.conversation_style import CHAT_STYLE
from astrbot_plugin_ada_companion.temper import temper_context, temper_cues

from astrbot.core.provider.entities import ProviderRequest
from maintenance.tests import test_contact_channels as channels
from maintenance.tests.test_browser_url_messages import event


def replies(*texts):
    return [
        {
            "role": "assistant",
            "channel": "qq",
            "source_id": f"s{i}",
            "part": 0,
            "text": text,
        }
        for i, text in enumerate(texts)
    ]


def test_challenge_cue_does_not_echo_the_user_message():
    text = "不对吧，你上次说的那个我不同意"
    cues = temper_cues([], text)
    assert any("disagreement" in cue for cue in cues)
    assert all("不对吧" not in cue and text not in cue for cue in cues)


def test_serious_topic_suppresses_banter_and_habit_cues():
    history = replies("真的吗？", "然后呢？", "你说呢？")
    cues = temper_cues(history, "我确诊了，有点撑不住")
    assert cues == [temper_cues([], "我确诊了")[0]]
    assert "Serious moment" in cues[0]


def test_noticing_a_bot_tone_asks_for_an_honest_answer():
    cues = temper_cues([], "你是不是有点敷衍，像机器人")
    assert len(cues) == 1 and "bot" in cues[0]
    assert "not interested" in cues[0]


def test_repeated_questions_become_a_habit_cue():
    history = replies("真的吗？", "然后呢？", "你说呢？")
    cues = temper_cues(history, "嗯")
    assert any("question" in cue for cue in cues)


def test_agreeable_history_invites_one_honest_preference():
    history = replies("确实是这样。", "哈哈，那也没办法。")
    cues = temper_cues(history, "嗯")
    assert any("preference" in cue for cue in cues)


def test_temper_context_is_bounded_and_labelled():
    result = temper_context([], "好烦啊，今天又被临时改需求")
    assert result.startswith("[Expression note for this reply; internal guidance")
    assert len(temper_cues([], "好烦啊，今天又被临时改需求")) <= 2
    assert temper_context([], "今天天气不错") == ""


@pytest.mark.asyncio
async def test_prepare_adds_one_temporary_expression_note():
    case = channels.ContactChannelTests()
    case.setUp()
    try:
        incoming = event("qq-personal-companion", "不对吧，我觉得没那么好", "12345678")
        req = ProviderRequest(
            system_prompt="Original Ada card",
            contexts=replies("真的吗？", "然后呢？", "你说呢？"),
        )
        await case.plugin.prompt.prepare(incoming, req)
        assert len(req.extra_user_content_parts) == 1
        part = req.extra_user_content_parts[0]
        assert part._no_save is True
        assert part.text.startswith("[Expression note for this reply")
        await case.plugin.prompt.prepare(incoming, req)
        assert len(req.extra_user_content_parts) == 1
        assert req.system_prompt.count(CHAT_STYLE) == 1
    finally:
        case.doCleanups()


@pytest.mark.asyncio
async def test_temper_switch_off_leaves_no_expression_note():
    case = channels.ContactChannelTests()
    case.setUp()
    try:
        case.plugin.config["temper_enabled"] = False
        incoming = event("qq-personal-companion", "不对吧，我觉得没那么好", "12345678")
        req = ProviderRequest(system_prompt="Original Ada card")
        await case.plugin.prompt.prepare(incoming, req)
        assert req.extra_user_content_parts == []
    finally:
        case.doCleanups()
