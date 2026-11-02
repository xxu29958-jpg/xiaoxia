"""Cross-channel state, durable core edits, concurrency and reload regressions."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_ada_companion.chat_controls import ChatControls
from astrbot_plugin_ada_companion.lifecycle import Lifecycle
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_ada_companion.policy import DeliveryPolicy
from astrbot_plugin_ada_companion.public_browser import PublicBrowser
from astrbot_plugin_ada_companion.shared_core import SharedCoreMemory
from astrbot_plugin_ada_companion.web_notebook import BackpackError
from astrbot_plugin_memory_companion.core.config import ConfigView
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService
from astrbot_plugin_memory_companion.core.store import MemoryStore

QQ = "qq-personal-companion:FriendMessage:owner"
WX = "wechat-companion:FriendMessage:owner-wx"


def event(origin, text="hello"):
    return SimpleNamespace(
        unified_msg_origin=origin,
        is_private_chat=lambda: True,
        get_sender_id=lambda: origin.split(":", 2)[-1],
        get_self_id=lambda: origin.split(":", 1)[0],
        get_platform_name=lambda: origin.split(":", 1)[0],
        get_message_str=lambda: text,
        plain_result=lambda value: value,
        send=AsyncMock(),
        stop_event=lambda: None,
    )


class SharedCoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.links = OwnerMemory(self.folder / "owners.json", [QQ, WX])
        for origin in self.links.owners:
            self.links.observe(event(origin))
        self.core = SharedCoreMemory(self.links)
        self.service = object.__new__(MemoryCompanionService)
        self.service.config = ConfigView({})
        self.service.store = MemoryStore(self.folder / "memory.db")
        self.service.store.initialize()
        self.addCleanup(self.service.store.close)

    async def call(self, origin=QQ, **kwargs):
        return await self.core.execute(origin, self.service, WX, **kwargs)

    async def test_write_on_qq_read_edit_on_wechat_and_delete_on_qq(self):
        self.assertTrue(
            (await self.call(action="set", label="voice", content="gentle"))["ok"]
        )
        before = await self.call(WX)
        self.assertEqual(before, await self.call(QQ))
        memory_id = before["blocks"][0]["id"]
        self.assertTrue(
            (
                await self.call(
                    WX, action="set", label="voice", content="gentle and natural"
                )
            )["ok"]
        )
        after = await self.call(QQ)
        self.assertEqual(after["blocks"][0]["id"], memory_id)
        self.assertEqual(after["blocks"][0]["revision"], 2)
        self.assertEqual(after["blocks"][0]["content"], "gentle and natural")
        restored = OwnerMemory(self.links.path, [QQ, WX])
        self.core = SharedCoreMemory(restored)
        self.assertEqual(await self.call(WX), after)
        self.assertTrue((await self.call(QQ, action="delete", label="voice"))["ok"])
        self.assertEqual((await self.call(WX))["blocks"], [])

    async def test_stale_management_edit_cannot_overwrite_newer_chat_preference(self):
        await self.call(action="set", label="voice", content="gentle")
        await self.call(WX, action="set", label="voice", content="gentle and natural")
        refused = await self.call(
            action="set", label="voice", content="old tab edit", expected="gentle"
        )
        self.assertFalse(refused["ok"])
        self.assertEqual(
            (await self.call())["blocks"][0]["content"], "gentle and natural"
        )

    async def test_existing_copies_are_updated_together_and_stranger_is_denied(self):
        for origin in (QQ, WX):
            payload = {
                **self.links.context_variants(origin)[0],
                "label": "boundary",
                "content": "old",
                "target_id": origin.split(":", 2)[-1],
                "kind": "boundary",
            }
            self.assertTrue((await self.service.save_core_memory_block(payload))["ok"])
        result = await self.call(WX, action="set", label="boundary", content="new")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["writes"]), 2)
        self.assertEqual({x["content"] for x in (await self.call())["blocks"]}, {"new"})
        result = await self.call(
            "qq-personal-companion:FriendMessage:stranger",
            action="delete",
            label="boundary",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(len((await self.call())["blocks"]), 2)

    async def test_simultaneous_edits_use_one_record_and_increase_revision(self):
        results = await asyncio.gather(
            self.call(QQ, action="set", label="topic", content="QQ change"),
            self.call(WX, action="set", label="topic", content="WeChat change"),
        )
        self.assertTrue(all(x["ok"] for x in results))
        blocks = (await self.call())["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["revision"], 2)

    async def test_partial_read_failure_cannot_create_a_duplicate(self):
        await self.call(action="set", label="voice", content="gentle")
        with patch.object(
            self.service, "core_memories_for_context", side_effect=OSError("disk read")
        ):
            result = await self.call(
                WX, action="set", label="voice", content="bad replacement"
            )
            self.assertFalse(result["ok"])
        self.assertEqual((await self.call())["blocks"][0]["content"], "gentle")

    async def test_native_write_failure_is_reported_without_false_success(self):
        with patch.object(
            self.service, "save_core_memory_block", side_effect=OSError("disk full")
        ):
            result = await self.call(action="set", label="voice", content="gentle")
            self.assertFalse(result["ok"])
        self.assertEqual((await self.call())["blocks"], [])

    async def test_both_channels_use_one_tool_scope_and_activity_state(self):
        self.assertEqual(self.links.scope(event(QQ)), self.links.scope(event(WX)))
        policy = DeliveryPolicy(self.folder / "state.json", WX)
        controls = ChatControls({"owner_sessions": [QQ, WX]}, self.links, policy)
        await controls.receive(event(QQ, "晚安"))
        restored = DeliveryPolicy(policy.path, WX)
        self.assertGreater(restored.data["closing_until"], restored.data["last_user"])
        await controls.receive(event(WX, "开启语音回复"))
        self.assertTrue(DeliveryPolicy(policy.path, WX).data["voice_sessions"]["owner"])

    def test_corrupt_owner_file_is_not_silently_replaced(self):
        self.links.path.write_text("[]", encoding="utf-8")
        restored = OwnerMemory(self.links.path, [QQ, WX])
        restored.observe(event(QQ))
        self.assertFalse(restored.healthy)
        self.assertEqual(self.links.path.read_text("utf-8"), "[]")

    def test_invalid_but_parseable_delivery_state_fails_closed(self):
        for value in ([], {"version": 1, "target": WX, "last_user": "bad"}):
            path = self.folder / "bad-state.json"
            raw = json.dumps(value)
            path.write_text(raw, encoding="utf-8")
            policy = DeliveryPolicy(path, WX)
            self.assertEqual(policy.gate(0), "state_unreadable")
            self.assertEqual(path.read_text("utf-8"), raw)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_reload_waits_for_active_work_and_refuses_new_work(self):
        lifecycle = Lifecycle()
        entered, release = asyncio.Event(), asyncio.Event()

        async def request():
            async with lifecycle.operation():
                entered.set()
                await release.wait()

        old = asyncio.create_task(request())
        await entered.wait()
        close = asyncio.create_task(lifecycle.close())
        await asyncio.sleep(0)
        self.assertFalse(close.done())
        with self.assertRaises(RuntimeError):
            async with lifecycle.operation():
                pass
        release.set()
        await asyncio.gather(old, close)
        self.assertEqual(lifecycle.active, {})

    async def test_same_owner_browser_first_open_cannot_race_and_stop_cancels_start(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            browser = PublicBrowser(Path(td), {})
            await browser.start_lock.acquire()
            first = asyncio.create_task(
                browser.act("owner", "open", "https://example.com")
            )
            await asyncio.sleep(0)
            with self.assertRaisesRegex(BackpackError, "already running"):
                await browser.act("owner", "open", "https://example.com")
            await browser.close("owner")
            self.assertTrue(first.cancelled())
            self.assertFalse(browser.status("owner")["running"])
            browser.start_lock.release()


if __name__ == "__main__":
    unittest.main()
