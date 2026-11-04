"""Terminate the writer process at transaction boundaries and upgrade a legacy copy."""

import asyncio
import json
import multiprocessing
import sqlite3
import unittest
from contextlib import closing

from support import QQ, WX, Harness, OwnerMemory, SharedCoreMemory, event, open_service


def writer(folder, stage, reached, release):
    async def run():
        service = open_service(folder)
        links = OwnerMemory(folder / "owners.json", [QQ, WX])
        source = event(message_id="process-loss")
        links.observe(source)
        if stage == "between_copies":
            original = service.store._core_write

            def interrupted(record, current, source):
                result = original(record, current, source)
                reached.set()
                release.wait(60)
                return result

            service.store._core_write = interrupted
        await SharedCoreMemory(links).execute(
            QQ, service, WX, event=source, action="set", label="voice", content="new"
        )
        reached.set()
        release.wait(60)

    asyncio.run(run())


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = Harness(self)

    async def test_kill_between_copies_rolls_back_and_same_event_can_retry(self):
        await self.check_kill("between_copies")

    async def test_kill_after_commit_replays_receipt_without_second_write(self):
        await self.check_kill("after_commit")

    async def check_kill(self, stage):
        await self.h.seed_copies()
        context = multiprocessing.get_context("spawn")
        reached, release = context.Event(), context.Event()
        process = context.Process(
            target=writer, args=(self.h.folder, stage, reached, release)
        )
        process.start()
        try:
            self.assertTrue(
                await asyncio.to_thread(reached.wait, 25),
                "writer never reached fault boundary",
            )
        finally:
            process.terminate()
            await asyncio.to_thread(process.join, 10)
            self.assertFalse(process.is_alive())
        self.h.reopen()
        expected = "old" if stage == "between_copies" else "new"
        self.assertEqual({r["content"] for r in await self.h.blocks()}, {expected})
        replay = await self.h.call(
            event(message_id="process-loss"), action="set", label="voice", content="new"
        )
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["replayed"], stage == "after_commit")
        self.assertEqual({r["revision"] for r in await self.h.blocks()}, {2})
        self.assertEqual(
            self.h.service.store._conn.execute("PRAGMA integrity_check").fetchone()[0],
            "ok",
        )

    async def test_legacy_schema_upgrade_keeps_backup_and_unknown_provenance(self):
        await self.h.seed_copies()
        store = self.h.service.store
        with store._lock, store._transaction_sync():
            store._conn.execute("DROP TABLE core_memory_revisions")
            store._conn.execute("DROP TABLE core_memory_mutations")
            rows = store._conn.execute("SELECT id,metadata FROM memories").fetchall()
            for row in rows:
                meta = json.loads(row["metadata"])
                meta.pop("core_source", None)
                meta.pop("core_version", None)
                store._conn.execute(
                    "UPDATE memories SET metadata=? WHERE id=?",
                    (json.dumps(meta), row["id"]),
                )
            store._conn.execute(
                "UPDATE schema_metadata SET value='memory-atom-v2' WHERE key='schema_version'"
            )
        self.h.reopen()
        backups = list(self.h.folder.glob("memory.before-*.db"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(
                backup.execute(
                    "SELECT value FROM schema_metadata WHERE key='schema_version'"
                ).fetchone()[0],
                "memory-atom-v2",
            )
            self.assertEqual(
                backup.execute("SELECT count(*) FROM memories").fetchone()[0], 2
            )
        await self.h.call(
            event(message_id="upgrade-edit"), action="set", label="voice", content="new"
        )
        rows = (await self.h.call(action="history", label="voice"))["history"]
        legacy = [row for row in rows if row["revision"] == 1]
        self.assertEqual(len(legacy), 2)
        self.assertTrue(all(row["source"]["kind"] == "legacy" for row in legacy))
        self.assertTrue(all(row["source"]["message_id"] == "" for row in legacy))
