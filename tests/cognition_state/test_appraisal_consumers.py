"""Final native JSON reaches persistent state and a later native prompt/history."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from appraisal_support import FIRST, LATER, SAVE, AppraisalHarness
from astrbot_plugin_ada_companion.chat_replyer import ChatReplyer
from astrbot_plugin_ada_companion.policy import DeliveryPolicy
from astrbot_plugin_ada_companion.prompting import ContextBuilder
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

    async def test_failed_persistence_is_visible_without_claiming_saved_or_new_evidence(
        self,
    ):
        await self.prepare(self.h.first)
        response = await self.reply(
            self.h.first, {**SAVE, "evidence_quote": "不是本轮原话"}
        )
        self.assertIn("没能保存", response.completion_text)
        self.assertEqual((await self.h.snapshot())["total"], 0)
