"""Synthetic native events and temporary storage for continuing-state consumers."""

import os
from types import SimpleNamespace
from uuid import uuid4

from astrbot_plugin_ada_companion.dialogue import DialogueState
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_memory_companion.core.store import MemoryStore

from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata


def native_dialogue(path, cleanup, owners=None, bridge=None):
    assert os.environ.get("ASTRBOT_ROOT"), "Run with an isolated AstrBot root"
    owners = owners or OwnerMemory(path.with_name("owners.json"), [])
    store = MemoryStore(path.with_suffix(".db"))
    store.initialize()
    cleanup(store.close)
    bridge = bridge or SimpleNamespace()
    bridge._plugin = SimpleNamespace(store=store)
    services = SimpleNamespace(memory=lambda: bridge)
    state = DialogueState(path, owners, services)
    for origin in owners.owners:
        incoming(state, origin, "synthetic identity observation")
    return state


def incoming(state, origin, text, message_id=None):
    channel, _, sender = origin.split(":", 2)
    identity = state.owners.contexts.get(origin, {})
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = identity.get("bot_id", "bot")
    message.sender = MessageMember(sender, "Fixture owner")
    message.message = [Plain(text)]
    message.message_str = text
    message.message_id = message_id or uuid4().hex
    message.timestamp = 0
    result = AstrMessageEvent(
        text,
        message,
        PlatformMetadata(identity.get("platform", channel), "fixture", channel),
        sender,
    )
    state.owners.observe(result)
    return result


async def save(state, origin, text, **kwargs):
    event = incoming(state, origin, text)
    await state.snapshot(
        origin, event=event, include_closed=True, now=kwargs.get("now")
    )
    return await state.update(event, **kwargs)
