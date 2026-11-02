"""Verify cross-channel continuity without broadening access to other people."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[2] / "data/plugins/astrbot_plugin_ada_companion"
modules = {}
for name in ("owner_memory", "dialogue"):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    modules[name] = module
OwnerMemory = modules["owner_memory"].OwnerMemory
DialogueState = modules["dialogue"].DialogueState


class OwnerMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.qq = "qq:FriendMessage:verified-owner"
        self.wx = "wechat:FriendMessage:verified-owner-wx"
        self.links = OwnerMemory(self.root / "links.json", [self.qq, self.wx])
        for origin in (self.qq, self.wx):
            self.links.observe(
                SimpleNamespace(
                    unified_msg_origin=origin,
                    is_private_chat=lambda: True,
                    get_platform_name=lambda: "test-platform",
                    get_sender_id=lambda origin=origin: origin.split(":", 2)[-1],
                    get_self_id=lambda origin=origin: origin.split(":", 1)[0],
                )
            )

    async def test_qq_reads_wechat_memories_and_recent_dialogue_with_attribution(self):
        bridge = SimpleNamespace(
            compose_context=AsyncMock(return_value="用户明确喜欢猫咪表情")
        )
        conversation = SimpleNamespace(
            updated_at=100,
            history=json.dumps(
                [
                    {"role": "tool", "content": "DO NOT COPY TOOL OUTPUT"},
                    {"role": "user", "content": "以后叫我阿树"},
                    {"role": "assistant", "content": "好，阿树。"},
                ]
            ),
        )
        cm = SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="wx-cid"),
            get_conversation=AsyncMock(return_value=conversation),
        )
        result = await self.links.compose(self.qq, "我们刚说叫什么", bridge, cm)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sources"][0]["channel"], "wechat")
        self.assertIn("猫咪", result["sources"][0]["memories"])
        self.assertEqual(
            [x["role"] for x in result["sources"][0]["recent"]], ["user", "assistant"]
        )
        self.assertNotIn("DO NOT COPY", json.dumps(result))
        ctx = bridge.compose_context.call_args.kwargs["session_context"]
        self.assertEqual(ctx["session_id"], self.wx)
        self.assertTrue(ctx["strict_session_only"])
        cm.get_conversation.assert_awaited_once_with(self.wx, "wx-cid")

    async def test_wechat_reads_qq_and_unknown_accounts_cannot_read_either(self):
        bridge = SimpleNamespace(compose_context=AsyncMock(return_value="QQ agreement"))
        cm = SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value=None))
        result = await self.links.compose(self.wx, "约定", bridge, cm)
        self.assertEqual(result["sources"][0]["channel"], "qq")
        bridge.compose_context.reset_mock()
        cm.get_curr_conversation_id.reset_mock()
        for origin in (
            "qq:FriendMessage:stranger",
            "qq:GroupMessage:verified-owner",
            "webchat:FriendMessage:astrbot",
        ):
            result = await self.links.compose(origin, "约定", bridge, cm)
            self.assertFalse(result["ok"])
            self.assertEqual(result["sources"], [])
        bridge.compose_context.assert_not_awaited()
        cm.get_curr_conversation_id.assert_not_awaited()

    async def test_identity_persists_and_read_failure_does_not_fake_memory(self):
        restored = OwnerMemory(self.links.path, [self.qq, self.wx])
        self.assertEqual(restored.contexts, self.links.contexts)
        bridge = SimpleNamespace(compose_context=AsyncMock(side_effect=TimeoutError()))
        cm = SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value=None))
        result = await restored.compose(self.wx, "昵称", bridge, cm)
        self.assertFalse(result["ok"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["errors"][0]["error"], "TimeoutError")

    async def test_broken_context_binding_cannot_read_another_session(self):
        self.links.contexts[self.wx]["session_id"] = "wechat:FriendMessage:stranger"
        bridge = SimpleNamespace(compose_context=AsyncMock())
        cm = SimpleNamespace(get_curr_conversation_id=AsyncMock())
        result = await self.links.compose(self.qq, "昵称", bridge, cm)
        self.assertFalse(result["ok"])
        bridge.compose_context.assert_not_awaited()
        cm.get_curr_conversation_id.assert_not_awaited()

    async def test_placeholder_bot_id_keeps_the_previous_native_scope(self):
        self.links.observe(
            SimpleNamespace(
                unified_msg_origin=self.qq,
                is_private_chat=lambda: True,
                get_platform_name=lambda: "test-platform",
                get_sender_id=lambda: "verified-owner",
                get_self_id=lambda: "unknown_selfid",
            )
        )
        restored = OwnerMemory(self.links.path, [self.qq, self.wx])
        bridge = SimpleNamespace(
            compose_context=AsyncMock(
                side_effect=["new summary", "old core preference"]
            )
        )
        cm = SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value=None))
        result = await restored.compose(self.wx, "preference", bridge, cm)
        self.assertTrue(result["ok"])
        self.assertIn("old core preference", result["sources"][0]["memories"])
        self.assertIn("new summary", result["sources"][0]["memories"])
        contexts = [
            call.kwargs["session_context"]
            for call in bridge.compose_context.await_args_list
        ]
        self.assertEqual([ctx["bot_id"] for ctx in contexts], ["unknown_selfid", "qq"])
        self.assertTrue(all(ctx["session_id"] == self.qq for ctx in contexts))
        self.assertTrue(all(ctx["strict_session_only"] for ctx in contexts))
        self.assertTrue(all(ctx["user_id"] == "verified-owner" for ctx in contexts))

    def test_missing_self_id_does_not_overwrite_a_verified_identity(self):
        previous = self.links.contexts[self.qq].copy()
        self.links.observe(
            SimpleNamespace(
                unified_msg_origin=self.qq,
                is_private_chat=lambda: True,
                get_platform_name=lambda: "test-platform",
                get_sender_id=lambda: "verified-owner",
                get_self_id=lambda: None,
            )
        )
        self.assertEqual(self.links.contexts[self.qq], previous)

    def test_joke_saved_on_qq_is_visible_and_resolvable_on_wechat_after_restart(self):
        path = self.root / "dialogue.json"
        d = DialogueState(path, linked_sessions=[self.qq, self.wx])
        saved = d.update(
            self.qq,
            "以后叫这事周日特种兵",
            action="save",
            kind="shared_joke",
            title="周日特种兵",
            content="一个明确约定的虚构梗",
            evidence_quote="以后叫这事周日特种兵",
            now=100,
        )
        d = DialogueState(path, linked_sessions=[self.qq, self.wx])
        self.assertEqual(d.visible(self.wx, 101)[0]["id"], saved["id"])
        self.assertEqual(d.visible("qq:FriendMessage:stranger", 101), [])
        result = d.update(
            self.wx,
            "不再聊周日特种兵了",
            action="resolve",
            item_id=saved["id"],
            evidence_quote="不再聊周日特种兵了",
            now=102,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(d.visible(self.qq, 103), [])

    async def test_actual_prompt_hook_includes_other_channel_evidence(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        sys.path.insert(0, str(ROOT.parent))
        from astrbot_plugin_ada_companion.policy import DeliveryPolicy
        from astrbot_plugin_ada_companion.prompting import ContextBuilder

        plugin = object.__new__(ContextBuilder)
        plugin.owner_memory = self.links
        plugin.config = {"owner_sessions": [self.qq, self.wx]}
        plugin.target = self.wx
        plugin.policy = DeliveryPolicy(self.root / "policy.json", self.wx)
        plugin.services = SimpleNamespace(plugin=lambda name: None)
        plugin.dialogue = SimpleNamespace(visible=lambda origin: [])
        plugin.services.memory = lambda: SimpleNamespace(
            compose_context=AsyncMock(return_value="微信中明确约定称呼阿树")
        )
        plugin.context = SimpleNamespace(
            conversation_manager=SimpleNamespace(
                get_curr_conversation_id=AsyncMock(return_value=None)
            )
        )
        ContextBuilder.__init__(
            plugin,
            plugin.config,
            plugin.owner_memory,
            plugin.dialogue,
            plugin.policy,
            plugin.context,
            plugin.services,
        )
        from maintenance.tests.test_browser_url_messages import event as native_event

        event = native_event("qq", "你还记得怎么称呼我吗", "verified-owner")
        request = SimpleNamespace(system_prompt="Ada persona")
        await plugin.prepare(event, request)
        self.assertIn("微信中明确约定称呼阿树", request.system_prompt)
        self.assertTrue(request.system_prompt.startswith("Ada persona"))

    async def test_personal_qq_gate_only_allows_the_confirmed_private_owner(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        sys.path.insert(0, str(ROOT.parent))
        from astrbot_plugin_ada_companion.tool_policy import ToolPolicy

        plugin = object.__new__(ToolPolicy)
        origin = "qq-personal-companion:FriendMessage:verified-owner"
        plugin.owner_memory = SimpleNamespace(owners=(origin,))
        for candidate, private, blocked in (
            (origin, True, False),
            ("qq-personal-companion:FriendMessage:stranger", True, True),
            ("qq-personal-companion:GroupMessage:any", False, True),
        ):
            stop = Mock()
            event = SimpleNamespace(
                unified_msg_origin=candidate,
                is_private_chat=lambda: private,
                stop_event=stop,
            )
            await plugin.personal_qq_access(event)
            self.assertEqual(stop.called, blocked)


if __name__ == "__main__":
    unittest.main()
