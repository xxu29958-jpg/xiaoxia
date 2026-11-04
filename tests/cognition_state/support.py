"""Real native storage and chat tools; all identities and messages are synthetic."""

# ruff: noqa: E402 -- refuse an unisolated runtime before importing AstrBot

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

assert os.environ.get("ASTRBOT_ROOT"), (
    "Run with an isolated ASTRBOT_ROOT before importing AstrBot"
)


from astrbot_plugin_ada_companion.memory_tools import MemoryTools
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_ada_companion.shared_core import SharedCoreMemory
from astrbot_plugin_memory_companion.core.config import ConfigView
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService
from astrbot_plugin_memory_companion.core.store import MemoryStore

from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata

QQ, WX = "qq:FriendMessage:owner", "wechat:FriendMessage:owner-wx"


def event(origin=QQ, text="记住我喜欢自然说话。", message_id="q1", received_ns=None):
    channel, _, sender = origin.split(":", 2)
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = channel
    message.sender = MessageMember(sender, "Owner")
    message.message = [Plain(text)]
    message.message_str = text
    message.message_id = message_id
    message.timestamp = 0
    result = AstrMessageEvent(
        text, message, PlatformMetadata(channel, "fixture", channel), sender
    )
    if received_ns:
        setattr(result, "_ada_memory_received_ns", received_ns)
    return result


def open_service(folder):
    service = object.__new__(MemoryCompanionService)
    service.config = ConfigView({})
    service.store = MemoryStore(Path(folder) / "memory.db")
    service.store.initialize()
    return service


class Harness:
    def __init__(self, test):
        self.temp = tempfile.TemporaryDirectory(prefix="xiaoxia-core-")
        test.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.links = OwnerMemory(self.folder / "owners.json", [QQ, WX])
        self.first = event()
        self.links.observe(self.first)
        self.links.observe(event(WX))
        self.service = open_service(self.folder)
        test.addCleanup(lambda: self.service.store.close())
        self.core = SharedCoreMemory(self.links)
        self.tools = MemoryTools(
            self.links,
            None,
            None,
            SimpleNamespace(memory=lambda: SimpleNamespace(_plugin=self.service)),
            WX,
        )

    async def call(self, source=None, **kwargs):
        source = source or self.first
        self.links.observe(source)
        return json.loads(await self.tools.shared_core_memory(source, **kwargs))

    async def seed_copies(self, label="voice"):
        for origin in (QQ, WX):
            payload = {
                **self.links.context_variants(origin)[0],
                "label": label,
                "content": "old",
                "target_id": origin.split(":", 2)[-1],
                "kind": "preference",
            }
            receipt = await self.service.save_core_memory_block(payload)
            if not receipt["ok"]:
                raise AssertionError(receipt)
        self.first = event()
        self.links.observe(self.first)

    async def blocks(self):
        return (await self.call())["blocks"]

    def reopen(self):
        self.service.store.close()
        self.service = open_service(self.folder)
