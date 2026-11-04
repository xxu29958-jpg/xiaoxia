"""Atomic shared edits, idempotent native events, and independent SQLite clients."""

import asyncio
import unittest
from unittest.mock import patch

from support import QQ, WX, Harness, SharedCoreMemory, event, open_service


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = Harness(self)

    async def test_authority_revoked_between_writes_rolls_back_every_copy(self):
        await self.h.seed_copies()
        store = self.h.service.store
        original = store._core_write

        def revoke(*args):
            result = original(*args)
            self.h.links.healthy = False
            return result

        with patch.object(store, "_core_write", side_effect=revoke):
            refused = await self.h.call(action="set", label="voice", content="changed")
        self.assertEqual(refused["error"], "core_scope_changed")
        self.h.links.healthy = True
        self.assertEqual({r["content"] for r in await self.h.blocks()}, {"old"})

    async def test_copy_created_after_read_is_detected_before_any_write(self):
        await self.h.call(action="set", label="voice", content="old")
        original = self.h.service.store.change_core_memories

        async def add_copy(**kwargs):
            await self.h.service.save_core_memory_block(
                {
                    **self.h.links.context_variants(QQ)[0],
                    "label": "voice",
                    "content": "late copy",
                    "target_id": "owner",
                    "kind": "preference",
                }
            )
            return await original(**kwargs)

        with patch.object(
            self.h.service.store, "change_core_memories", side_effect=add_copy
        ):
            refused = await self.h.call(
                event(message_id="second"),
                action="set",
                label="voice",
                content="changed",
            )
        self.assertEqual(refused["error"], "core_members_changed")
        self.assertEqual(
            {r["content"] for r in await self.h.blocks()}, {"old", "late copy"}
        )

    async def test_feature_disabled_before_commit_rolls_back(self):
        await self.h.seed_copies()
        enabled = [True]
        store = self.h.service.store
        original = store._core_write

        def disable(*args):
            result = original(*args)
            enabled[0] = False
            return result

        with (
            patch.object(store, "_core_write", side_effect=disable),
            patch.object(
                self.h.service.config, "bool", side_effect=lambda *_args: enabled[0]
            ),
        ):
            refused = await self.h.call(action="set", label="voice", content="changed")
        self.assertEqual(refused["error"], "core_scope_changed")
        self.assertEqual({r["content"] for r in await self.h.blocks()}, {"old"})

    async def test_failure_after_first_copy_rolls_back_rows_history_and_receipt(self):
        await self.h.seed_copies()
        store = self.h.service.store
        original = store._core_write
        count = 0

        def fail_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic disk full")
            return original(*args)

        with patch.object(store, "_core_write", side_effect=fail_second):
            failed = await self.h.call(action="set", label="voice", content="new")
        self.assertFalse(failed["ok"])
        self.h.reopen()
        self.assertEqual({r["content"] for r in await self.h.blocks()}, {"old"})
        self.assertEqual([r["revision"] for r in await self.h.blocks()], [1, 1])
        rows = self.h.service.store._conn.execute(
            "SELECT count(*) FROM core_memory_mutations"
        ).fetchone()
        self.assertEqual(rows[0], 0)
        self.assertTrue(
            (await self.h.call(action="set", label="voice", content="new"))["ok"]
        )
        self.assertEqual({r["revision"] for r in await self.h.blocks()}, {2})

    async def test_ten_replays_and_restart_commit_once_then_old_replay_stays_historical(
        self,
    ):
        first = await self.h.call(
            action="set", label=" voice ", content="natural", kind=" Preference "
        )
        for _ in range(10):
            self.h.reopen()
            replay = await self.h.call(
                event(), action="set", label="voice", content="natural"
            )
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["writes"], first["writes"])
        changed = await self.h.call(
            event(WX, "我现在喜欢活泼些。", "w2"),
            action="set",
            label="voice",
            content="lively",
        )
        self.assertTrue(changed["ok"])
        replay = await self.h.call(
            event(), action="set", label="voice", content="natural"
        )
        self.assertTrue(replay["ok"])
        self.assertFalse(replay["current"])
        self.assertEqual((await self.h.blocks())[0]["content"], "lively")
        conflict = await self.h.call(
            event(), action="set", label="voice", content="different interpretation"
        )
        self.assertEqual(conflict["error"], "core_operation_conflict")

    async def test_delayed_model_cannot_overwrite_more_recent_incoming_message(self):
        older = event(QQ, "记住我喜欢安静。", "old")
        self.h.links.observe(older)
        newer = event(WX, "改为活泼一些。", "new")
        self.h.links.observe(newer)
        self.assertTrue(
            (await self.h.call(newer, action="set", label="voice", content="lively"))[
                "ok"
            ]
        )
        denied = await self.h.call(older, action="set", label="voice", content="quiet")
        self.assertEqual(denied["error"], "core_source_is_older")
        self.assertEqual((await self.h.blocks())[0]["content"], "lively")

    async def test_same_words_new_native_event_is_new_evidence(self):
        for message in ("one", "two"):
            receipt = await self.h.call(
                event(message_id=message),
                action="set",
                label="voice",
                content="natural",
            )
            self.assertTrue(receipt["ok"])
        self.assertEqual((await self.h.blocks())[0]["revision"], 2)

    async def test_two_connections_compare_versions_inside_the_transaction(self):
        await self.h.seed_copies()
        other = open_service(self.h.folder)
        self.addCleanup(other.store.close)
        adapter = SharedCoreMemory(self.h.links)
        reached, release = asyncio.Event(), asyncio.Event()
        original = self.h.service.store.change_core_memories

        async def late(**kwargs):
            reached.set()
            await release.wait()
            return await original(**kwargs)

        with patch.object(
            self.h.service.store, "change_core_memories", side_effect=late
        ):
            task = asyncio.create_task(
                self.h.call(action="set", label="voice", content="stale")
            )
            await reached.wait()
            fresh = await adapter.execute(
                WX, other, WX, action="set", label="voice", content="fresh"
            )
            self.assertTrue(fresh["ok"])
            release.set()
            stale = await task
        self.assertEqual(stale["error"], "core_revision_conflict")
        self.h.reopen()
        self.assertEqual({r["content"] for r in await self.h.blocks()}, {"fresh"})

    async def test_forget_removes_versions_and_old_replay_cannot_resurrect(self):
        await self.h.call(action="set", label="voice", content="natural")
        deleted = await self.h.call(
            event(WX, "忘掉这条语音偏好。", "forget"), action="delete", label="voice"
        )
        self.assertTrue(deleted["ok"])
        self.h.reopen()
        self.assertEqual(await self.h.blocks(), [])
        self.assertEqual(
            self.h.service.store._conn.execute(
                "SELECT count(*) FROM core_memory_revisions"
            ).fetchone()[0],
            0,
        )
        replay = await self.h.call(
            event(), action="set", label="voice", content="natural"
        )
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["current"])
        self.assertEqual(await self.h.blocks(), [])
