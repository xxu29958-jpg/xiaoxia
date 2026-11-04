"""Chat admission → real HTTP jobs → retained MP3 → receipt/recovery, no external sends."""

import asyncio
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from imageio_ffmpeg import get_ffmpeg_exe
from support import Gateway, audio_file, event, until

from astrbot.core.message.components import File
from data.plugins.astrbot_plugin_ada_companion.lifecycle import Lifecycle
from data.plugins.astrbot_plugin_ada_companion.main import AdaCompanion
from data.plugins.astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from data.plugins.astrbot_plugin_ada_companion.performance_files import verify
from data.plugins.astrbot_plugin_ada_companion.performance_store import PerformanceError
from data.plugins.astrbot_plugin_ada_companion.performances import Performances


class JourneyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gateway = Gateway(self.root / "gateway")
        self.addCleanup(self.gateway.close)
        key = self.root / "service.key"
        key.write_text("fixture-key")
        self.config = {
            "voice_enabled": True,
            "voice_key_file": str(key),
            "voice_base_url": self.gateway.url,
            "ffmpeg_path": shutil.which("ffmpeg") or get_ffmpeg_exe(),
        }
        self.events = {name: event(name) for name in ("aiocqhttp", "weixin_oc")}
        self.owners = OwnerMemory(
            self.root / "owners.json",
            [current.unified_msg_origin for current in self.events.values()],
        )
        self.bot = SimpleNamespace(
            send_private_msg=AsyncMock(return_value={"message_id": 12345})
        )
        self.adapters = {
            name: SimpleNamespace(
                meta=lambda name=name: SimpleNamespace(name=name), bot=self.bot
            )
            for name in self.events
        }
        self.context = SimpleNamespace(
            get_platform_inst=self.adapters.get,
            send_message=AsyncMock(return_value=True),
        )
        self.component = Performances(self.root, self.config, self.owners, self.context)
        self.plugin = object.__new__(AdaCompanion)
        self.plugin.lifecycle = Lifecycle()
        self.plugin.performances = self.component
        self.source = audio_file(self.root / "opera.wav")
        for current in self.events.values():
            current.message_obj.message.append(
                File(name="opera.wav", file=str(self.source))
            )
        self.addAsyncCleanup(self.close_component)

    async def close_component(self):
        await self.component.worker.close()

    async def submit(self, channel="aiocqhttp"):
        result = json.loads(
            await self.plugin.cover_song(
                self.events[channel], title="戏曲", acapella=True
            )
        )
        self.assertTrue(result["ok"])
        return result["request_id"]

    def saved(self, request_id):
        return self.component.store.get(self.owners.shared_scope, request_id)

    async def restart(self):
        await self.component.worker.close()
        self.component = Performances(self.root, self.config, self.owners, self.context)
        self.plugin.performances = self.component
        self.component.worker.start()

    async def assert_full_audio(self, artifact):
        file = verify(artifact)
        process = await asyncio.create_subprocess_exec(
            self.config["ffmpeg_path"],
            "-v",
            "error",
            "-i",
            str(file),
            "-f",
            "s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        decoded, _ = await process.communicate()
        self.assertEqual(process.returncode, 0)
        self.assertLess(abs(len(decoded) / 32000 - 61), 0.02)

    async def test_complete_track_progress_cross_channel_restart_and_no_duplicate_send(
        self,
    ):
        before = time.monotonic()
        request_id = await self.submit()
        self.assertLess(time.monotonic() - before, 2)
        self.component.worker.start()
        await until(lambda: self.gateway.started.is_set(), bool)
        # Remote rendering may start before the POST response binds the local job.
        await until(lambda: self.saved(request_id), lambda row: bool(row["job_id"]))
        status = json.loads(
            await self.plugin.performance_status(
                self.events["weixin_oc"], request_id=request_id
            )
        )
        self.assertEqual(status["generation"]["stage"], "converting")
        # A retried native event with the same bytes at a different attachment path is one request.
        other = self.root / "redelivered.wav"
        other.write_bytes(self.source.read_bytes())
        self.events["aiocqhttp"].message_obj.message[-1] = File(
            name="again.wav", file=str(other)
        )
        self.assertEqual(await self.submit(), request_id)
        self.gateway.release.set()
        saved = await until(
            lambda: self.saved(request_id), lambda row: row["state"] == "accepted"
        )
        self.assertEqual(saved["receipt"]["message_id"], "12345")
        self.assertFalse(saved["receipt"]["played"])
        await self.assert_full_audio(saved["artifact"])
        self.bot.send_private_msg.assert_awaited_once()
        call = self.bot.send_private_msg.call_args.kwargs
        self.assertEqual(call["user_id"], 1234567890)
        self.assertTrue(call["message"][0]["data"]["file"].startswith("file:"))
        await self.restart()
        await asyncio.sleep(1.2)
        self.assertEqual(self.saved(request_id)["receipt"], saved["receipt"])
        self.bot.send_private_msg.assert_awaited_once()
        self.assertEqual(
            self.gateway.store.get(saved["job_id"], self.owners.shared_scope)[
                "attempt"
            ],
            1,
        )

    async def test_wechat_adapter_return_is_retained_without_fabricated_receipt(self):
        request_id = await self.submit("weixin_oc")
        self.gateway.release.set()
        self.component.worker.start()
        saved = await until(
            lambda: self.saved(request_id), lambda row: row["state"] == "unknown"
        )
        self.assertEqual(saved["receipt"]["kind"], "adapter_returned")
        self.assertNotIn("message_id", saved["receipt"])
        self.context.send_message.assert_awaited_once()
        target, chain = self.context.send_message.call_args.args
        self.assertEqual(target, self.events["weixin_oc"].unified_msg_origin)
        self.assertIsInstance(chain.chain[0], File)
        await self.assert_full_audio(saved["artifact"])
        await self.restart()
        await asyncio.sleep(1.2)
        self.context.send_message.assert_awaited_once()

    async def test_cancel_while_generation_waits_keeps_controls_available_and_never_sends(
        self,
    ):
        request_id = await self.submit()
        self.component.worker.start()
        await until(lambda: self.gateway.started.is_set(), bool)
        before = time.monotonic()
        result = json.loads(
            await self.plugin.performance_status(
                self.events["weixin_oc"], "cancel", request_id
            )
        )
        self.assertTrue(result["cancel_requested"])
        self.assertLess(time.monotonic() - before, 1)
        saved = await until(
            lambda: self.saved(request_id), lambda row: row["state"] == "cancelled"
        )
        self.gateway.release.set()
        self.bot.send_private_msg.assert_not_awaited()
        self.assertTrue(
            self.gateway.store.get(saved["job_id"], self.owners.shared_scope)[
                "cancel_requested"
            ]
        )

    async def test_lost_send_response_is_unknown_and_never_retried(self):
        self.bot.send_private_msg.side_effect = TimeoutError("fixture response lost")
        request_id = await self.submit()
        self.gateway.release.set()
        self.component.worker.start()
        saved = await until(
            lambda: self.saved(request_id), lambda row: row["state"] == "unknown"
        )
        self.assertEqual(saved["receipt"]["reason"], "TimeoutError")
        await self.restart()
        await asyncio.sleep(1.2)
        self.bot.send_private_msg.assert_awaited_once()

    async def test_revoked_destination_and_foreign_caller_are_rejected(self):
        request_id = await self.submit()
        self.component.worker.start()
        await until(lambda: self.gateway.started.is_set(), bool)
        self.owners.owners = (self.events["weixin_oc"].unified_msg_origin,)
        self.gateway.release.set()
        saved = await until(
            lambda: self.saved(request_id), lambda row: row["state"] == "failed"
        )
        self.assertEqual(saved["error"], "performance_authorization_revoked")
        self.bot.send_private_msg.assert_not_awaited()
        with self.assertRaises(PermissionError):
            await self.plugin.performance_status(
                event("aiocqhttp", "999"), request_id=request_id
            )

    async def test_admission_response_loss_replays_same_gateway_job(self):
        request_id = await self.submit()
        original_submit = self.component.client.submit

        async def lost_response(request):
            await original_submit(request)
            raise PerformanceError("performance_gateway_unavailable")

        self.component.client.submit = lost_response
        claim = self.component.store.claim()
        assert claim is not None
        await self.component.worker.perform(claim)
        self.assertIsNone(self.saved(request_id)["job_id"])
        await self.restart()
        self.component.store.clock = lambda: time.time() + 16
        self.gateway.release.set()
        saved = await until(
            lambda: self.saved(request_id), lambda row: row["state"] == "accepted"
        )
        with self.gateway.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        self.assertEqual(
            self.gateway.store.get(saved["job_id"], self.owners.shared_scope)[
                "attempt"
            ],
            1,
        )
        self.bot.send_private_msg.assert_awaited_once()

    async def test_generation_failure_delivers_a_failure_notice_and_keeps_audio_absent(
        self,
    ):
        self.gateway.fail_generation = True
        request_id = await self.submit()
        self.gateway.release.set()
        self.component.worker.start()
        saved = await until(
            lambda: self.saved(request_id), lambda row: row["state"] == "accepted"
        )
        self.assertEqual(saved["generation"]["state"], "failed")
        self.assertEqual(saved["artifact"]["format"], "text")
        self.assertNotIn("path", saved["artifact"])
        call = self.bot.send_private_msg.call_args.kwargs
        self.assertEqual(call["message"][0]["type"], "text")
        self.assertIn("没能生成完整歌声", call["message"][0]["data"]["text"])

    async def test_equivalent_numeric_parameters_are_one_native_request(self):
        current = self.events["aiocqhttp"]
        first = json.loads(await self.plugin.cover_song(current, start_seconds=0))
        second = json.loads(
            await self.plugin.cover_song(
                current, title="同一首歌的新显示名", start_seconds=0.0
            )
        )
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(first["request_id"], second["request_id"])
        with self.component.store.connection() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1
            )

    async def test_invalid_input_is_rejected_before_admission(self):
        current = self.events["aiocqhttp"]
        result = json.loads(await self.plugin.cover_song(current, pitch=13))
        self.assertEqual(result["reason"], "invalid_performance_parameters")
        current.message_obj.message_id = ""
        result = json.loads(await self.plugin.cover_song(current))
        self.assertEqual(result["reason"], "missing_native_message_id")
        with self.component.store.connection() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0
            )


if __name__ == "__main__":
    unittest.main()
