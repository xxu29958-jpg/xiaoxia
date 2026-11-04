"""Native backup migration, moved restoration, cancellation and real process loss."""

import asyncio
import multiprocessing
import shutil
import sqlite3
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

from appraisal_support import FIRST, SAVE, AppraisalHarness
from astrbot_plugin_memory_companion.core.appraisal_state import AppraisalState
from support import QQ, event, open_service


def writer(folder, stage, reached, release):
    async def run():
        service = open_service(folder)
        state = AppraisalState(service.store)
        if stage == "before_commit":
            original = state._write

            def interrupted(*args, **kwargs):
                result = original(*args, **kwargs)
                reached.set()
                release.wait(60)
                return result

            state._write = interrupted
        receipt = await write_recovery(state)
        if not receipt["ok"]:
            raise AssertionError(receipt)
        reached.set()
        release.wait(60)

    asyncio.run(run())


async def write_recovery(state):
    source = event(text=FIRST, message_id="process-loss", received_ns=100)
    return await state.change(
        owner="crash-owner",
        change=SAVE,
        source=state.store.core_event_source(source),
        scopes=[
            {"session_id": QQ, "platform": "qq", "user_id": "owner", "bot_id": "qq"}
        ],
        authorize=lambda: True,
    )


class AppraisalRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = AppraisalHarness(self)

    async def test_kill_before_commit_rolls_back_and_retry_commits_once(self):
        await self.check_kill("before_commit")

    async def test_kill_after_commit_replays_original_receipt(self):
        await self.check_kill("after_commit")

    async def check_kill(self, stage):
        ctx = multiprocessing.get_context("spawn")
        reached, release = ctx.Event(), ctx.Event()
        child = ctx.Process(
            target=writer, args=(self.h.folder, stage, reached, release)
        )
        child.start()
        try:
            self.assertTrue(
                await asyncio.to_thread(reached.wait, 30), "fault boundary not reached"
            )
        finally:
            child.terminate()
            await asyncio.to_thread(child.join, 10)
            self.assertFalse(child.is_alive())
        self.h.reopen()
        state = AppraisalState(self.h.service.store)
        before = await state.snapshot("crash-owner", authorize=lambda: True)
        self.assertEqual(before["total"], int(stage == "after_commit"))
        receipt = await write_recovery(state)
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(receipt["replayed"], stage == "after_commit")
        self.assertEqual(
            (await state.snapshot("crash-owner", authorize=lambda: True))["total"], 1
        )

    async def test_backup_move_restore_keeps_versions_and_sources(self):
        first = await self.h.change()
        expected = await self.h.snapshot()
        backup = self.h.service.store.backup(".appraisal-backup")
        recovered = self.h.folder / "moved"
        recovered.mkdir()
        shutil.copyfile(backup, recovered / "memory.db")
        service = open_service(recovered)
        self.addCleanup(service.store.close)
        state = AppraisalState(service.store)
        self.assertEqual(
            await state.snapshot(self.h.links.shared_scope, authorize=lambda: True),
            expected,
        )
        history = await state.history(
            self.h.links.shared_scope, first["id"], authorize=lambda: True
        )
        self.assertEqual(history["history"][0]["original_quote"], FIRST)

    async def test_previous_schema_is_backed_up_before_new_tables(self):
        store = self.h.service.store
        with store._lock, store._transaction_sync():
            store._conn.execute("DROP TRIGGER appraisal_source_deleted")
            for table in (
                "appraisal_revisions",
                "appraisal_objects",
                "appraisal_operations",
            ):
                store._conn.execute("DROP TABLE " + table)
            store._conn.execute(
                "UPDATE schema_metadata SET value='memory-atom-v2-working-state-v1' WHERE key='schema_version'"
            )
        self.h.reopen()
        backups = list(self.h.folder.glob("memory.before-*.db"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as old:
            self.assertIsNone(
                old.execute(
                    "SELECT name FROM sqlite_master WHERE name='appraisal_objects'"
                ).fetchone()
            )
        self.assertTrue((await self.h.change())["ok"])

    async def test_stopping_turn_during_write_rolls_back_before_store_close(self):
        reached, release = threading.Event(), threading.Event()
        original = AppraisalState._write

        def interrupted(state, *args):
            result = original(state, *args)
            reached.set()
            if not release.wait(10):
                raise TimeoutError("release missing")
            return result

        with patch.object(AppraisalState, "_write", interrupted):
            task = asyncio.create_task(self.h.change())
            try:
                self.assertTrue(await asyncio.to_thread(reached.wait, 5))
                self.h.first.stop_event()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                closed = asyncio.create_task(
                    asyncio.to_thread(self.h.service.store.close)
                )
                await asyncio.sleep(0.02)
                self.assertFalse(closed.done())
            finally:
                release.set()
            await asyncio.wait_for(closed, 5)
        self.h.reopen()
        self.assertEqual((await self.h.snapshot())["total"], 0)

    async def test_global_clear_removes_prose_keeps_only_replay_barrier(self):
        await self.h.change()
        cleared = await self.h.service.store.clear_all_memory_data()
        self.assertEqual(cleared["deleted"]["appraisal_objects"], 1)
        self.assertEqual((await self.h.snapshot())["total"], 0)
        replay = await self.h.change()
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["current"])
