"""Actual state reads across elapsed time, context changes and retrieval expansion."""

import asyncio
import copy
import functools
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from appraisal_support import LATER, AppraisalHarness
from astrbot_plugin_ada_companion.appraisals import EventUnderstanding
from astrbot_plugin_ada_companion.lifecycle import Lifecycle
from astrbot_plugin_ada_companion.main import AdaCompanion
from astrbot_plugin_memory_companion.core.appraisal_state import AppraisalState
from mcp.types import CallToolResult, TextContent
from support import QQ, WX, event

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.provider.register import llm_tools
from astrbot.core.star.context import Context


class AppraisalAttentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = AppraisalHarness(self)

    async def distractions(self, count=9):
        for i in range(count):
            text = f"今日快递记录{i}：包裹已放门卫。"
            receipt = await self.h.change(
                event(text=text, message_id=f"parcel-{i}"),
                title=f"快递{i}",
                understanding="包裹暂存门卫。",
                response_implication="被问到时可以说明保管处。",
                uncertainty="",
                evidence_quote=text,
            )
            self.assertTrue(receipt["ok"], receipt)

    async def lookup(self, source, **kwargs):
        self.h.links.observe(source)
        return json.loads(
            await self.h.tools.threads(source, action="interpretations", **kwargs)
        )

    def stored(self):
        db = self.h.service.store._conn
        return {
            table: [
                tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1,2")
            ]
            for table in (
                "appraisal_objects",
                "appraisal_revisions",
                "appraisal_operations",
            )
        }

    async def test_current_cue_recovers_earlier_event_outside_recent_eight(self):
        target = await self.h.change()
        await self.distractions()
        recent = await self.h.snapshot()
        self.assertNotIn(target["id"], [r["id"] for r in recent["items"]])
        source = event(WX, "值夜班时说的山歌，现在怎么看？", "ask")
        self.h.links.observe(source)
        await self.h.appraisals.prompt(source)
        items = list(source.get_extra("ada_appraisals_observed").values())
        self.assertEqual(items[0]["id"], target["id"])
        self.assertGreater(items[0]["accessibility"]["current_cue"], 0)

    async def test_offline_and_many_continuous_reads_match_without_mutation(self):
        target = await self.h.change()
        item = (await self.h.snapshot())["items"][0]
        before = self.stored()
        at = item["updated_at"] + 180 * 86400
        for days in (1, 3, 7, 30, 90, 180):
            await self.h.appraisals.snapshot(
                QQ, mode="attention", now=item["updated_at"] + days * 86400
            )
        continuous = await self.h.appraisals.snapshot(QQ, mode="attention", now=at)
        self.h.reopen()
        offline = await self.h.appraisals.snapshot(QQ, mode="attention", now=at)
        self.assertEqual(offline, continuous)
        self.assertEqual(offline["items"], [])
        recalled = await self.h.appraisals.snapshot(
            QQ, mode="attention", query="山歌", now=at
        )
        self.assertEqual(recalled["items"][0]["id"], target["id"])
        self.assertEqual(before, self.stored())
        self.assertEqual(offline["total"], 1)

    async def test_resolved_event_fades_faster_without_becoming_invalid(self):
        active = await self.h.change()
        text = "饭局时他看手机，原来在联系水管维修。"
        resolved = await self.h.change(
            event(text=text, message_id="dinner"),
            status="resolved",
            title="饭局误会",
            understanding="看手机有维修原因，误会已解除。",
            response_implication="可聊再约吃饭。",
            evidence_quote=text,
        )
        now = time.time() + 20 * 86400
        values = await self.h.appraisals.snapshot(QQ, mode="search", now=now)
        traces = {r["id"]: r["accessibility"] for r in values["items"]}
        self.assertGreater(
            traces[active["id"]]["freshness"], traces[resolved["id"]]["freshness"]
        )
        self.assertEqual(values["total"], 2)

    async def test_new_process_same_time_and_cues_match_exactly(self):
        await self.h.change()
        await self.distractions()
        at = time.time() + 90 * 86400
        expected = await self.h.appraisals.snapshot(
            QQ, mode="attention", query="山歌 值夜班", now=at
        )
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-B",
                str(Path(__file__).with_name("attention_reopen.py")),
                str(self.h.folder / "memory.db"),
                self.h.links.shared_scope,
                str(at),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            env={**os.environ, "PYTHONHASHSEED": "random", "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(
            next(
                line[7:]
                for line in result.stdout.splitlines()
                if line.startswith("RESULT:")
            )
        )
        self.assertEqual(actual, expected)

    async def test_goal_cues_recover_old_event_but_closed_goal_is_not_seed(self):
        target = await self.h.change()
        goal = {
            "kind": "goal",
            "status": "open",
            "title": "山歌版本",
            "content": "比较山歌录音",
        }
        at = time.time() + 365 * 86400
        state = await self.h.appraisals.snapshot(
            QQ, mode="attention", goals=EventUnderstanding.goal_cues([goal]), now=at
        )
        self.assertEqual(state["items"][0]["id"], target["id"])
        goal["status"] = "resolved"
        state = await self.h.appraisals.snapshot(
            QQ, mode="attention", goals=EventUnderstanding.goal_cues([goal]), now=at
        )
        self.assertEqual(state["items"], [])

    async def test_explicit_lookup_crosses_attention_cutoff_and_preserves_original(
        self,
    ):
        target = await self.h.change()
        state = await self.h.appraisals.snapshot(
            QQ, mode="search", query="山歌", now=time.time() + 3000 * 86400
        )
        self.assertEqual(state["items"][0]["id"], target["id"])
        self.assertEqual(state["items"][0]["revision"], 1)
        self.assertEqual(
            state["items"][0]["basis"],
            "model_interpretation_of_user_report_not_observed_fact",
        )
        unrelated = await self.h.appraisals.snapshot(
            QQ, mode="search", query="线性代数"
        )
        self.assertEqual(unrelated["items"], [])

    async def test_lookup_pagination_keeps_observed_versions_and_stable_time_order(
        self,
    ):
        await self.h.change()
        await self.distractions(10)
        source = event(WX, "找之前的事", "page")
        first = await self.lookup(source)
        self.assertEqual(first["next_offset"], 8)
        self.h.reopen()
        second = await self.h.appraisals.snapshot(
            WX,
            event=source,
            mode="search",
            offset=8,
            revision=first["revision"],
            now=time.time() + 86400,
        )
        self.assertEqual(len(source.get_extra("ada_appraisals_observed")), 11)
        self.assertEqual(second["revision"], first["revision"])
        self.assertIsNone(second["next_offset"])
        self.assertFalse(
            {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
        )

    async def test_cursor_rejects_changed_query_or_state_or_missing_binding(self):
        await self.h.change()
        await self.distractions()
        source = event(text="查看旧解释", message_id="cursor")
        first = await self.lookup(source)
        for kwargs in (
            {"offset": 8},
            {"offset": 8, "revision": first["revision"], "query": "快递"},
        ):
            self.assertFalse((await self.lookup(source, **kwargs))["ok"])
        await self.h.change(
            event(text=LATER, message_id="later-new"), evidence_quote=LATER
        )
        self.assertEqual(
            (await self.lookup(source, offset=8, revision=first["revision"]))["error"],
            "appraisal_search_changed",
        )

    async def test_registered_tool_expands_cues_then_revises_original_id(self):
        target = await self.h.change()
        await self.distractions()
        source = event(WX, LATER, "correction")
        self.h.links.observe(source)
        plugin = object.__new__(AdaCompanion)
        plugin.lifecycle, plugin.memory = Lifecycle(), self.h.tools
        tool = copy.copy(
            next(t for t in llm_tools.func_list if t.name == "ada_threads")
        )
        tool.handler = functools.partial(AdaCompanion.threads, plugin)
        context = ContextWrapper(
            context=AstrAgentContext(context=object.__new__(Context), event=source)
        )
        responses = [
            r
            async for r in FunctionToolExecutor.execute(
                tool, context, action="interpretations", query="山歌"
            )
            if r
        ]
        result = responses[0]
        assert isinstance(result, CallToolResult)
        content = result.content[0]
        assert isinstance(content, TextContent)
        found = json.loads(content.text)
        self.assertEqual(found["items"][0]["id"], target["id"])
        receipt = await self.h.change(
            source, read=False, item_id=target["id"], evidence_quote=LATER
        )
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(receipt["revision"], 2)
        self.assertEqual((await self.h.snapshot())["total"], 10)

    async def test_lookup_still_rejects_private_boundary_and_withdrawn_source(self):
        capture, original = self.h.capture(self.h.first)
        await self.h.change()
        for source in (event("qq:FriendMessage:stranger"), event()):
            if source.unified_msg_origin == QQ:
                source.set_extra("ada_web_read_active", True)
            self.assertFalse((await self.lookup(source, query="山歌"))["ok"])
        capture.withdraw(original["source_key"])
        self.assertEqual((await self.lookup(event(WX), query="山歌"))["items"], [])

    async def test_invalid_selection_parameters_fail_without_writes(self):
        await self.h.change()
        before = self.stored()
        for options in (
            {"now": float("nan")},
            {"query": "x" * 2001},
            {"mode": "unknown"},
            {"offset": -1},
            {"revision": 3},
        ):
            result = await self.h.appraisals.snapshot(QQ, **options)
            self.assertFalse(result["ok"], result)
        self.assertEqual(before, self.stored())

    async def test_reauthorization_after_selection_prevents_return(self):
        await self.h.change()
        checks = iter((True, False))
        with self.assertRaisesRegex(ValueError, "appraisal_scope_changed"):
            await AppraisalState(self.h.service.store).snapshot(
                self.h.links.shared_scope,
                authorize=lambda: next(checks),
                query="山歌",
                mode="search",
            )
