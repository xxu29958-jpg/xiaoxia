"""An interrupted turn must still close its native response stream."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_turnflow.main import ContinuousMessagePlugin


@pytest.mark.asyncio
async def test_interruption_blocks_old_text_but_delivers_native_completion():
    plugin = ContinuousMessagePlugin(SimpleNamespace(), {})
    original = AsyncMock()
    event = SimpleNamespace(
        unified_msg_origin="webchat:FriendMessage:completion-check",
        send=original,
        stop_event=Mock(),
    )
    session = plugin.turns.create_session(event.unified_msg_origin)
    plugin.turns.activate(session)
    plugin._install_event_capture(event, session)
    plugin.turns.invalidate(session.turn_id)

    await event.send("old reply must not arrive")
    original.assert_not_awaited()
    await event.send(None)
    original.assert_awaited_once_with(None)
    assert not session.send_succeeded
    assert not session.sent_message_ids
    await plugin.terminate()
