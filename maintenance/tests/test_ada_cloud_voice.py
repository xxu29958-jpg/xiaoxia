import asyncio
import contextlib
import io
import math
import sqlite3
import struct
import sys
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_ada_companion.cloud_voice import CloudVoice, VoiceError
from astrbot_plugin_ada_companion.policy import LOCAL_TZ, DeliveryPolicy


class CloudVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = {
            "cloud_api_key": "fake-test-key-not-a-credential",
            "cloud_workspace_id": "test-workspace",
            "cloud_voice": "longanrou_v3",
            "voice_monthly_limit": 100,
        }
        self.voice = CloudVoice(self.folder, self.config)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(
                b"".join(
                    struct.pack(
                        "<h", int(3000 * math.sin(i * 440 * 2 * math.pi / 24000))
                    )
                    for i in range(24000)
                )
            )
        self.audio = buffer.getvalue()
        self.response = MagicMock()
        self.response.status = 200
        self.response.json = AsyncMock(
            return_value={
                "output": {
                    "finish_reason": "stop",
                    "audio": {
                        "url": "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/test.wav?signature=test"
                    },
                }
            }
        )
        self.response.__aenter__ = AsyncMock(return_value=self.response)
        self.download = MagicMock()
        self.download.status = 200
        self.download.__aenter__ = AsyncMock(return_value=self.download)

        async def chunks(_size):
            yield self.audio

        self.download.content.iter_chunked.side_effect = chunks
        self.client = MagicMock()
        self.client.post.return_value = self.response
        self.client.get.return_value = self.download
        self.client.__aenter__ = AsyncMock(return_value=self.client)
        self.network = patch(
            "astrbot_plugin_ada_companion.cloud_voice.aiohttp.ClientSession",
            return_value=self.client,
        )
        self.network.start()
        self.addCleanup(self.network.stop)

    async def test_protocol_audio_validation_and_key_is_not_forwarded(self):
        self.assertEqual(await self.voice.synthesize("晚安。"), self.audio)
        args, kwargs = self.client.post.call_args
        self.assertTrue(args[0].startswith("https://test-workspace.cn-beijing."))
        self.assertEqual(kwargs["json"]["model"], "cosyvoice-v3-flash")
        self.assertEqual(kwargs["json"]["input"]["voice"], "longanrou_v3")
        self.assertNotIn("instruction", kwargs["json"]["input"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("headers", self.client.get.call_args.kwargs)
        self.assertTrue(self.client.get.call_args.args[0].startswith("https://"))
        self.assertEqual(self.voice.status()["reserved_characters"], 3)
        self.assertNotIn(self.config["cloud_api_key"], str(self.voice.status()))

    async def test_missing_credentials_never_reserve_or_call_network(self):
        self.config["cloud_api_key"] = ""
        with self.assertRaisesRegex(VoiceError, "not_configured"):
            await self.voice.synthesize("晚安")
        self.client.post.assert_not_called()
        self.assertEqual(self.voice.status()["reserved_characters"], 0)

    async def test_invalid_workspace_cannot_change_destination(self):
        self.config["cloud_workspace_id"] = "example.com/path"
        with self.assertRaisesRegex(VoiceError, "invalid_workspace"):
            await self.voice.synthesize("晚安")
        self.client.post.assert_not_called()

    async def test_failure_reservation_survives_restart_and_blocks_over_budget(self):
        self.config["voice_monthly_limit"] = 5
        self.client.post.side_effect = TimeoutError
        with self.assertRaisesRegex(VoiceError, "network_error"):
            await self.voice.synthesize("晚安好梦")
        restored = CloudVoice(self.folder, self.config)
        self.assertEqual(restored.status()["reserved_characters"], 4)
        with self.assertRaisesRegex(VoiceError, "monthly_limit"):
            await restored.synthesize("晚安")
        self.client.post.assert_called_once()

    async def test_provider_auth_error_is_sanitized_and_never_retried(self):
        self.response.status = 401
        with self.assertRaisesRegex(VoiceError, "^provider_http_401$"):
            await self.voice.synthesize("你好")
        self.client.post.assert_called_once()
        self.client.get.assert_not_called()
        self.response.json.assert_not_awaited()

    async def test_allowance_is_shared_across_two_instances(self):
        self.config["voice_monthly_limit"] = 3
        other = CloudVoice(self.folder, self.config)
        outcomes = await asyncio.gather(
            self.voice.synthesize("晚安"),
            other.synthesize("你好"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(x, bytes) for x in outcomes), 1)
        self.assertEqual(sum(isinstance(x, VoiceError) for x in outcomes), 1)
        self.client.post.assert_called_once()

    async def test_new_month_has_new_allowance_without_erasing_old_records(self):
        self.config["voice_monthly_limit"] = 2
        await self.voice.synthesize("晚安")
        with patch("astrbot_plugin_ada_companion.cloud_voice.datetime") as clock:
            clock.now.return_value = datetime(2030, 1, 1, tzinfo=LOCAL_TZ)
            await self.voice.synthesize("你好")
            self.assertEqual(self.voice.status()["reserved_characters"], 2)
        with contextlib.closing(sqlite3.connect(self.voice.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usage").fetchone()[0], 2)

    async def test_external_audio_url_is_not_fetched(self):
        self.response.json.return_value["output"]["audio"]["url"] = (
            "http://127.0.0.1/private"
        )
        with self.assertRaisesRegex(VoiceError, "invalid_audio_url"):
            await self.voice.synthesize("晚安")
        self.client.get.assert_not_called()

    async def test_bad_audio_does_not_become_a_voice_message(self):
        self.audio = b"<html>not audio</html>"
        with self.assertRaises(VoiceError):
            await self.voice.synthesize("晚安")

    async def test_corrupted_usage_database_fails_closed(self):
        self.voice.path.write_bytes(b"broken usage database")
        broken = CloudVoice(self.folder, self.config)
        with self.assertRaisesRegex(VoiceError, "usage_unavailable"):
            await broken.synthesize("晚安")
        self.client.post.assert_not_called()

    async def test_phone_reply_components_keep_text_and_attach_the_right_audio(self):
        from astrbot_plugin_ada_companion.response_media import ResponseMedia

        from astrbot.api.message_components import File, Plain, Record

        plugin = ResponseMedia({"voice_enabled": True}, self.folder, self.voice)
        for platform, component in (("qq_official", Record), ("weixin_oc", File)):
            with self.subTest(platform=platform):
                extra = {"ada_voice_requested": True}
                result = SimpleNamespace(chain=[Plain("晚安，好梦。")])
                event = SimpleNamespace(
                    get_extra=extra.get,
                    set_extra=extra.__setitem__,
                    get_result=lambda result=result: result,
                    get_platform_name=lambda platform=platform: platform,
                )
                await plugin.decorate(event)
                self.assertIsInstance(result.chain[0], Plain)
                self.assertIsInstance(result.chain[-1], component)
                path = Path(result.chain[-1].file)
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 1000)
                self.assertEqual(
                    path.suffix, ".mp3" if platform == "weixin_oc" else ".wav"
                )
                self.assertEqual(plugin.last_voice["state"], "attached_to_reply")
                await plugin.decorate(event)
                self.assertEqual(len(result.chain), 2)

    async def test_typo_triggers_owner_voice_but_not_unknown_private_chat(self):
        from astrbot_plugin_ada_companion.prompting import ContextBuilder

        plugin = object.__new__(ContextBuilder)
        plugin.target = "wechat:FriendMessage:owner"
        plugin.config = {"voice_enabled": True, "owner_sessions": [plugin.target]}
        plugin.policy = DeliveryPolicy(self.folder / "state.json", plugin.target)
        from astrbot_plugin_ada_companion.dialogue import DialogueState

        plugin.dialogue = DialogueState(self.folder / "dialogue.json")
        plugin.context = SimpleNamespace(
            get_all_stars=lambda: [],
            conversation_manager=SimpleNamespace(
                get_curr_conversation_id=AsyncMock(return_value=None)
            ),
        )
        plugin.services = SimpleNamespace(plugin=lambda name: None, memory=lambda: None)
        from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

        plugin.owner_memory = OwnerMemory(self.folder / "owners.json", [plugin.target])
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

        for origin, expected in (
            (plugin.target, True),
            ("qq:FriendMessage:stranger", False),
        ):
            channel, _, sender = origin.split(":", 2)
            event = native_event(channel, "用语言说一句晚安", sender)
            await plugin.prepare(event, SimpleNamespace(system_prompt="Ada"))
            self.assertEqual(event.get_extra("ada_voice_requested"), expected)


if __name__ == "__main__":
    unittest.main()
