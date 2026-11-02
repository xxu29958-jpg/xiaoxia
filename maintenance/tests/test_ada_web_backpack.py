import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_ada_companion.public_browser import PublicBrowser
from astrbot_plugin_ada_companion.public_transport import (
    BackpackError,
    PublicTransport,
    browse_url,
    public_addresses,
)
from astrbot_plugin_ada_companion.web_notebook import WebNotebook


class BackpackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = WebNotebook(self.folder)
        self.shot = self.store.folder / "page.jpg"
        self.shot.write_bytes(b"test-only-image")
        self.page = {
            "page_id": "page",
            "url": "https://example.com/",
            "title": "A public page",
            "text": "A source fact. Another paragraph.",
        }
        self.store.save_visit("owner", self.page, self.shot)

    def test_private_protocol_credentials_actions_and_attachments_are_blocked(self):
        for url in (
            "file:///C:/secret",
            "http://127.0.0.1:6185",
            "https://169.254.169.254/",
            "https://[::1]/",
            "http://2130706433/",
            "https://host.internal/",
            "https://user:pass@example.com/",
            "https://example.com:6185/",
            "https://example.com/login",
            "https://example.com/a.zip",
            "https://example.com/?action=buy",
            "https://example.com/?access_token=private",
        ):
            with self.subTest(url=url), self.assertRaises(BackpackError):
                browse_url(url)

    def test_canonical_urls_remove_tracking_fragments(self):
        self.assertEqual(
            browse_url("https://EXAMPLE.com/?utm_source=x&a=1#hello"),
            "https://example.com/?a=1",
        )

    def test_public_host_resolving_to_private_or_mixed_addresses_fails_closed(self):
        for addresses in (
            ["127.0.0.1"],
            ["169.254.169.254"],
            ["198.18.0.2"],
            ["93.184.216.34", "10.0.0.2"],
            [],
        ):
            with self.subTest(addresses=addresses), self.assertRaises(BackpackError):
                public_addresses(
                    {"Status": 0, "Answer": [{"type": 1, "data": x} for x in addresses]}
                )
        self.assertEqual(
            public_addresses(
                {"Status": 0, "Answer": [{"type": 1, "data": "93.184.216.34"}]}
            ),
            ["93.184.216.34"],
        )

    def test_bookmark_persistence_deduplication_and_scope(self):
        first = self.store.bookmarks("owner", "save", "page", "A favorite")
        reloaded = WebNotebook(self.folder)
        second = reloaded.bookmarks("owner", "save", "page", "Updated note")
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["bookmark"]["id"], second["bookmark"]["id"])
        self.assertEqual(
            first["bookmark"]["created_at"], second["bookmark"]["created_at"]
        )
        self.assertEqual(
            len(reloaded.bookmarks("owner", "list", query="updated")["bookmarks"]), 1
        )
        self.assertEqual(reloaded.bookmarks("stranger", "list")["bookmarks"], [])
        self.assertFalse(
            reloaded.bookmarks("stranger", "delete", item_id=first["bookmark"]["id"])[
                "ok"
            ]
        )
        self.assertTrue(
            reloaded.bookmarks("owner", "delete", item_id=first["bookmark"]["id"])["ok"]
        )

    def test_unvisited_pages_cannot_be_saved_or_shared(self):
        with self.assertRaises(BackpackError):
            self.store.bookmarks("owner", "save", "invented", "Fake")
        with self.assertRaises(BackpackError):
            self.store.visit("stranger", "page")

    def test_web_quotes_must_exist_and_comments_stay_separate(self):
        with self.assertRaises(BackpackError):
            self.store.notes("owner", "save", "page", source_quote="Something invented")
        saved = self.store.notes(
            "owner",
            "save",
            "page",
            source_quote="A source fact.",
            comment="I like this",
            next_step="Read the next page",
        )
        notes = WebNotebook(self.folder).notes("owner", "list")["notes"]
        self.assertEqual(notes[0]["source_quote"], "A source fact.")
        self.assertEqual(notes[0]["comment"], "I like this")
        self.assertEqual(notes[0]["url"], self.page["url"])
        self.assertFalse(
            self.store.notes("stranger", "delete", item_id=saved["id"])["ok"]
        )

    def test_export_only_contains_own_bookmarks_and_notes(self):
        self.store.bookmarks("owner", "save", "page", "A favorite")
        data = json.loads(self.store.export("owner").read_text(encoding="utf-8"))
        self.assertEqual(len(data["bookmarks"]), 1)
        self.assertNotIn("scope", data["bookmarks"][0])
        self.assertEqual(
            json.loads(self.store.export("stranger").read_text(encoding="utf-8"))[
                "bookmarks"
            ],
            [],
        )

    async def test_master_switch_and_quota_survive_reload_without_network(self):
        self.store.set_enabled(False)
        browser = PublicBrowser(self.folder, {})
        with self.assertRaisesRegex(BackpackError, "master switch"):
            await browser.act("owner", "open", "https://example.com/")
        self.store.set_enabled(True)
        for _ in range(30):
            self.store.record("owner", "open", "", "started")
        with self.assertRaisesRegex(BackpackError, "24 hours"):
            await browser.act("owner", "open", "https://example.com/")
        self.assertFalse(browser.sessions)

    async def test_redirects_are_resolved_before_browser_and_have_a_limit(self):
        net = PublicTransport(None, None)
        net.fetch = AsyncMock(
            side_effect=[
                {"status": 302, "headers": {"location": "https://example.com/final"}},
                {"status": 200, "headers": {}, "body": b"hi"},
            ]
        )
        url, result = await net.load("https://example.com/", True)
        self.assertEqual(url, "https://example.com/final")
        self.assertEqual(result["body"], b"hi")
        net.fetch = AsyncMock(
            return_value={
                "status": 302,
                "headers": {"location": "https://example.com/"},
            }
        )
        with self.assertRaisesRegex(BackpackError, "Too many redirects"):
            await net.load("https://example.com/", True)
        self.assertEqual(net.fetch.await_count, 5)

    async def test_unknown_user_cannot_access_browser(self):
        from astrbot_plugin_ada_companion.web_tools import WebTools

        plugin = object.__new__(WebTools)
        plugin.config = {"owner_sessions": ["wechat:owner"]}
        from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

        plugin.owners = OwnerMemory(
            Path(self.temp.name) / "owners.json", ["wechat:owner"]
        )
        plugin.owner_memory = plugin.owners
        event = SimpleNamespace(
            unified_msg_origin="qq:stranger",
            get_platform_name=lambda: "qq",
            get_sender_id=lambda: "stranger",
            is_private_chat=lambda: True,
        )
        self.assertEqual(
            json.loads(await plugin.browse(event, "open", "https://example.com/"))[
                "error"
            ],
            "private_owner_only",
        )

    async def test_private_target_is_not_opened(self):
        from astrbot_plugin_ada_companion.web_tools import WebTools

        plugin = object.__new__(WebTools)
        plugin.config = {"owner_sessions": ["wechat:owner"]}
        from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

        plugin.owners = OwnerMemory(
            Path(self.temp.name) / "owners.json", ["wechat:owner"]
        )
        plugin.owner_memory = plugin.owners
        plugin.public_browser = PublicBrowser(self.folder, {})
        event = SimpleNamespace(
            unified_msg_origin="wechat:owner",
            get_platform_name=lambda: "wechat",
            get_sender_id=lambda: "owner",
            is_private_chat=lambda: True,
            get_extra=lambda *args: 0,
            set_extra=lambda *args: None,
            get_message_str=lambda: "Read https://example.com/",
        )
        result = json.loads(await plugin.browse(event, "open", "https://127.0.0.1/"))
        self.assertFalse(result["ok"])
        self.assertFalse(plugin.public_browser.sessions)

    async def test_share_errors_are_not_reported_as_sent(self):
        from astrbot_plugin_ada_companion.web_tools import WebTools

        plugin = object.__new__(WebTools)
        plugin.public_browser = PublicBrowser(self.folder, {})
        plugin.owners = SimpleNamespace(scope=lambda event: "owner")
        event = SimpleNamespace(
            get_extra=lambda *args: False,
            set_extra=lambda *args: None,
            send=AsyncMock(side_effect=RuntimeError("test")),
        )
        result = json.loads(await plugin.share_web(event, "page", "A discovery", True))
        self.assertFalse(result["ok"])
        self.assertIn("uncertain", result["error"])

    async def test_web_followup_removes_private_memory_and_game_actions(self):
        from astrbot_plugin_ada_companion.tool_policy import ToolPolicy

        from astrbot.core.agent.tool import FunctionTool, ToolSet

        plugin = object.__new__(ToolPolicy)
        plugin.config = {"owner_sessions": ["wechat:owner"]}
        from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

        plugin.owners = OwnerMemory(
            Path(self.temp.name) / "owners.json", ["wechat:owner"]
        )
        plugin.owner_memory = plugin.owners
        extras = {}
        event = SimpleNamespace(
            unified_msg_origin="wechat:owner",
            get_platform_name=lambda: "wechat",
            get_sender_id=lambda: "owner",
            is_private_chat=lambda: True,
            get_message_str=lambda: "翻到下一页",
            get_extra=lambda k, default=None: extras.get(k, default),
            set_extra=lambda k, v: extras.__setitem__(k, v),
        )
        tools = ToolSet(
            [
                FunctionTool(
                    name=name, description="test", parameters={"type": "object"}
                )
                for name in (
                    "ada_browser",
                    "ada_bookmarks",
                    "memory_companion_query",
                    "stardew_spawn",
                    "execute_shell",
                )
            ]
        )
        req = SimpleNamespace(func_tool=tools)
        await plugin.game_tool_scope(event, req)
        self.assertIsNone(tools.get_tool("memory_companion_query"))
        await plugin.isolate_web_tools(event, tools.get_tool("ada_browser"), {})
        self.assertEqual(set(tools.names()), {"ada_browser", "ada_bookmarks"})

    async def test_unknown_user_cannot_receive_game_mcp_tools(self):
        from astrbot_plugin_ada_companion.tool_policy import ToolPolicy

        plugin = object.__new__(ToolPolicy)
        plugin.config = {"owner_sessions": ["wechat:owner"]}
        from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

        plugin.owners = OwnerMemory(
            Path(self.temp.name) / "owners.json", ["wechat:owner"]
        )
        plugin.owner_memory = plugin.owners
        remaining = [
            SimpleNamespace(name="stardew_spawn", mcp_server_name="stardew-companion")
        ]
        toolset = SimpleNamespace(
            func_list=remaining, remove_tool=lambda name: remaining.clear()
        )
        event = SimpleNamespace(
            unified_msg_origin="qq:stranger",
            get_platform_name=lambda: "qq",
            get_sender_id=lambda: "stranger",
            is_private_chat=lambda: True,
            get_message_str=lambda: "play",
            get_extra=lambda key, default=None: default,
            set_extra=lambda *args: None,
        )
        await plugin.game_tool_scope(event, SimpleNamespace(func_tool=toolset))
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
