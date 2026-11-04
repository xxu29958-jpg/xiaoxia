"""Reappraisal revises the same event and never treats recall as fresh evidence."""

import asyncio
import json
import unittest
from unittest.mock import patch

from appraisal_support import FIRST, LATER, SAVE, AppraisalHarness
from astrbot_plugin_memory_companion.core.appraisal_state import AppraisalState
from support import QQ, WX, event


class AppraisalStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = AppraisalHarness(self)

    async def test_same_event_revised_cross_channel_restart_and_replay(self):
        first = await self.h.change()
        self.assertTrue(first["ok"], first)
        for _ in range(3):
            self.h.reopen()
            replay = await self.h.change()
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["version"], first["version"])
        later = event(WX, LATER, "correction")
        revised = await self.h.change(
            later,
            item_id=first["id"],
            evidence_quote=LATER,
            understanding="烦的是加班；现已下班，还想继续听这首歌。",
            response_implication="相关时可以接着聊那个版本。",
            uncertainty="",
            status="resolved",
        )
        self.assertTrue(revised["ok"], revised)
        self.h.reopen()
        state = await self.h.snapshot(WX)
        self.assertEqual(state["total"], 1)
        item = state["items"][0]
        self.assertEqual(item["original_quote"], FIRST)
        self.assertEqual(item["source"]["origin"], WX)
        self.assertEqual(item["original_source"]["origin"], QQ)
        self.assertEqual(item["revision"], 2)
        self.assertEqual(len((await self.h.history(first["id"]))["history"]), 2)
        replay = await self.h.change()
        self.assertFalse(replay["current"])
        self.assertEqual((await self.h.snapshot())["revision"], state["revision"])
        db = self.h.service.store._conn
        self.assertEqual(
            db.execute("SELECT count(*) FROM emotion_events").fetchone()[0], 0
        )
        self.assertEqual(
            db.execute("SELECT count(*) FROM working_objects").fetchone()[0], 0
        )

    async def test_concurrent_updates_unobserved_ids_and_late_sources(self):
        first = await self.h.change()
        a, b = event(text=LATER, message_id="a"), event(WX, LATER, "b")
        for source in (a, b):
            self.h.links.observe(source)
            await self.h.appraisals.snapshot(source.unified_msg_origin, event=source)
        change = {"item_id": first["id"], "evidence_quote": LATER}
        results = await asyncio.gather(
            self.h.change(a, read=False, **change),
            self.h.change(b, read=False, **change),
        )
        self.assertEqual(sum(r["ok"] for r in results), 1)
        self.assertIn("appraisal_version_conflict", [r.get("error") for r in results])
        unknown = event(text=LATER, message_id="unobserved")
        self.assertEqual(
            (await self.h.change(unknown, read=False, **change))["error"],
            "appraisal_version_conflict",
        )
        old = event(text=LATER, message_id="late", received_ns=1)
        self.assertEqual(
            (await self.h.change(old, **change))["error"], "appraisal_source_is_older"
        )

    async def test_same_source_cannot_create_second_event_or_conflicting_replay(self):
        first = await self.h.change()
        changed = await self.h.change(title="另造一件事")
        self.assertEqual(changed["error"], "appraisal_operation_conflict")
        self.assertEqual((await self.h.snapshot())["total"], 1)
        self.assertEqual(len((await self.h.history(first["id"]))["history"]), 1)

    async def test_quote_scope_stop_web_read_and_wrong_identity_are_rejected(self):
        bad_quotes = ["昨天说的并不在本条消息中", "", "烦"]
        for quote in bad_quotes:
            self.assertFalse((await self.h.change(evidence_quote=quote))["ok"])
        self.h.first.set_extra("ada_web_read_active", True)
        self.assertFalse((await self.h.change())["ok"])
        self.h.first.set_extra("ada_web_read_active", False)
        self.h.first.stop_event()
        self.assertFalse((await self.h.change())["ok"])
        stranger = event("qq:FriendMessage:stranger", FIRST, "stranger")
        self.assertFalse((await self.h.change(stranger))["ok"])
        self.assertEqual((await self.h.snapshot())["total"], 0)

    async def test_revoked_old_support_invalidates_latest_interpretation(self):
        capture, original = self.h.capture(self.h.first)
        first = await self.h.change()
        later = event(WX, LATER, "new-info")
        self.h.capture(later)
        self.assertTrue(
            (await self.h.change(later, item_id=first["id"], evidence_quote=LATER))[
                "ok"
            ]
        )
        capture.withdraw(original["source_key"])
        self.assertEqual((await self.h.snapshot())["items"], [])
        self.assertEqual(
            self.h.service.store._conn.execute(
                "SELECT count(*) FROM appraisal_revisions"
            ).fetchone()[0],
            0,
        )
        self.assertFalse((await self.h.change())["ok"])

    async def test_source_edit_invalidates_current_without_promoting_stale_prose(self):
        _, prepared = self.h.capture(self.h.first)
        first = await self.h.change()
        db = self.h.service.store._conn
        source_id = getattr(self.h.first, "_memory_capture_receipt")[
            "source_ref"
        ].split(":", 1)[1]
        with self.h.service.store._lock, self.h.service.store._transaction_sync():
            db.execute(
                "UPDATE timeline SET content='source corrected' WHERE id=?",
                (source_id,),
            )
        state = await self.h.snapshot()
        self.assertEqual((state["total"], state["unavailable"]), (0, 1))
        self.assertFalse((await self.h.change())["ok"])
        self.assertTrue(prepared["operation"])
        with self.assertRaises(ValueError):
            await self.h.history(first["id"])

    async def test_private_erasure_removes_all_derived_revisions_and_blocks_resurrection(
        self,
    ):
        first = await self.h.change()
        source = event(WX, LATER, "later")
        await self.h.change(source, item_id=first["id"], evidence_quote=LATER)
        preview = await self.h.service.store.preview_scoped_memory_clear(
            target_type="private", user_id="owner"
        )
        self.assertEqual(preview["counts"]["appraisal_objects"], 1)
        await self.h.service.store.clear_scoped_memory(
            target_type="private", user_id="owner"
        )
        self.assertEqual((await self.h.snapshot())["items"], [])
        replay = await self.h.change()
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["current"])
        rows = self.h.service.store._conn.execute(
            "SELECT * FROM appraisal_operations"
        ).fetchall()
        self.assertNotIn(
            FIRST, json.dumps([tuple(r) for r in rows], ensure_ascii=False)
        )

    async def test_disk_failure_rolls_back_state_history_receipt_then_recovers(self):
        original = AppraisalState._write

        def failing(port, *args):
            original(port, *args)
            raise OSError("synthetic full disk")

        with patch.object(AppraisalState, "_write", failing):
            self.assertFalse((await self.h.change())["ok"])
        for table in (
            "appraisal_objects",
            "appraisal_revisions",
            "appraisal_operations",
        ):
            self.assertEqual(
                self.h.service.store._conn.execute(
                    "SELECT count(*) FROM " + table
                ).fetchone()[0],
                0,
            )
        self.assertTrue((await self.h.change())["ok"])

    async def test_bounded_snapshot_and_history_do_not_change_evidence(self):
        for i in range(10):
            await self.h.change(event(text=FIRST, message_id=str(i)))
        before = await self.h.snapshot()
        self.assertEqual(
            (before["total"], len(before["items"]), before["truncated"]), (10, 8, True)
        )
        for _ in range(4):
            self.assertEqual(await self.h.snapshot(WX), before)
        self.assertEqual(
            self.h.service.store._conn.execute(
                "SELECT count(*) FROM appraisal_revisions"
            ).fetchone()[0],
            10,
        )

    async def test_malformed_and_oversized_proposals_are_failures_not_partial_records(
        self,
    ):
        for change in [
            None,
            [],
            {**SAVE, "understanding": 3},
            {**SAVE, "status": "guess"},
            {**SAVE, "understanding": "x" * 1001},
            {**SAVE, "extra": "new field"},
        ]:
            self.assertFalse(
                (await self.h.appraisals.update(self.h.first, change))["ok"]
            )
        self.assertEqual((await self.h.snapshot())["total"], 0)
