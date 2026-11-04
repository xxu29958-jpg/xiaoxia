"""The actual chat tool, prompt reader and existing source capture share one store."""

import json
import unittest
from unittest.mock import patch

from astrbot_plugin_memory_companion.core.injection import InjectionComposer
from astrbot_plugin_memory_companion.core.models import utc_now
from astrbot_plugin_memory_companion.core.source_capture import SourceCapture
from support import QQ, WX, Harness, event


class ConsumerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = Harness(self)

    async def test_chat_source_history_cross_channel_prompt_and_restart(self):
        original = "记住：我喜欢自然说话。\n别像播音。"
        first = event(QQ, original, "source-q")
        await self.h.call(first, action="set", label="voice", content="natural")
        second = event(WX, "改一下，喜欢自然而且活泼。", "source-w")
        await self.h.call(
            second, action="set", label="voice", content="natural and lively"
        )
        self.h.reopen()
        for origin in (QQ, WX):
            listed = await self.h.call(event(origin))
            self.assertEqual(listed["blocks"][0]["content"], "natural and lively")
            self.assertEqual(
                listed["blocks"][0]["provenance"]["message_id"], "source-w"
            )
        history = (await self.h.call(action="history", label="voice"))["history"]
        self.assertEqual([r["revision"] for r in history], [2, 1])
        self.assertEqual(history[1]["source"]["text"], original)
        self.assertFalse(history[1]["current"])
        ctx = self.h.service.session_context_from_bridge(
            self.h.links.context_variants(WX)[0]
        )
        records = await self.h.service.core_memories_for_context(ctx)
        rendered = InjectionComposer().compose(
            ctx, [], max_chars=2200, core_memories=records
        )
        self.assertIn("natural and lively", rendered)
        self.assertNotIn("经管理端确认", rendered)
        self.assertNotIn(original, rendered)
        self.assertEqual(records[0].message_id, "source-w")
        self.assertEqual(records[0].metadata["managed_by"], "owner_chat")

    async def test_unlinked_missing_id_spoofed_sender_and_disabled_tool_are_rejected(
        self,
    ):
        unlinked = event("qq:FriendMessage:stranger")
        missing = event(message_id="")
        spoofed = event()
        spoofed.get_sender_id = lambda: "stranger"
        for source in (unlinked, missing, spoofed):
            result = await self.h.call(
                source, action="set", label="voice", content="unauthorized"
            )
            self.assertFalse(result["ok"], result)
        with patch.object(self.h.service.config, "bool", return_value=False):
            self.assertFalse(
                (await self.h.call(action="set", label="voice", content="disabled"))[
                    "ok"
                ]
            )
        self.assertEqual(await self.h.blocks(), [])

    async def test_redaction_covers_new_source_and_history_and_list(self):
        secret = "synthetic" + "-credential-value"
        source = event(text="记住偏好。password=" + secret)
        await self.h.call(source, action="set", label="voice", content="natural")
        history = await self.h.call(action="history", label="voice")
        self.assertTrue(history["history"][0]["source"]["redacted"])
        self.assertNotIn(secret, json.dumps(history))
        for table in ("memories", "core_memory_revisions", "core_memory_mutations"):
            rows = self.h.service.store._conn.execute(
                "SELECT * FROM " + table
            ).fetchall()
            self.assertNotIn(secret, str([tuple(r) for r in rows]))

    def captured(self):
        store = self.h.service.store
        capture = SourceCapture(store)
        capture.activate("test-generation")
        source = event(message_id="captured")
        identity = {
            "installation": capture.installation,
            "platform": "qq",
            "bot": "qq",
            "persona": "Ada",
            "scope": "private",
            "session": QQ,
            "speaker": "owner",
            "audience": "owner",
            "direction": "incoming",
            "message": "captured",
        }
        prepared = capture.prepare(
            identity, body=source.get_message_str(), observed_at=utc_now()
        )
        capture.stage(prepared, generation="test-generation")
        setattr(
            source,
            "_memory_capture_receipt",
            capture.commit(prepared["operation"], generation="test-generation"),
        )
        return source, capture, prepared

    async def test_existing_source_capture_is_reused_and_revocation_blocks_replay(self):
        source, capture, prepared = self.captured()
        receipt = await self.h.call(
            source, action="set", label="voice", content="natural"
        )
        self.assertTrue(receipt["ok"], receipt)
        history = (await self.h.call(action="history", label="voice"))["history"]
        self.assertEqual(
            history[0]["source"]["capture"]["source_ref"],
            getattr(source, "_memory_capture_receipt")["source_ref"],
        )
        capture.withdraw(prepared["source_key"])
        denied = await self.h.call(
            source, action="set", label="voice", content="natural"
        )
        self.assertEqual(denied["error"], "core_source_unavailable")
        self.assertEqual(await self.h.blocks(), [])

    async def test_withdrawing_old_source_erases_its_history_but_keeps_independent_current_fact(
        self,
    ):
        source, capture, prepared = self.captured()
        await self.h.call(
            source, action="set", label="voice", content="old captured fact"
        )
        await self.h.call(
            event(WX, "新约定。", "independent"),
            action="set",
            label="voice",
            content="independent new fact",
        )
        capture.withdraw(prepared["source_key"])
        rows = (await self.h.call(action="history", label="voice"))["history"]
        self.assertEqual([row["revision"] for row in rows], [2])
        self.assertEqual((await self.h.blocks())[0]["content"], "independent new fact")

    async def test_recognized_speech_keeps_transcript_provenance(self):
        source = event(text="记住我喜欢自然说话。", message_id="voice-input")
        source.set_extra("voice_input", True)
        result = await self.h.call(
            source, action="set", label="voice", content="natural"
        )
        self.assertTrue(result["ok"], result)
        rows = (await self.h.call(action="history", label="voice"))["history"]
        self.assertEqual(rows[0]["source"]["observation"], "speech_transcript")

    async def test_forget_and_recreate_cannot_make_old_receipt_current(self):
        await self.h.call(action="set", label="voice", content="natural")
        await self.h.call(event(message_id="delete"), action="delete", label="voice")
        await self.h.call(
            event(message_id="recreate"),
            action="set",
            label="voice",
            content="new fact",
        )
        replay = await self.h.call(action="set", label="voice", content="natural")
        self.assertFalse(replay["current"])
        self.assertEqual((await self.h.blocks())[0]["content"], "new fact")

    def test_empty_retrieval_facts_render_as_no_facts(self):
        self.assertEqual(InjectionComposer._select_relevant_facts([]), [])
