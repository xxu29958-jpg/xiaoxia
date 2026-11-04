import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_ada_companion.policy import (
    LOCAL_TZ,
    DeliveryPolicy,
    speech_text,
    wants_voice,
)

from maintenance.tests.working_state_support import native_dialogue, save

TARGET = "wechat-companion:FriendMessage:test-owner"
NOW = datetime(2026, 10, 18, 12, tzinfo=LOCAL_TZ).timestamp()


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.policy = DeliveryPolicy(self.path, TARGET, NOW - 4 * 3600)

    def test_goodnight_defers_until_morning_but_new_message_reopens(self):
        self.policy.on_user(TARGET, NOW, "我先去睡了，晚安")
        restored = DeliveryPolicy(self.path, TARGET)
        self.assertEqual(restored.gate(NOW + 4 * 3600), "conversation_closed")
        restored.on_user(TARGET, NOW + 4 * 3600, "我又回来了")
        self.assertEqual(restored.gate(NOW + 5 * 3600), "ready")

    def test_sleep_quotation_or_negation_does_not_close_chat(self):
        for text in ("他说晚安之后又回来了", "晚安？我还不睡", "我不睡觉了"):
            self.policy.on_user(TARGET, NOW, text)
            self.assertEqual(self.policy.data["closing_until"], 0)

    def test_quiet_boundaries(self):
        for hour, ready in ((8, False), (9, True), (22, True), (23, False)):
            at = datetime(2026, 10, 18, hour, tzinfo=LOCAL_TZ).timestamp()
            self.assertEqual(self.policy.gate(at, manual=True) == "ready", ready)

    def test_uncertain_delivery_survives_restart(self):
        self.policy.reserve("Hey, a quick question.", NOW)
        restored = DeliveryPolicy(self.path, TARGET, NOW)
        self.assertEqual(restored.daily_count(NOW), 1)
        self.assertEqual(restored.data["unanswered"], 1)
        self.assertEqual(restored.gate(NOW + 100), "cooldown")

    def test_two_unanswered_allow_a_next_day_checkin_and_owner_reply(self):
        self.policy.reserve("Hey, a quick question.", NOW)
        self.policy.reserve(
            "The imaginary committee has rejected Mondays.", NOW + 4 * 3600
        )
        self.assertEqual(self.policy.gate(NOW + 8 * 3600), "awaiting_reply")
        restored = DeliveryPolicy(self.path, TARGET)
        self.assertEqual(restored.gate(NOW + 28 * 3600), "ready")
        restored.reserve("A fresh thought the next afternoon.", NOW + 28 * 3600)
        self.assertEqual(restored.gate(NOW + 29 * 3600), "awaiting_reply")
        self.policy.on_user("qq-companion:FriendMessage:test-owner", NOW + 8 * 3600)
        self.assertEqual(self.policy.data["unanswered"], 2)
        self.policy.on_user(TARGET, NOW + 8 * 3600)
        self.assertEqual(self.policy.data["unanswered"], 0)
        self.assertEqual(self.policy.gate(NOW + 8 * 3600), "recent_conversation")

    def test_daily_cap_and_new_day(self):
        for index in range(4):
            self.policy.data["unanswered"] = 0
            self.policy.reserve(
                f"Unique thought {index}: " + "abcd"[index] * 35,
                NOW + index * 3600,
                manual=True,
            )
        self.assertEqual(self.policy.gate(NOW + 5 * 3600), "daily_limit")
        self.policy.data["unanswered"] = 0
        self.assertEqual(self.policy.gate(NOW + 86400), "ready")

    def test_cooldown_idle_and_manual(self):
        self.policy.on_user(TARGET, NOW)
        self.assertEqual(self.policy.gate(NOW + 100), "recent_conversation")
        self.assertEqual(self.policy.gate(NOW, manual=True), "ready")
        self.policy.reserve("A real delivered message.", NOW, manual=True)
        self.assertEqual(self.policy.gate(NOW + 5, manual=True), "manual_cooldown")

    def test_qq_cannot_be_proactive_target(self):
        other = DeliveryPolicy(
            Path(self.temp.name) / "qq.json",
            "qq-companion:FriendMessage:test-owner",
            NOW - 4 * 3600,
        )
        self.assertEqual(other.gate(NOW), "invalid_target")

    def test_corrupt_state_fails_closed(self):
        self.path.write_text("corrupted", encoding="utf-8")
        restored = DeliveryPolicy(self.path, TARGET, NOW)
        self.assertEqual(restored.gate(NOW), "state_unreadable")
        with self.assertRaises(RuntimeError):
            restored.save()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "corrupted")

    def test_duplicate_guard(self):
        self.policy.reserve("Wait. Is cereal a soup?", NOW)
        self.assertTrue(self.policy.duplicate("wait, is cereal a soup!"))
        self.assertFalse(self.policy.duplicate("Hope that interview went okay."))

    def test_explicit_voice_and_negation(self):
        for text in (
            "用语音说你好",
            "用语言说一句晚安",
            "想听听你的声音",
            "请帮我读出来",
            "发条语音",
            "voice message please",
            "say hello out loud",
        ):
            self.assertTrue(wants_voice(text), text)
        for text in (
            "不要语音",
            "不要用语言说，打字就好",
            "只发文字",
            "别发语音",
            "no voice",
            "don't send a voice message",
            "text only",
        ):
            self.assertFalse(wants_voice(text, enabled=True), text)
        self.assertFalse(wants_voice("你好"))
        for text in (
            "用英语说晚安",
            "什么编程语言",
            "用语言表达情感",
            "我想听你慢慢讲一个关于旧书店的想法，展开说说。",
            "听听你怎么想",
        ):
            self.assertFalse(wants_voice(text), text)

    def test_speech_does_not_read_markup(self):
        self.assertEqual(
            speech_text("**Hey** [you](http://example.com)\n&&happy&&\n```hidden```"),
            "Hey you",
        )


class ProactiveIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from astrbot_plugin_ada_companion.contact_channels import ContactChannels
        from astrbot_plugin_ada_companion.initiative import ProactiveChat
        from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plugin = object.__new__(ProactiveChat)
        self.plugin.config = {
            "proactive_enabled": True,
            "provider_id": "chatgpt-companion",
        }
        self.plugin.policy = DeliveryPolicy(
            Path(self.temp.name) / "state.json", TARGET, NOW - 4 * 3600
        )
        self.plugin.proactive_lock = asyncio.Lock()
        self.plugin.last_check = {}
        conv = SimpleNamespace(
            history=json.dumps([{"role": "user", "content": "Use English"}]),
            persona_id="Ada",
        )
        cm = SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="conversation"),
            get_conversation=AsyncMock(return_value=conv),
            update_conversation=AsyncMock(),
        )
        self.context = SimpleNamespace(
            get_platform_inst=lambda name: SimpleNamespace(
                status=SimpleNamespace(value="running"),
                meta=lambda: SimpleNamespace(name="weixin_oc"),
            ),
            conversation_manager=cm,
            persona_manager=SimpleNamespace(
                get_persona=AsyncMock(
                    return_value=SimpleNamespace(system_prompt="Ada is an AI.")
                )
            ),
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(
                    completion_text='{"send":true,"text":"A tiny thought: cereal has an alarming amount in common with soup."}'
                )
            ),
            send_message=AsyncMock(return_value=True),
        )
        self.plugin.context = self.context
        self.bridge = SimpleNamespace(
            compose_injection=AsyncMock(return_value="Verified memory only"),
            record_visible_turn=AsyncMock(return_value="evt-real"),
        )
        self.plugin.services = SimpleNamespace(
            memory=lambda: self.bridge, plugin=lambda _: None
        )
        self.plugin.owner_memory = OwnerMemory(
            Path(self.temp.name) / "owners.json", [TARGET]
        )
        self.plugin.owner_memory.contexts[TARGET] = {
            "session_id": TARGET,
            "scope": "private",
            "platform": "weixin_oc",
            "user_id": "test-owner",
            "bot_id": "wechat-bot",
        }
        self.plugin.owner_memory.compose = AsyncMock(return_value={})
        self.plugin.dialogue = native_dialogue(
            Path(self.temp.name) / "dialogue.json",
            self.addCleanup,
            self.plugin.owner_memory,
            self.bridge,
        )
        self.plugin.contacts = ContactChannels(
            self.plugin.policy, self.plugin.owner_memory, self.context
        )
        ProactiveChat.__init__(
            self.plugin,
            self.plugin.config,
            self.plugin.owner_memory,
            self.plugin.dialogue,
            self.plugin.policy,
            self.context,
            self.plugin.services,
            self.plugin.contacts,
        )
        self.clock = patch(
            "astrbot_plugin_ada_companion.initiative.time.time", return_value=NOW
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def test_success_records_actual_assistant_turn(self):
        result = await self.plugin.proactive()
        self.assertEqual(result["reason"], "sent")
        call = self.context.send_message.await_args
        self.assertEqual(call.args[0], TARGET)
        history = (
            self.context.conversation_manager.update_conversation.await_args.kwargs[
                "history"
            ]
        )
        self.assertEqual([x["role"] for x in history], ["user", "assistant"])
        self.assertEqual(history[-1]["content"], call.args[1].chain[0].text)
        self.assertEqual(
            self.bridge.record_visible_turn.await_args.kwargs["content"],
            history[-1]["content"],
        )
        self.assertEqual(
            self.plugin.policy.data["deliveries"][0]["memory_id"], "evt-real"
        )

    async def test_send_timeout_is_not_retried_or_remembered_as_sent(self):
        self.context.send_message.side_effect = TimeoutError
        self.assertEqual(
            (await self.plugin.proactive())["reason"], "delivery_uncertain"
        )
        self.assertEqual((await self.plugin.proactive())["reason"], "cooldown")
        self.context.send_message.assert_awaited_once()
        self.bridge.record_visible_turn.assert_not_awaited()
        self.context.conversation_manager.update_conversation.assert_not_awaited()

    async def test_reply_during_generation_discards_stale_checkin(self):
        async def generated(**kwargs):
            self.plugin.policy.on_user(TARGET, NOW + 1)
            return SimpleNamespace(
                completion_text='{"send":true,"text":"Old stale thought."}'
            )

        self.context.llm_generate.side_effect = generated
        self.assertEqual((await self.plugin.proactive())["reason"], "context_changed")
        self.context.send_message.assert_not_awaited()

    async def test_quiet_hours_never_call_model(self):
        self.clock.stop()
        with patch(
            "astrbot_plugin_ada_companion.initiative.time.time",
            return_value=NOW + 11 * 3600,
        ):
            self.assertEqual((await self.plugin.proactive())["reason"], "quiet_hours")
        self.context.llm_generate.assert_not_awaited()

    async def test_model_skip_is_not_a_message_or_quota(self):
        self.context.llm_generate.return_value.completion_text = (
            '{"send":false,"text":""}'
        )
        self.assertEqual((await self.plugin.proactive())["reason"], "skipped")
        self.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.policy.daily_count(NOW), 0)

    async def test_preview_does_not_send_or_consume_quota(self):
        before = self.plugin.policy.path.read_bytes()
        self.assertEqual(
            (await self.plugin.proactive(preview=True))["reason"], "preview"
        )
        self.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.policy.path.read_bytes(), before)

    async def test_preview_rejects_invalid_candidate_like_real_delivery(self):
        for candidate in ({"bad": "object"}, "长" * 200, "hello &&happy&&"):
            self.context.llm_generate.return_value.completion_text = json.dumps(
                {"send": True, "text": candidate}
            )
            result = await self.plugin.proactive(preview=True)
            self.assertFalse(result["send"])
            self.assertEqual(result["text"], "")
        self.context.send_message.assert_not_awaited()

    async def test_turning_off_config_during_generation_prevents_sending(self):
        async def generated(**kwargs):
            self.plugin.config["proactive_enabled"] = False
            return SimpleNamespace(
                completion_text='{"send":true,"text":"A stale thought."}'
            )

        self.context.llm_generate.side_effect = generated
        self.assertEqual((await self.plugin.proactive())["reason"], "context_changed")
        self.context.send_message.assert_not_awaited()

    async def test_proactive_cannot_use_resolved_or_invented_thread(self):
        result = await save(
            self.plugin.dialogue,
            TARGET,
            "下次聊春游",
            action="save",
            title="春游",
            content="下次聊春游",
            evidence_quote="下次聊春游",
            now=NOW,
        )
        await save(
            self.plugin.dialogue,
            TARGET,
            "春游取消了，别再聊了",
            action="resolve",
            item_id=result["id"],
            evidence_quote="春游取消了，别再聊了",
            now=NOW,
        )
        self.context.llm_generate.return_value.completion_text = json.dumps(
            {"send": True, "text": "春游准备得怎么样？", "thread_id": result["id"]}
        )
        self.assertFalse((await self.plugin.proactive(preview=True))["send"])
        self.context.send_message.assert_not_awaited()

    async def test_same_id_edited_during_candidate_generation_cannot_be_sent(self):
        saved = await save(
            self.plugin.dialogue,
            TARGET,
            "下次聊春游",
            action="save",
            title="春游",
            content="一起讨论春游",
            evidence_quote="下次聊春游",
            now=NOW,
        )

        async def generated(**kwargs):
            self.assertIn(saved["version"], kwargs["prompt"])
            changed = await save(
                self.plugin.dialogue,
                TARGET,
                "春游改去公园",
                action="save",
                item_id=saved["id"],
                title="春游",
                content="改去公园",
                evidence_quote="春游改去公园",
                now=NOW,
            )
            self.assertTrue(changed["ok"], changed)
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "send": True,
                        "text": "春游路线准备好了吗？",
                        "thread_id": saved["id"],
                    }
                )
            )

        self.context.llm_generate.side_effect = generated
        self.assertEqual((await self.plugin.proactive())["reason"], "context_changed")
        self.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.policy.data["deliveries"], [])


class MobileControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from astrbot_plugin_ada_companion.main import AdaCompanion

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.qq = "qq-companion:FriendMessage:test-owner"
        config = {
            "owner_sessions": [TARGET, self.qq],
            "wechat_target": TARGET,
            "proactive_enabled": True,
            "voice_enabled": False,
        }
        context: Any = SimpleNamespace(
            get_provider_by_id=lambda _: object(),
            get_all_stars=lambda: [],
            conversation_manager=SimpleNamespace(
                get_curr_conversation_id=AsyncMock(return_value=None)
            ),
        )
        with patch(
            "astrbot_plugin_ada_companion.main.StarTools.get_data_dir",
            return_value=Path(self.temp.name),
        ):
            self.plugin = AdaCompanion(context, config)
        self.plugin.policy.data.update(last_user=NOW, next_due=NOW)
        self.event: Any = SimpleNamespace(
            is_private_chat=lambda: True,
            unified_msg_origin=self.qq,
            get_message_str=Mock(return_value="暂停主动消息"),
            get_platform_name=lambda: "qq_official",
            get_sender_id=lambda: "test-owner",
            get_self_id=lambda: "bot",
            set_extra=Mock(),
            get_extra=lambda key, default=None: default,
            plain_result=lambda text: text,
            send=AsyncMock(),
            stop_event=Mock(),
        )

    async def test_pause_is_persisted_and_llm_gets_current_state(self):
        await self.plugin.receive(self.event)
        restored = DeliveryPolicy(self.plugin.policy.path, TARGET)
        self.assertFalse(restored.data["enabled"])
        req = SimpleNamespace(system_prompt="Ada")
        self.event.get_message_str.return_value = "你现在还会主动发消息吗？"
        await self.plugin.prepare(self.event, req)
        self.assertIn('"proactive_enabled": false', req.system_prompt)
        self.event.stop_event.assert_called_once()

    async def test_explicit_qq_resume_clears_unanswered_pause(self):
        self.plugin.policy.data.update(enabled=False, unanswered=2)
        self.event.get_message_str.return_value = "恢复主动聊天"
        await self.plugin.receive(self.event)
        restored = DeliveryPolicy(self.plugin.policy.path, TARGET)
        self.assertTrue(restored.data["enabled"])
        self.assertEqual(restored.data["unanswered"], 0)
        self.assertGreater(restored.data["next_due"], NOW)

    async def test_global_switch_off_is_not_reported_as_resumed(self):
        self.plugin.config["proactive_enabled"] = False
        self.plugin.policy.data["enabled"] = False
        self.event.get_message_str.return_value = "恢复主动聊天"
        await self.plugin.receive(self.event)
        self.assertIn("还没有恢复", self.event.send.await_args.args[0])
        status = (await self.plugin.admin.status())["data"]
        self.assertFalse(status["enabled"])
        self.assertEqual(status["gate"], "config_disabled")

    async def test_unavailable_voice_is_not_claimed_as_working(self):
        self.event.get_message_str.return_value = "开启语音回复"
        await self.plugin.receive(self.event)
        self.assertIn("还未开通", self.event.send.await_args.args[0])
        self.assertTrue(self.plugin.policy.data["voice_sessions"]["owner"])

    async def test_stranger_cannot_pause_owner(self):
        self.event.unified_msg_origin = "qq-companion:FriendMessage:stranger"
        before = self.plugin.policy.path.read_bytes()
        await self.plugin.receive(self.event)
        self.assertEqual(self.plugin.policy.path.read_bytes(), before)
        self.event.send.assert_not_awaited()

    async def test_help_is_local_and_mentions_current_voice_limit(self):
        self.event.get_message_str.return_value = "Ada帮助"
        await self.plugin.receive(self.event)
        text = self.event.send.await_args.args[0]
        for feature in ("记忆", "表情", "主动聊天", "还未开通"):
            self.assertIn(feature, text)
        self.event.stop_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
