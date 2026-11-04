"""Management mutations use the same persistent topics and notebook as phone chat."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_ada_companion.dialogue import DialogueState

from maintenance.tests import test_contact_channels as channels
from maintenance.tests.working_state_support import save


@pytest.mark.asyncio
async def test_management_cannot_close_a_changed_topic_but_current_close_reaches_both_channels():
    case = channels.ContactChannelTests()
    case.setUp()
    try:
        p = case.plugin
        item_id = (
            await save(
                p.dialogue,
                channels.QQ,
                "下次聊这个游戏",
                action="save",
                title="游戏",
                content="下次接着玩",
                evidence_quote="下次聊这个游戏",
            )
        )["id"]
        old = (await p.dialogue.visible(channels.WX))[0]
        await save(
            p.dialogue,
            channels.QQ,
            "改成周末接着玩",
            action="save",
            item_id=item_id,
            title="游戏",
            content="周末接着玩",
            evidence_quote="改成周末接着玩",
        )
        with pytest.raises(ValueError, match="未关闭"):
            await p.management.apply(
                {"action": "topic_close", "id": item_id, "expected": old}
            )
        current = (await p.dialogue.visible(channels.WX))[0]
        assert current["content"] == "周末接着玩"
        result = await p.management.apply(
            {"action": "topic_close", "id": item_id, "expected": current}
        )
        assert "已结束" in result
        restored = DialogueState(p.dialogue.path, p.owner_memory, p.services)
        assert (
            await restored.visible(channels.WX)
            == await restored.visible(channels.QQ)
            == []
        )
        assert not p.policy.data["deliveries"]
    finally:
        case.doCleanups()
