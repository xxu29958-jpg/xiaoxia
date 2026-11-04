"""Exercise phone commands through persistent routing and the actual sender."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))

from astrbot_plugin_ada_companion.main import AdaCompanion
from astrbot_plugin_ada_companion.policy import LOCAL_TZ

from maintenance.tests.working_state_support import native_dialogue

WX = "wechat-companion:FriendMessage:owner-wx"
QQ = "qq-personal-companion:FriendMessage:12345678"
OLD_QQ = "qq-companion:FriendMessage:retired-owner"
NOW = datetime(2026, 10, 19, 12, tzinfo=LOCAL_TZ).timestamp()


class ContactChannelTests(unittest.IsolatedAsyncioTestCase):
    def _callCleanup(self, function, /, *args, **kwargs):
        """Run sync cleanups even when a test drives setUp without the asyncio runner.

        Other suites construct this case directly and call setUp/doCleanups. The native
        IsolatedAsyncioTestCase implementation asserts on a runner that manual use never
        starts, which would silently keep the patched clock active across suites.
        """
        function(*args, **kwargs)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.clock = patch("time.time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.config = {
            "owner_sessions": [OLD_QQ, WX, QQ],
            "wechat_target": WX,
            "proactive_enabled": True,
        }
        self.platforms: dict[str, Any] = {
            origin.split(":", 1)[0]: SimpleNamespace(
                status=SimpleNamespace(value="running"),
                meta=lambda kind=kind: SimpleNamespace(name=kind),
            )
            for origin, kind in ((WX, "weixin_oc"), (QQ, "aiocqhttp"))
        }
        self.histories = {WX: [], QQ: []}

        async def get_conversation(origin, cid):
            return SimpleNamespace(
                history=json.dumps(self.histories[origin]),
                persona_id="Ada",
                updated_at=NOW,
            )

        async def update_conversation(origin, cid, history):
            self.histories[origin] = history

        self.context: Any = SimpleNamespace(
            get_provider_by_id=lambda pid: (
                SimpleNamespace() if pid == "chatgpt-companion" else None
            ),
            get_all_stars=lambda: [],
            get_platform_inst=lambda name: self.platforms.get(name),
            conversation_manager=SimpleNamespace(
                get_curr_conversation_id=AsyncMock(return_value="conversation"),
                get_conversation=get_conversation,
                update_conversation=update_conversation,
            ),
            persona_manager=SimpleNamespace(
                get_persona=AsyncMock(return_value=SimpleNamespace(system_prompt="Ada"))
            ),
            llm_generate=AsyncMock(
                return_value=SimpleNamespace(
                    role="assistant",
                    completion_text='{"send":true,"text":"突然想起那个把周一取消的提案，我投赞成票。"}',
                )
            ),
            send_message=AsyncMock(return_value=True),
        )
        self.bridge = SimpleNamespace(
            compose_injection=AsyncMock(return_value=""),
            compose_context=AsyncMock(return_value=""),
            record_visible_turn=AsyncMock(return_value="recorded-turn"),
        )
        self.plugin = self.load_plugin()
        for origin in (WX, QQ, OLD_QQ):
            self.plugin.owner_memory.observe(self.event(origin))
        native_dialogue(
            self.folder / "dialogue.json",
            self.addCleanup,
            self.plugin.owner_memory,
            self.bridge,
        )

    def load_plugin(self):
        with patch(
            "astrbot_plugin_ada_companion.main.StarTools.get_data_dir",
            return_value=self.folder,
        ):
            plugin = AdaCompanion(self.context, self.config)
        plugin.services.memory = lambda: self.bridge
        return plugin

    def event(self, origin=WX, text="hello", private=True) -> Any:
        return SimpleNamespace(
            unified_msg_origin=origin,
            is_private_chat=lambda: private,
            get_message_str=lambda: text,
            get_sender_id=lambda: origin.split(":", 2)[-1],
            get_self_id=lambda: "personal-bot" if origin == QQ else "wechat-bot",
            get_platform_name=lambda: "aiocqhttp" if origin == QQ else "weixin_oc",
            send=AsyncMock(),
            plain_result=lambda text: text,
            stop_event=Mock(),
            set_extra=Mock(),
            get_extra=lambda key, default=None: default,
        )

    async def test_phone_add_survives_restart_and_delivers_to_personal_qq(self):
        command = self.event(text="以后你也来QQ找我吧")
        await self.plugin.receive(command)
        command.stop_event.assert_called_once()
        self.assertIn("QQ", command.send.await_args.args[0])
        self.plugin = self.load_plugin()
        from astrbot.core.platform.platform import PlatformStatus
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
            AiocqhttpAdapter,
        )
        from astrbot.core.star.context import Context

        # Keep AstrBot's session routing, QQ adapter and OneBot encoding real;
        # replace only the network boundary, so this cannot message a phone.
        adapter: Any = object.__new__(AiocqhttpAdapter)
        adapter._status = PlatformStatus.RUNNING
        adapter.meta = lambda: SimpleNamespace(
            id="qq-personal-companion", name="aiocqhttp"
        )
        adapter.bot = SimpleNamespace(send_private_msg=AsyncMock())
        self.platforms["qq-personal-companion"] = adapter
        self.context.platform_manager = SimpleNamespace(platform_insts=[adapter])
        self.context.get_config = lambda **kwargs: {}
        self.context.send_message = MethodType(Context.send_message, self.context)
        with patch(
            "astrbot.core.platform.platform.Metric.upload", new_callable=AsyncMock
        ):
            result = await self.plugin.initiative.proactive(manual=True)
        self.assertEqual(result["reason"], "sent")
        payload = adapter.bot.send_private_msg.await_args.kwargs
        self.assertEqual(payload["user_id"], 12345678)
        self.assertEqual(
            payload["message"],
            [{"type": "text", "data": {"text": self.histories[QQ][-1]["content"]}}],
        )
        self.assertEqual(self.histories[WX], [])
        memory = self.bridge.record_visible_turn.await_args.kwargs
        self.assertEqual(
            (memory["platform"], memory["user_id"]), ("aiocqhttp", "12345678")
        )
        self.assertEqual(memory["metadata"]["bot_id"], "personal-bot")
        self.assertEqual(self.plugin.policy.data["deliveries"][-1]["target"], QQ)

    async def test_only_and_remove_are_shared_without_resetting_delivery_history(self):
        self.plugin.policy.data["deliveries"] = [
            {"at": NOW - 3600, "text": "Previously sent", "status": "sent"}
        ]
        for origin, command, expected in (
            (WX, "QQ也找我", ["wechat", "qq"]),
            (QQ, "以后只在QQ找我", ["qq"]),
            (QQ, "微信也可以主动找我", ["qq", "wechat"]),
            (WX, "别在QQ主动找我了", ["wechat"]),
        ):
            await self.plugin.receive(self.event(origin, command))
            self.plugin = self.load_plugin()
            self.assertEqual(self.plugin.policy.data["channels"], expected)
            self.assertEqual(self.plugin.policy.daily_count(NOW), 1)
        req = SimpleNamespace(system_prompt="")
        await self.plugin.prepare(self.event(QQ), req)
        self.assertIn('"proactive_channels": ["wechat"]', req.system_prompt)

    async def test_quotes_negation_strangers_and_groups_cannot_enable_qq(self):
        for event in (
            self.event(text="他说“QQ也找我”，什么意思？"),
            self.event(text="不是让你QQ也找我"),
            self.event(text="如果以后让你QQ也找我会怎样"),
            self.event(text="QQ也找我", private=False),
            self.event("wechat-companion:FriendMessage:stranger", "QQ也找我"),
        ):
            await self.plugin.receive(event)
            self.assertEqual(self.plugin.policy.data["channels"], ["wechat"])

    async def test_missing_or_retired_qq_cannot_be_enabled(self):
        self.platforms.pop("qq-personal-companion")
        command = self.event(text="QQ也找我")
        await self.plugin.receive(command)
        self.assertEqual(self.plugin.policy.data["channels"], ["wechat"])
        self.assertIn("没有", command.send.await_args.args[0])

    async def test_failed_preference_write_is_not_applied_or_reported_successful(self):
        command = self.event(text="QQ也找我")
        before = self.plugin.policy.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            await self.plugin.receive(command)
        self.assertEqual(self.plugin.policy.data["channels"], ["wechat"])
        self.assertEqual(self.plugin.policy.path.read_bytes(), before)
        self.assertIn("没能保存", command.send.await_args.args[0])

    async def test_channel_removed_during_generation_cannot_receive_stale_message(self):
        await self.plugin.receive(self.event(text="QQ也找我"))

        async def generating(**kwargs):
            await self.plugin.receive(self.event(QQ, "以后只在微信找我"))
            return SimpleNamespace(
                role="assistant",
                completion_text='{"send":true,"text":"A stale message"}',
            )

        self.context.llm_generate.side_effect = generating
        result = await self.plugin.initiative.proactive(manual=True)
        self.assertEqual(result["reason"], "context_changed")
        self.context.send_message.assert_not_awaited()
        self.assertEqual(self.plugin.policy.daily_count(NOW), 0)

    async def test_shared_quota_and_uncertain_send_never_fan_out_to_wechat(self):
        await self.plugin.receive(self.event(text="QQ也找我"))
        self.context.send_message.side_effect = TimeoutError
        first = await self.plugin.initiative.proactive(manual=True)
        self.assertEqual(first["reason"], "delivery_uncertain")
        self.plugin = self.load_plugin()
        second = await self.plugin.initiative.proactive(manual=True)
        self.assertEqual(second["reason"], "manual_cooldown")
        self.assertEqual(self.context.send_message.await_args.args[0], QQ)
        self.context.send_message.assert_awaited_once()
        self.assertEqual(self.plugin.policy.daily_count(NOW), 1)
        self.bridge.record_visible_turn.assert_not_awaited()

    async def test_rotation_and_total_cap_are_shared_across_channels(self):
        await self.plugin.receive(self.event(text="QQ也找我"))
        for index, target in enumerate((QQ, WX, QQ, WX)):
            with patch("time.time", return_value=NOW + index * 600):
                await self.plugin.receive(self.event(target, "刚才那条收到了"))
                self.context.llm_generate.return_value.completion_text = json.dumps(
                    {"send": True, "text": "不同的想法：" + "甲乙丙丁"[index] * 20}
                )
                result = await self.plugin.initiative.proactive(manual=True)
                self.assertEqual(result["reason"], "sent")
                self.assertEqual(self.context.send_message.await_args.args[0], target)
        self.plugin = self.load_plugin()
        with patch("time.time", return_value=NOW + 3600):
            self.assertEqual(
                (await self.plugin.initiative.proactive(manual=True))["reason"],
                "daily_limit",
            )
        self.assertEqual(self.context.send_message.await_count, 4)

    async def test_free_form_tool_uses_current_evidence_and_shared_state(self):
        command = "以后QQ也可以主动给我发点消息，微信照旧就行"
        for wrong_action in ("set", "remove"):
            rejected = json.loads(
                await self.plugin.contact_channels(
                    self.event(text=command),
                    action=wrong_action,
                    channel="qq",
                    evidence_quote=command,
                )
            )
            self.assertFalse(rejected["ok"])
        result = json.loads(
            await self.plugin.contact_channels(
                self.event(text=command),
                action="add",
                channel="qq",
                evidence_quote=command,
            )
        )
        self.assertTrue(result["ok"])
        self.plugin = self.load_plugin()
        status = json.loads(await self.plugin.contact_channels(self.event(QQ)))
        self.assertEqual(status["channels"], ["wechat", "qq"])
        before = self.plugin.policy.path.read_bytes()
        for source, quote in (
            (self.event(text="你好"), command),
            (self.event(text="他说“微信也找我”"), "他说“微信也找我”"),
            (self.event(text="不要在微信找我"), "不要在微信找我"),
            (self.event("wechat-companion:FriendMessage:stranger", command), command),
        ):
            denied = json.loads(
                await self.plugin.contact_channels(
                    source, action="set", channel="wechat", evidence_quote=quote
                )
            )
            self.assertFalse(denied["ok"])
            self.assertEqual(self.plugin.policy.path.read_bytes(), before)

    async def test_disabled_or_offline_delivery_keeps_preference_without_false_promise(
        self,
    ):
        self.config["proactive_enabled"] = False
        command = self.event(text="QQ也找我")
        await self.plugin.receive(command)
        self.assertIn("总开关", command.send.await_args.args[0])
        self.assertEqual(
            (await self.plugin.initiative.proactive(manual=True))["reason"],
            "config_disabled",
        )
        self.config["proactive_enabled"] = True
        self.platforms["qq-personal-companion"].status.value = "error"
        command = self.event(text="以后只在QQ找我")
        await self.plugin.receive(command)
        self.assertIn("掉线", command.send.await_args.args[0])
        self.assertEqual(
            (await self.plugin.initiative.proactive(manual=True))["reason"],
            "channel_unavailable",
        )
        self.context.send_message.assert_not_awaited()
        self.context.llm_generate.assert_not_awaited()
        self.assertEqual(self.plugin.policy.daily_count(NOW), 0)


if __name__ == "__main__":
    unittest.main()
