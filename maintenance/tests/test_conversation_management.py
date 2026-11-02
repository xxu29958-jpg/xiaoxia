"""Management mutations use the same persistent topics and notebook as phone chat."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_ada_companion.dialogue import DialogueState

from maintenance.tests import test_contact_channels as channels


@pytest.mark.asyncio
async def test_management_cannot_close_a_changed_topic_but_current_close_reaches_both_channels():
    case = channels.ContactChannelTests()
    case.setUp()
    try:
        p = case.plugin
        item_id = p.dialogue.update(
            channels.QQ,
            "下次聊这个游戏",
            action="save",
            title="游戏",
            content="下次接着玩",
            evidence_quote="下次聊这个游戏",
        )["id"]
        old = p.dialogue.visible(channels.WX)[0]
        p.dialogue.update(
            channels.QQ,
            "改成周末接着玩",
            action="save",
            item_id=item_id,
            title="游戏",
            content="周末接着玩",
            evidence_quote="改成周末接着玩",
        )
        with pytest.raises(ValueError, match="已经更新"):
            await p.management.apply(
                {"action": "topic_close", "id": item_id, "expected": old}
            )
        current = p.dialogue.visible(channels.WX)[0]
        assert current["content"] == "周末接着玩"
        result = await p.management.apply(
            {"action": "topic_close", "id": item_id, "expected": current}
        )
        assert "已结束" in result
        restored = DialogueState(p.dialogue.path, linked_sessions=p.owner_memory.owners)
        assert restored.visible(channels.WX) == restored.visible(channels.QQ) == []
        assert not p.policy.data["deliveries"]
    finally:
        case.doCleanups()
