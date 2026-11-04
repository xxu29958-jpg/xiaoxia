"""Actual migration rollback, native source withdrawal and process termination."""

import asyncio
import json
import multiprocessing
import shutil
import sqlite3
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

from astrbot_plugin_ada_companion.dialogue import DialogueState
from astrbot_plugin_memory_companion.core.models import utc_now
from astrbot_plugin_memory_companion.core.source_capture import SourceCapture
from astrbot_plugin_memory_companion.core.working_state import WorkingState
from support import QQ, WX, event, open_service
from working_support import WorkingHarness, legacy_record

SAVE = {
    "action": "save",
    "kind": "goal",
    "title": "练歌",
    "content": "继续练完整歌曲",
    "evidence_quote": "喜欢自然说话",
}


def working_writer(folder, stage, reached, release):
    async def run():
        service = open_service(folder)
        source = event(message_id="process-loss", received_ns=100)
        state = WorkingState(service.store)
        if stage == "before_commit":
            original = state.write

            def interrupted(*args, **kwargs):
                original(*args, **kwargs)
                reached.set()
                release.wait(60)

            state.write = interrupted
        receipt = await state.change(
            owner="crash-owner",
            change=SAVE,
            source=service.store.core_event_source(source),
            scopes=[
                {"session_id": QQ, "platform": "qq", "user_id": "owner", "bot_id": "qq"}
            ],
            authorize=lambda: True,
        )
        if not receipt["ok"]:
            raise AssertionError(receipt)
        reached.set()
        release.wait(60)

    asyncio.run(run())


class WorkingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = WorkingHarness(self)

    def legacy(self, records):
        path = self.h.dialogue.path
        path.write_text(
            json.dumps({"version": 1, "sessions": {QQ: records}}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path.read_bytes()

    async def test_legacy_adoption_is_atomic_preserves_bytes_and_does_not_reapply(self):
        before = self.legacy(
            [legacy_record("old-one"), legacy_record("old-two", "另一话题")]
        )
        original = WorkingState.write

        def fail_second(port, record, **kwargs):
            original(port, record, **kwargs)
            if record["id"] == "old-two":
                raise OSError("synthetic disk full")

        with patch.object(WorkingState, "write", fail_second):
            self.assertFalse((await self.h.dialogue.snapshot(QQ))["ok"])
        for table in (
            "working_objects",
            "working_revisions",
            "working_names",
            "working_imports",
        ):
            self.assertEqual(
                self.h.service.store._conn.execute(
                    "SELECT count(*) FROM " + table
                ).fetchone()[0],
                0,
            )
        items = await self.h.items()
        self.assertEqual({r["id"] for r in items}, {"old-one", "old-two"})
        self.assertTrue(all(r["source"]["kind"] == "legacy" for r in items))
        self.assertTrue(all("message_id" not in r["source"] for r in items))
        self.assertEqual(self.h.dialogue.path.read_bytes(), before)
        changed = await self.h.working(
            event(text="现在改成新进展", message_id="edit"),
            action="save",
            title="后续话题",
            content="新进展",
            evidence_quote="现在改成新进展",
        )
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(changed["id"], "old-one")
        self.h.reopen()
        self.assertEqual(
            next(r["content"] for r in await self.h.items() if r["id"] == "old-one"),
            "新进展",
        )
        await self.h.service.store.clear_all_memory_data()
        self.assertEqual(await self.h.items(), [])
        self.assertEqual(self.h.dialogue.path.read_bytes(), before)

    async def test_damaged_or_changed_legacy_file_is_explicitly_unavailable(self):
        for contents in (b"", b"not-json", b'{"version":1,"sessions":{"qq":[{}]}}'):
            self.h.dialogue.path.write_bytes(contents)
            result = await self.h.dialogue.snapshot(QQ)
            self.assertFalse(result["ok"])
            self.assertEqual(self.h.dialogue.path.read_bytes(), contents)
        self.legacy([legacy_record("original")])
        self.assertEqual(len(await self.h.items()), 1)
        before = self.legacy([legacy_record("externally-changed")])
        result = await self.h.dialogue.snapshot(QQ)
        self.assertEqual(result["error"], "working_legacy_changed_after_adoption")
        self.assertEqual(self.h.dialogue.path.read_bytes(), before)

    async def test_duplicate_legacy_titles_keep_ids_and_require_explicit_selection(
        self,
    ):
        self.legacy([legacy_record("one"), legacy_record("two")])
        self.assertEqual(len(await self.h.items()), 2)
        source = event(text="现在更新这个话题", message_id="edit")
        change = {
            "action": "save",
            "title": "后续话题",
            "content": "新内容",
            "evidence_quote": "更新这个话题",
        }
        self.assertEqual(
            (await self.h.working(source, **change))["error"],
            "working_title_ambiguous_use_id",
        )
        self.assertTrue((await self.h.working(source, **change, item_id="one"))["ok"])
        self.assertEqual(len(await self.h.items()), 2)

    async def test_backup_restore_preserves_versions_and_can_move_with_original_json(
        self,
    ):
        self.legacy([legacy_record("retained")])
        initial = await self.h.items()
        backup = self.h.service.store.backup(".working-backup")
        recovered = self.h.folder / "recovered"
        recovered.mkdir()
        shutil.copyfile(backup, recovered / "memory.db")
        shutil.copyfile(self.h.dialogue.path, recovered / "dialogue.json")
        service = open_service(recovered)
        self.addCleanup(service.store.close)
        from types import SimpleNamespace

        state = DialogueState(
            recovered / "dialogue.json",
            self.h.links,
            SimpleNamespace(memory=lambda: SimpleNamespace(_plugin=service)),
        )
        self.assertEqual(await state.visible(QQ), initial)
        self.assertEqual(
            service.store._conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
        )

    async def test_schema_upgrade_creates_backup_before_adoption(self):
        store = self.h.service.store
        with store._lock, store._transaction_sync():
            store._conn.execute("DROP TRIGGER working_source_deleted")
            for table in (
                "working_revisions",
                "working_objects",
                "working_operations",
                "working_names",
                "working_imports",
            ):
                store._conn.execute("DROP TABLE " + table)
            store._conn.execute(
                "UPDATE schema_metadata SET value='memory-atom-v2-core-state-v1' WHERE key='schema_version'"
            )
        self.h.reopen()
        backups = list(self.h.folder.glob("memory.before-*.db"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as old:
            self.assertIsNone(
                old.execute(
                    "SELECT name FROM sqlite_master WHERE name='working_objects'"
                ).fetchone()
            )
        self.assertTrue((await self.h.working(**SAVE))["ok"])

    async def test_source_withdrawal_removes_working_object_and_blocks_replay(self):
        capture = SourceCapture(self.h.service.store)
        capture.activate("working-test")
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
        capture.stage(prepared, generation="working-test")
        setattr(
            source,
            "_memory_capture_receipt",
            capture.commit(prepared["operation"], generation="working-test"),
        )
        self.assertTrue((await self.h.working(source, **SAVE))["ok"])
        capture.withdraw(prepared["source_key"])
        self.assertEqual(await self.h.items(), [])
        self.assertEqual(
            (await self.h.working(source, **SAVE))["error"], "core_source_unavailable"
        )

    async def test_kill_before_commit_rolls_back_then_retries(self):
        await self.check_kill("before_commit")

    async def test_kill_after_commit_returns_original_receipt(self):
        await self.check_kill("after_commit")

    async def test_later_verified_channel_adopts_only_its_preexisting_legacy_entries(
        self,
    ):
        self.h.dialogue.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sessions": {
                        QQ: [legacy_record("qq-old", "QQ话题")],
                        WX: [legacy_record("wx-old", "微信话题")],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.h.links.owners = (QQ,)
        self.assertEqual({r["id"] for r in await self.h.items()}, {"qq-old"})
        await self.h.service.store.clear_all_memory_data()
        self.h.links.owners = (QQ, WX)
        self.assertEqual({r["id"] for r in await self.h.items()}, {"wx-old"})
        self.h.reopen()
        self.assertEqual({r["id"] for r in await self.h.items()}, {"wx-old"})

    async def test_cancelled_wait_and_store_close_preserve_a_recoverable_commit(self):
        await self.cancel_at_write(stopped=False)

    async def test_stopped_turn_before_commit_rolls_back_even_when_wait_is_cancelled(
        self,
    ):
        await self.cancel_at_write(stopped=True)

    async def cancel_at_write(self, *, stopped):
        reached, release, closing = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        original = WorkingState.write
        store = self.h.service.store

        def interrupted(port, record, **kwargs):
            original(port, record, **kwargs)
            reached.set()
            if not release.wait(10):
                raise TimeoutError("test release missing")

        def close():
            closing.set()
            store.close()

        with patch.object(WorkingState, "write", interrupted):
            task = asyncio.create_task(self.h.working(**SAVE))
            try:
                self.assertTrue(await asyncio.to_thread(reached.wait, 5))
                if stopped:
                    self.h.first.stop_event()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                closed = asyncio.create_task(asyncio.to_thread(close))
                self.assertTrue(await asyncio.to_thread(closing.wait, 5))
                self.assertFalse(closed.done())
            finally:
                release.set()
            await asyncio.wait_for(closed, 5)
        self.h.reopen()
        self.assertEqual(len(await self.h.items()), 0 if stopped else 1)
        replay = await self.h.working(event(), **SAVE)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["replayed"], not stopped)

    async def check_kill(self, stage):
        context = multiprocessing.get_context("spawn")
        reached, release = context.Event(), context.Event()
        process = context.Process(
            target=working_writer, args=(self.h.folder, stage, reached, release)
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
        state = WorkingState(self.h.service.store)
        before = await state.snapshot("crash-owner", authorize=lambda: True)
        self.assertEqual(len(before["items"]), int(stage == "after_commit"))
        source = event(message_id="process-loss", received_ns=200)
        result = await state.change(
            owner="crash-owner",
            change=SAVE,
            source=state.store.core_event_source(source),
            scopes=[
                {"session_id": QQ, "platform": "qq", "user_id": "owner", "bot_id": "qq"}
            ],
            authorize=lambda: True,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["replayed"], stage == "after_commit")
        self.assertEqual(
            (await state.snapshot("crash-owner", authorize=lambda: True))["items"][0][
                "revision"
            ],
            1,
        )
        self.assertEqual(
            state.store._conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
        )
