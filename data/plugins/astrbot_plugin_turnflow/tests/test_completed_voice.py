"""A finished voice clip reaches the agent without a keyboard collection wait."""

import asyncio
from types import SimpleNamespace

import pytest
from astrbot_plugin_turnflow.main import ContinuousMessagePlugin

from maintenance.tests.test_browser_url_messages import event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,voice_input,ready",
    [
        ("忙完啦，我回来了。", True, True),
        ("刚才那段你听清楚了吗？", True, True),
        ("忙完啦，我回来了。", False, False),
        ("等下，我再发一条。", True, False),
        ("我刚想说的是，", True, False),
    ],
)
async def test_complete_voice_flushes_but_text_and_unfinished_voice_wait(
    text, voice_input, ready
):
    plugin = ContinuousMessagePlugin(
        SimpleNamespace(),
        {"debounce_time": 1.0, "min_debounce_time": 0.5},
    )
    incoming = event("aiocqhttp", text)
    incoming.set_extra("voice_input", voice_input)
    task = asyncio.create_task(plugin.handle_private_msg(incoming))
    try:
        if ready:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.3)
        else:
            await asyncio.sleep(0.05)
            assert not task.done()
            session = plugin.turns.get_session(incoming.unified_msg_origin)
            session.flush_event.set()
            await task
        assert incoming.get_extra("turnflow_user_text") == text
        assert not incoming.is_stopped()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await plugin.terminate()
