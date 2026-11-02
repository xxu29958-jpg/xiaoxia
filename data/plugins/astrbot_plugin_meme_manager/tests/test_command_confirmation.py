"""Native session-waiter isolation, cancellation and timeout behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.utils.session_waiter import FILTERS, USER_SESSIONS, SessionWaiter
from astrbot_plugin_meme_manager.mixins.commands import CommandMixin, SenderScopedSessionFilter


def event(origin, sender, text=""):
    return SimpleNamespace(unified_msg_origin=origin, get_sender_id=lambda: sender,
                           message_str=text, send=AsyncMock(), plain_result=lambda value: value)


@pytest.mark.asyncio
async def test_confirmation_isolated_by_platform_chat_and_sender():
    subject = CommandMixin()
    owner = event("qq:GroupMessage:g", "owner")
    scope = SenderScopedSessionFilter()
    filters_before = list(FILTERS)
    task = asyncio.create_task(subject._wait_for_command_confirmation(owner))
    await asyncio.sleep(0)
    try:
        for other in [event("wechat:GroupMessage:g", "owner", "确认"), event(owner.unified_msg_origin, "other", "确认")]:
            await SessionWaiter.trigger(scope.filter(other), other)
            assert not task.done()
        cancel = event(owner.unified_msg_origin, "owner", "取消")
        await SessionWaiter.trigger(scope.filter(cancel), cancel)
        assert await task is False
        assert cancel.send.await_args.args == ("已取消本次操作。",)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert scope.filter(owner) not in USER_SESSIONS
    assert FILTERS == filters_before


@pytest.mark.asyncio
async def test_confirmation_timeout_then_new_request_can_succeed():
    subject = CommandMixin()
    owner = event("qq:FriendMessage:owner", "owner")
    assert await subject._wait_for_command_confirmation(owner, timeout=0.01) is False
    assert "超时" in owner.send.await_args.args[0]
    task = asyncio.create_task(subject._wait_for_command_confirmation(owner))
    await asyncio.sleep(0)
    reply = event(owner.unified_msg_origin, "owner", "确认")
    await SessionWaiter.trigger(SenderScopedSessionFilter().filter(reply), reply)
    assert await task is True
