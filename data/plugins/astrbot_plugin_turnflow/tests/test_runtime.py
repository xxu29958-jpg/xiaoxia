"""Run the actual TurnFlow runtime against cancellable platform boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot_plugin_turnflow.main import ContinuousMessagePlugin


class Conversation:
    def __init__(self, path):
        self.path = path
        self.path.write_text("[]", encoding="utf-8")

    async def update_conversation(self, origin, cid, *, history):
        self.path.write_text(json.dumps(history), encoding="utf-8")


@pytest.mark.asyncio
async def test_late_rollback_cannot_erase_a_newer_successful_turn(tmp_path):
    manager = Conversation(tmp_path / "history.json")
    plugin = ContinuousMessagePlugin(SimpleNamespace(conversation_manager=manager), {})
    event = SimpleNamespace(unified_msg_origin="qq:FriendMessage:owner")
    old = plugin.turns.create_session(event.unified_msg_origin)
    old.history_conversation_id = "conversation"
    old.history_snapshot = [{"role": "user", "content": "before"}]
    plugin._record_history_generation(event, old)
    await manager.update_conversation(event.unified_msg_origin, "conversation", history=[{"role": "assistant", "content": "partial"}])
    await plugin._rollback_history(event, old)
    assert json.loads(manager.path.read_text()) == old.history_snapshot
    newer = plugin.turns.create_session(event.unified_msg_origin)
    newer.history_conversation_id = "conversation"
    plugin._advance_history_generation(event, newer)
    committed = [{"role": "assistant", "content": "new successful reply"}]
    await manager.update_conversation(event.unified_msg_origin, "conversation", history=committed)
    await plugin._rollback_history(event, old, force=True)
    assert json.loads(manager.path.read_text()) == committed
    await plugin.terminate()


@pytest.mark.asyncio
async def test_termination_drains_owned_work_and_restores_platform_method():
    plugin = ContinuousMessagePlugin(SimpleNamespace(), {})
    original = AsyncMock(return_value={"message_id": "sent"})
    bot = SimpleNamespace(send_private_msg=original)
    event = SimpleNamespace(bot=bot, unified_msg_origin="qq:FriendMessage:owner", message_obj=SimpleNamespace(raw_message={"user_id": "owner"}))
    session = plugin.turns.create_session(event.unified_msg_origin)
    plugin.turns.activate(session)
    plugin._install_send_capture(event, session)
    assert bot.send_private_msg is not original
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def pending():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = plugin._spawn_background(pending())
    plugin._reset_timer(session, 60)
    await started.wait()
    await plugin.terminate()
    assert stopped.is_set() and task.done() and session.timer_task.done()
    assert bot.send_private_msg is original
    assert not plugin._background_tasks and not plugin._bot_dispatchers
    await plugin.terminate()
    assert await bot.send_private_msg(user_id="other", message="after reload") == {"message_id": "sent"}


@pytest.mark.asyncio
async def test_failed_pipeline_rolls_back_and_never_commits_partial_send(tmp_path):
    manager = Conversation(tmp_path / "history.json")
    plugin = ContinuousMessagePlugin(SimpleNamespace(conversation_manager=manager), {})
    event = SimpleNamespace(unified_msg_origin="wechat:FriendMessage:owner")
    session = plugin.turns.create_session(event.unified_msg_origin)
    session.history_conversation_id = "conversation"
    session.history_snapshot = [{"role": "user", "content": "real previous turn"}]
    session.delivery_state = "provider_failed"
    plugin._record_history_generation(event, session)
    plugin.turns.activate(session)
    await manager.update_conversation(event.unified_msg_origin, "conversation", history=[{"role": "assistant", "content": "broken partial"}])
    await plugin._finish_pipeline(event, session, failed=True)
    assert session.invalidated
    assert json.loads(manager.path.read_text()) == session.history_snapshot
    assert session.delivery_state != "committed"
    await plugin.terminate()
