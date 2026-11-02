"""Exercise input ownership, uncertainty and cancellation at the MCP boundary."""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult, ImageContent, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_ada_companion import desktop_guard as guard
from astrbot_plugin_ada_companion import desktop_tools as desktop
from astrbot_plugin_ada_companion.qq_tools import QQTools


class Event:
    def __init__(self, scope="owner", text=""):
        self.scope, self.text, self.extra = scope, text, {}

    def get_extra(self, key):
        return self.extra.get(key)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_message_str(self):
        return self.text


class DesktopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = {
            "handle": 100,
            "pid": 20,
            "bounds": [10, 10, 200, 200],
            "process": "calculator.exe",
            "title": "Calculator",
        }
        self.event = Event()
        self.controls = desktop.DesktopTools(
            Path(self.temp.name), SimpleNamespace(scope=lambda e: e.scope)
        )
        self.session = SimpleNamespace(
            call_tool=AsyncMock(
                return_value=CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text="Screenshot Coordinate Scale: 1.333333\nVisible page text: Failed to load a preview",
                        ),
                        ImageContent(
                            type="image", mimeType="image/png", data="aGVsbG8="
                        ),
                    ]
                )
            )
        )
        self.client = SimpleNamespace(session=self.session, cleanup=AsyncMock())
        self.controls.client = self.client
        self.addAsyncCleanup(self.controls.stop)

    async def view(self):
        with (
            patch.object(desktop, "foreground", return_value=self.window),
            patch.object(desktop, "window_inventory", return_value=[self.window]),
        ):
            result = await self.controls.act(self.event)
        return json.loads(result.content[0].text)

    async def test_observation_reports_scale_without_treating_screen_text_as_error(
        self,
    ):
        data = await self.view()
        self.assertEqual(data["image_to_screen_scale"], 1.333333)
        self.assertEqual(data["windows"][0]["handle"], 100)
        self.assertTrue(data["snapshot_id"])

    async def test_focus_change_or_other_owner_never_dispatches_input(self):
        data = await self.view()
        self.session.call_tool.reset_mock()
        with patch.object(
            guard, "foreground", return_value={**self.window, "handle": 101}
        ):
            result = await self.controls.act(
                self.event, "key", {"snapshot_id": data["snapshot_id"], "key": "enter"}
            )
        self.assertFalse(json.loads(result)["ok"])
        self.session.call_tool.assert_not_awaited()
        await self.view()
        self.session.call_tool.reset_mock()
        result = await self.controls.act(
            Event("other-owner"),
            "key",
            {"snapshot_id": self.controls.snapshot["id"], "key": "enter"},
        )
        self.assertFalse(json.loads(result)["ok"])
        self.session.call_tool.assert_not_awaited()

    async def test_stale_observation_rejected_before_reading_or_touching_windows(self):
        await self.view()
        self.controls.snapshot["at"] = time.monotonic() - 100
        self.session.call_tool.reset_mock()
        with patch.object(
            guard,
            "foreground",
            side_effect=AssertionError("must not inspect stale input"),
        ):
            result = await self.controls.act(
                self.event,
                "key",
                {"snapshot_id": self.controls.snapshot["id"], "key": "enter"},
            )
        self.assertIn("stale", json.loads(result)["error"])
        self.session.call_tool.assert_not_awaited()

    async def test_timeout_after_dispatch_consumes_observation_and_never_replays(self):
        data = await self.view()
        self.session.call_tool.reset_mock()
        self.session.call_tool.side_effect = TimeoutError("input outcome unknown")
        with patch.object(guard, "foreground", return_value=self.window):
            result = await self.controls.act(
                self.event, "key", {"snapshot_id": data["snapshot_id"], "key": "enter"}
            )
            second = await self.controls.act(
                self.event, "key", {"snapshot_id": data["snapshot_id"], "key": "enter"}
            )
        self.assertFalse(json.loads(result)["ok"])
        self.assertFalse(json.loads(second)["ok"])
        self.session.call_tool.assert_awaited_once()
        self.assertEqual(self.controls.snapshot, {})

    async def test_stop_cancels_real_pending_operation_and_prevents_turn_resuming(self):
        pending = asyncio.Event()

        async def blocked_call(*args, **kwargs):
            pending.set()
            await asyncio.Future()

        self.session.call_tool.side_effect = blocked_call
        with patch.object(desktop, "foreground", return_value=self.window):
            operation = asyncio.create_task(self.controls.act(self.event, "view"))
            await asyncio.wait_for(pending.wait(), 1)
            await asyncio.wait_for(self.controls.stop(), 1)
            result = await operation
        self.assertFalse(json.loads(result)["ok"])
        self.client.cleanup.assert_awaited_once()
        self.assertIsNone(self.controls.client)
        self.assertTrue(self.event.get_extra("ada_desktop_stopped"))
        self.assertIn(
            "stopped this turn",
            json.loads(await self.controls.act(self.event))["error"],
        )

    async def test_disabled_persists_and_web_content_cannot_start_desktop(self):
        self.controls.store.enable(False)
        recreated = desktop.DesktopTools(Path(self.temp.name), self.controls.owners)
        self.assertFalse(recreated.status()["enabled"])
        result = await recreated.act(self.event)
        self.assertIn("disabled", json.loads(result)["error"])
        self.controls.store.enable(True)
        self.event.set_extra("ada_web_read_active", True)
        result = await self.controls.act(self.event)
        self.assertFalse(json.loads(result)["ok"])
        self.session.call_tool.assert_not_awaited()


class QQTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_targets_linked_owner_and_never_claims_attachment_view(self):
        bot = SimpleNamespace(
            get_friend_msg_history=AsyncMock(
                return_value={
                    "messages": [
                        {
                            "message_id": 1,
                            "sender": {"user_id": 42},
                            "message": [
                                {"type": "text", "data": {"text": "hello"}},
                                {"type": "image", "data": {"url": "private URL"}},
                            ],
                        }
                    ]
                }
            ),
            mark_private_msg_as_read=AsyncMock(),
        )
        controls = QQTools(
            SimpleNamespace(scope=lambda e: "owner"),
            SimpleNamespace(
                resolve=lambda channel: {
                    "origin": "qq:FriendMessage:42",
                    "native": {"user_id": "42"},
                }
            ),
            SimpleNamespace(get_platform_inst=lambda _: SimpleNamespace(bot=bot)),
        )
        result = json.loads(await controls.act(Event(), "history", 3))
        bot.get_friend_msg_history.assert_awaited_once_with(user_id=42, count=3)
        self.assertEqual(result["messages"][0]["text"], "hello[image]")
        self.assertTrue(result["messages"][0]["from_owner"])
        self.assertNotIn("private URL", json.dumps(result))
        self.assertFalse(json.loads(await controls.act(Event(), "read"))["ok"])
        bot.mark_private_msg_as_read.assert_not_awaited()
        result = json.loads(
            await controls.act(Event(text="把我发来的消息标为已读"), "read")
        )
        self.assertTrue(result["marked_read"])
        bot.mark_private_msg_as_read.assert_awaited_once_with(user_id=42)


if __name__ == "__main__":
    unittest.main()
