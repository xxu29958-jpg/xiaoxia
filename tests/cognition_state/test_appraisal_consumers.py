"""Final native JSON reaches persistent state and a later native prompt/history."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from appraisal_support import FIRST, LATER, SAVE, AppraisalHarness
from astrbot_plugin_ada_companion.chat_replyer import ChatReplyer
from astrbot_plugin_ada_companion.policy import DeliveryPolicy
from astrbot_plugin_ada_companion.prompting import ContextBuilder
from astrbot_plugin_memory_companion.core.models import EntityRef, MemoryRecord
from support import QQ, WX, event

from astrbot.core.agent.message import Message, TextPart
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


class AppraisalConsumerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.h = AppraisalHarness(self)
        self.config = {"chat_replyer_enabled": True, "owner_sessions": [QQ, WX]}
        self.h.links.compose = AsyncMock(return_value={})
        self.context = SimpleNamespace(
            conversation_manager=None,
            persona_manager=SimpleNamespace(
                resolve_selected_persona=AsyncMock(
                    return_value=("Ada", {"prompt": "Ada"}, None, False)
                )
            ),
        )
        self.h.tools.services.plugin = lambda _: None
        policy = DeliveryPolicy(self.h.folder / "policy.json", QQ)
        self.builder = ContextBuilder(
            self.config,
            self.h.links,
            self.h.dialogue,
            policy,
            self.context,
            self.h.tools.services,
        )
        self.replyer = ChatReplyer(
            self.context, self.config, self.h.links, None, self.h.dialogue
        )

    async def prepare(self, source):
        self.h.links.observe(source)
        req = ProviderRequest(
            prompt=source.get_message_str(), system_prompt="Original persona."
        )
        await self.builder.prepare(source, req)
        await self.replyer.prepare(source, req)
        return req

    async def reply(self, source, change):
        response = LLMResponse(
            role="assistant",
            completion_text=json.dumps(
                {"reply": "好，先让你安静值班。", "appraisal_updates": [change]},
                ensure_ascii=False,
            ),
        )
        await self.replyer.compose(source, response)
        return response

    async def test_native_context_uses_current_query_after_unrelated_events(self):
        target = await self.h.change()
        for i in range(9):
            text = f"包裹{i}暂存在门卫。"
            await self.h.change(
                event(text=text, message_id=f"parcel-{i}"),
                title="包裹",
                understanding="快递暂存门卫。",
                response_implication="问到再提。",
                evidence_quote=text,
                uncertainty="",
            )
        source = event(WX, "山歌那个版本，现在你怎么理解？", "recall")
        request = await self.prepare(source)
        observed = source.get_extra("ada_appraisals_observed")
        self.assertEqual(next(iter(observed)), target["id"])
        self.assertIn("action='interpretations'", request.system_prompt)

    async def test_reply_persists_only_interpretation_metadata_not_spoken_history(self):
        request = await self.prepare(self.h.first)
        response = await self.reply(self.h.first, SAVE)
        receipt = self.h.first.get_extra("ada_appraisal_receipts")[0]
        self.assertTrue(receipt["current"], receipt)
        run = SimpleNamespace(
            messages=[Message(role="assistant", content=[TextPart(text="envelope")])]
        )
        self.replyer.sync_history(self.h.first, run)
        self.assertEqual(run.messages[-1].content[-1].text, response.completion_text)
        self.assertNotIn("appraisal", response.completion_text)
        self.assertEqual(self.replyer.last["extra_model_calls"], 0)
        self.assertFalse(self.replyer.last["appraisal_save_failed"])
        self.assertTrue(request.system_prompt.startswith("Original persona."))
        self.h.reopen()
        next_event = event(WX, "那件听歌的事你怎么理解？", "next")
        next_request = await self.prepare(next_event)
        self.assertIn(FIRST, next_request.system_prompt)
        self.assertIn(SAVE["understanding"], next_request.system_prompt)
        self.assertEqual(
            next_event.get_extra("ada_appraisals_observed")[receipt["id"]]["version"],
            receipt["version"],
        )
        self.assertEqual((await self.h.snapshot())["items"][0]["revision"], 1)

    async def test_revision_changes_following_prompt_retains_original_source(self):
        first = await self.h.change()
        correction = event(WX, LATER, "correction")
        await self.prepare(correction)
        await self.reply(
            correction,
            {
                **SAVE,
                "item_id": first["id"],
                "evidence_quote": LATER,
                "understanding": "烦是临时加活，仍想听歌。",
                "uncertainty": "",
                "response_implication": "下班后可以继续那个版本。",
                "status": "resolved",
            },
        )
        next_request = await self.prepare(
            event(text="继续那个吧。", message_id="continue")
        )
        self.assertIn("烦是临时加活，仍想听歌。", next_request.system_prompt)
        self.assertIn(FIRST, next_request.system_prompt)
        self.assertNotIn(SAVE["uncertainty"], next_request.system_prompt)
        self.assertEqual((await self.h.snapshot())["total"], 1)

    async def test_internal_save_failure_keeps_chat_natural_and_diagnostic_truthful(
        self,
    ):
        await self.prepare(self.h.first)
        response = await self.reply(
            self.h.first, {**SAVE, "evidence_quote": "不是本轮原话"}
        )
        self.assertEqual(response.completion_text, "好，先让你安静值班。")
        self.assertEqual(
            self.h.first.get_extra("ada_spoken_reply"), response.completion_text
        )
        self.assertTrue(self.replyer.last["appraisal_save_failed"])
        self.assertFalse(self.h.first.get_extra("ada_appraisal_receipts")[0]["ok"])
        run = SimpleNamespace(
            messages=[Message(role="assistant", content=[TextPart(text="envelope")])]
        )
        self.replyer.sync_history(self.h.first, run)
        self.assertEqual(run.messages[-1].content[-1].text, response.completion_text)
        self.assertEqual((await self.h.snapshot())["total"], 0)

    async def test_feedback_revises_its_context_once_without_rewriting_facts(self):
        fact_id = await self.h.service.store.insert_memory(
            MemoryRecord(
                subject=EntityRef(kind="user", id="owner"),
                scope="private",
                session_id=QQ,
                content="喜欢山歌，也喜欢散步。",
                evidence="之前独立确认的合成事实",
                confidence=0.7,
                metadata={"mention_feedback": {"accepted_count": 2}},
            )
        )
        first = await self.h.change()
        text = "不是不喜欢山歌，只是在上班时别催。等我到家还想继续听。"
        source = event(WX, text, "timing-feedback")
        self.h.capture(source)
        await self.prepare(source)
        change = {
            **SAVE,
            "item_id": first["id"],
            "understanding": "希望上班时不被催促听歌，回家后仍愿意听。",
            "response_implication": "工作时留空间，到家后可承接听歌；不推为讨厌山歌。",
            "uncertainty": "尚不知道何时到家。",
            "evidence_quote": text,
        }
        await self.reply(source, change)
        receipt = source.get_extra("ada_appraisal_receipts")[0]
        self.assertTrue(receipt["current"], receipt)
        await self.reply(source, change)
        self.assertTrue(source.get_extra("ada_appraisal_receipts")[0]["replayed"])
        self.h.reopen()
        state = await self.h.snapshot()
        self.assertEqual(state["total"], 1)
        self.assertEqual(state["items"][0]["revision"], 2)
        next_request = await self.prepare(
            event(QQ, "我到家了，刚才那首山歌呢？", "home")
        )
        self.assertIn(change["understanding"], next_request.system_prompt)
        self.assertIn(change["response_implication"], next_request.system_prompt)
        fact = await self.h.service.store.get_memory(fact_id)
        assert fact is not None
        self.assertEqual(fact.content, "喜欢山歌，也喜欢散步。")
        self.assertEqual(fact.confidence, 0.7)
        self.assertEqual(fact.metadata, {"mention_feedback": {"accepted_count": 2}})
