"""Cross-channel objects, stale model decisions and native replay boundaries."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_ada_companion.dialogue import DialogueState
from astrbot_plugin_memory_companion.core.working_state import WorkingState
from support import QQ, WX, event, open_service
from working_support import WorkingHarness


class WorkingStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = WorkingHarness(self)
        self.save = {
            "action": "save",
            "kind": "goal",
            "title": "练歌",
            "content": "先练完整歌曲，再讨论下一步",
            "evidence_quote": "喜欢自然说话",
        }

    async def test_goal_crosses_channels_days_and_restart_then_old_event_cannot_reopen(
        self,
    ):
        first = await self.h.working(**self.save)
        self.assertTrue(first["ok"], first)
        for _ in range(10):
            self.h.reopen()
            replay = await self.h.working(event(), **self.save)
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["version"], first["version"])
        item = (await self.h.items(now=9999999999))[0]
        self.assertEqual(
            (item["kind"], item["revision"], item["expires_at"]), ("goal", 1, 0)
        )
        self.assertEqual(item["source"]["message_id"], "q1")
        closed = await self.h.working(
            event(WX, "这首已经练完", "done"),
            action="resolve",
            item_id=item["id"],
            evidence_quote="已经练完",
        )
        self.assertTrue(closed["current"], closed)
        self.assertEqual(await self.h.items(), [])
        old = await self.h.working(event(), **self.save)
        self.assertFalse(old["current"])
        closed_item = (await self.h.items(include_closed=True))[0]
        self.assertEqual(
            closed_item["resolution_basis"], "source_report_not_execution_receipt"
        )
        self.assertEqual(closed_item["source"]["origin"], WX)
        self.assertEqual(
            len(
                (await self.h.working(action="history", item_id=item["id"]))["history"]
            ),
            2,
        )

    async def test_two_instances_and_connections_keep_both_inserts_and_reject_stale_edits(
        self,
    ):
        other = open_service(self.h.folder)
        self.addCleanup(other.store.close)
        adapter = DialogueState(
            self.h.dialogue.path,
            self.h.links,
            SimpleNamespace(memory=lambda: SimpleNamespace(_plugin=other)),
        )
        source = event(WX, "这本书下次继续聊", "second")
        self.h.links.observe(source)
        first, second = await asyncio.gather(
            self.h.working(**self.save),
            adapter.update(
                source,
                action="save",
                title="书",
                content="下次继续聊这本书",
                evidence_quote="下次继续聊",
            ),
        )
        self.assertTrue(first["ok"] and second["ok"], (first, second))
        self.assertEqual(len(await self.h.items()), 2)
        a, b = event(QQ, "改为先练低音", "a"), event(WX, "改为先练高音", "b")
        for e in (a, b):
            self.h.links.observe(e)
            await adapter.snapshot(e.unified_msg_origin, event=e)
        edit = {**self.save, "item_id": first["id"]}
        left, right = await asyncio.gather(
            self.h.dialogue.update(
                a, **{**edit, "content": "先练低音", "evidence_quote": "先练低音"}
            ),
            adapter.update(
                b, **{**edit, "content": "先练高音", "evidence_quote": "先练高音"}
            ),
        )
        self.assertEqual(sum(r["ok"] for r in (left, right)), 1)
        self.assertIn(
            "working_version_conflict", [r.get("error") for r in (left, right)]
        )

    async def test_late_source_and_unobserved_replacement_are_rejected(self):
        old = event(QQ, "先练低音", "old")
        self.h.links.observe(old)
        self.h.first = event(message_id="new")
        first = await self.h.working(**self.save)
        change = {
            **self.save,
            "item_id": first["id"],
            "content": "低音",
            "evidence_quote": "先练低音",
        }
        denied = await self.h.working(old, **change)
        self.assertEqual(denied["error"], "working_source_is_older")
        fresh = event(WX, "先练高音", "fresh")
        denied = await self.h.working(
            fresh, read=False, **{**change, "evidence_quote": "先练高音"}
        )
        self.assertEqual(denied["error"], "working_version_conflict")
        conflict = await self.h.working(**{**self.save, "content": "同一消息另一解释"})
        self.assertEqual(conflict["error"], "working_operation_conflict")

    async def test_goal_edits_without_a_kind_keep_the_goal_and_have_no_expiration(self):
        first = await self.h.working(**self.save)
        for index, item_id in enumerate((first["id"], "")):
            changed = await self.h.working(
                event(WX, "先练完第一段", f"edit-kind-{index}"),
                action="save",
                item_id=item_id,
                title="练歌",
                content="先练完第一段",
                evidence_quote="练完第一段",
            )
            self.assertTrue(changed["ok"], changed)
            self.assertEqual(changed["id"], first["id"])
        item = (await self.h.items(now=9999999999))[0]
        self.assertEqual((item["kind"], item["expires_at"]), ("goal", 0))

    async def test_forget_after_rename_keeps_replay_tombstone_but_not_old_text(self):
        first = await self.h.working(**self.save)
        renamed = await self.h.working(
            event(WX, "改成练整首曲子", "rename"),
            **{
                **self.save,
                "item_id": first["id"],
                "title": "整曲",
                "evidence_quote": "练整首曲子",
            },
        )
        self.assertTrue(renamed["ok"], renamed)
        forgotten = await self.h.working(
            event(WX, "忘掉这项", "forget"),
            action="forget",
            item_id=first["id"],
            evidence_quote="忘掉这项",
        )
        self.assertTrue(forgotten["ok"], forgotten)
        self.h.reopen()
        replay = await self.h.working(event(), **self.save)
        self.assertTrue(replay["replayed"])
        self.assertFalse(replay["current"])
        self.assertEqual(await self.h.items(include_closed=True), [])
        self.assertEqual(
            self.h.service.store._conn.execute(
                "SELECT count(*) FROM working_revisions"
            ).fetchone()[0],
            0,
        )
        new = await self.h.working(event(message_id="new"), **self.save)
        self.assertTrue(new["ok"], new)
        self.assertNotEqual(new["version"], first["version"])
        self.assertFalse((await self.h.working(event(), **self.save))["current"])

    async def test_atomic_rollback_and_identity_revocation_before_commit(self):
        original = WorkingState.write
        for failure in ("disk", "scope"):
            with self.subTest(failure=failure):

                def interrupted(port, record, **kwargs):
                    original(port, record, **kwargs)
                    if failure == "disk":
                        raise OSError("synthetic disk full")
                    self.h.links.owners = ()

                with patch.object(WorkingState, "write", interrupted):
                    denied = await self.h.working(**self.save)
                self.assertFalse(denied["ok"], denied)
                self.h.links.owners = (QQ, WX)
                self.assertEqual(await self.h.items(), [])
                self.assertEqual(
                    self.h.service.store._conn.execute(
                        "SELECT count(*) FROM working_operations"
                    ).fetchone()[0],
                    0,
                )
        self.assertTrue((await self.h.working(**self.save))["ok"])

    async def test_scopes_quotes_and_untrusted_context_do_not_create_state(self):
        for source in (event("qq:FriendMessage:stranger"), event()):
            if source.unified_msg_origin == QQ:
                source.set_extra("ada_web_read_active", True)
            self.assertFalse((await self.h.working(source, **self.save))["ok"])
        self.assertFalse(
            (await self.h.working(**{**self.save, "evidence_quote": "没有说过的话"}))[
                "ok"
            ]
        )
        admin = event("webchat:FriendMessage:astrbot")
        self.assertTrue((await self.h.working(admin, **self.save))["ok"])
        self.assertEqual(await self.h.items(), [])
        self.assertEqual(len((await self.h.working(admin))["items"]), 1)

    async def test_pagination_is_bounded_and_changed_query_must_restart(self):
        for index in range(14):
            self.assertTrue(
                (
                    await self.h.working(
                        event(message_id=str(index)),
                        **{**self.save, "title": f"目标 {index}"},
                    )
                )["ok"]
            )
        page = await self.h.working()
        self.assertEqual(
            (len(page["items"]), page["total"], page["next_offset"]), (12, 14, 12)
        )
        rest = await self.h.working(offset=12, revision=page["revision"])
        self.assertEqual(len(rest["items"]), 2)
        self.assertEqual(len({i["id"] for i in page["items"] + rest["items"]}), 14)
        self.assertFalse(
            (
                await self.h.working(
                    query="目标 1", offset=12, revision=page["revision"]
                )
            )["ok"]
        )
        item = page["items"][0]
        await self.h.working(
            event(text="取消这一项", message_id="cancel"),
            action="cancel",
            item_id=item["id"],
            evidence_quote="取消这一项",
        )
        self.assertFalse(
            (await self.h.working(offset=12, revision=page["revision"]))["ok"]
        )

    async def test_native_private_erasure_and_clear_all_do_not_reimport_or_replay(self):
        await self.h.working(**self.save)
        store = self.h.service.store
        preview = await store.preview_scoped_memory_clear(
            target_type="private", user_id="owner"
        )
        self.assertEqual(preview["counts"]["working_objects"], 1)
        await store.clear_scoped_memory(target_type="private", user_id="owner")
        self.assertEqual(await self.h.items(), [])
        self.assertFalse((await self.h.working(event(), **self.save))["current"])
        await self.h.working(event(message_id="second"), **self.save)
        result = await store.clear_all_memory_data()
        self.assertEqual(result["deleted"]["working_objects"], 1)
        self.assertEqual(await self.h.items(), [])

    async def test_source_projection_and_database_redaction(self):
        secret = "synthetic-not-a-real-credential"
        source = event(text="喜欢自然说话。" + "普通文字" * 1000 + "password=" + secret)
        receipt = await self.h.working(source, **self.save)
        self.assertTrue(receipt["ok"], receipt)
        item = (await self.h.items())[0]
        self.assertTrue(item["source"]["text_truncated"])
        self.assertEqual(len(item["source"]["text"]), 2000)
        for table in ("working_objects", "working_revisions", "working_operations"):
            self.assertNotIn(
                secret,
                str(
                    [
                        tuple(row)
                        for row in self.h.service.store._conn.execute(
                            "SELECT * FROM " + table
                        )
                    ]
                ),
            )
