"""Actual request ingress must not turn reply reactions into unrelated evidence."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from astrbot_plugin_memory_companion.core.models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
)
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService
from support import QQ, event

from astrbot.core.provider.entities import ProviderRequest


class FeedbackAttributionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory(prefix="xiaoxia-feedback-")
        self.addCleanup(folder.cleanup)
        self.service = MemoryCompanionService(
            context=None,
            config={"retrieval": {"embedding_enabled": False}},
            plugin_root=Path(__file__).resolve().parents[2]
            / "data/plugins/astrbot_plugin_memory_companion",
            data_dir=Path(folder.name),
        )
        self.addCleanup(self.service.close)
        # Isolate request ingress from unrelated retrieval and deferred extraction.
        # The service, event, existing response timeline and memories remain real.
        self.service._spawn_background = Mock(
            side_effect=lambda coro, **_: coro.close()
        )
        self.service.inject_memories = AsyncMock()
        self.service._reply_chain_for_event = AsyncMock(return_value=None)
        self.service._update_address_evolution = Mock()
        self.ids = []
        for content in ("喜欢晚饭后听山歌。", "三年前在杭州住过。"):
            self.ids.append(
                await self.service.store.insert_memory(
                    MemoryRecord(
                        subject=EntityRef(kind="user", id="owner"),
                        scope="private",
                        session_id=QQ,
                        content=content,
                        evidence="独立合成原始经历",
                        confidence=0.5,
                    )
                )
            )
        await self.response(1)

    async def response(self, number):
        return await self.service.store.add_timeline_event(
            event_type="bot_response",
            session_id=QQ,
            scope="private",
            subject_id="self",
            object_id="owner",
            content="休息一下也好。",
            occurred_at=f"2026-11-04T00:00:{number:02}Z",
            metadata={
                "message_id": f"reply-{number}",
                "memory_companion_injection_state": {
                    "feedback_target_memory_ids": self.ids
                },
            },
        )

    async def ingress(self, text, message_id="feedback-1"):
        source = event(text=text, message_id=message_id)
        ctx = SessionContext(
            scope="private",
            session_id=QQ,
            user_id="owner",
            platform="qq",
            bot_id="qq",
            message_id=message_id,
            message_text=text,
        )
        self.service.identity.resolve_event_context = AsyncMock(return_value=ctx)
        request = ProviderRequest(prompt=text, system_prompt="Ada")
        await self.service.handle_llm_request(source, request)
        self.assertTrue(self.service._scope_feature_enabled(ctx, "capture"))

    async def records(self):
        rows = await self.service.store.get_memories_by_ids(self.ids)
        return {
            key: (r.content, r.evidence, r.confidence, r.metadata)
            for key, r in rows.items()
        }

    async def test_thanks_does_not_confirm_every_injected_memory(self):
        before = await self.records()
        await self.ingress("谢谢你。")
        self.assertEqual(await self.records(), before)

    async def test_unrelated_wording_is_not_acceptance_feedback(self):
        before = await self.records()
        await self.ingress("我在对面的车站。")
        self.assertEqual(await self.records(), before)

    async def test_timing_feedback_does_not_suppress_unrelated_memories(self):
        before = await self.records()
        await self.ingress("现在开会，先别提这个，等我到家再聊。")
        self.assertEqual(await self.records(), before)

    async def test_feedback_redelivery_after_a_reply_is_not_new_evidence(self):
        before = await self.records()
        await self.ingress("谢谢你。")
        await self.response(2)
        await self.ingress("谢谢你。")
        self.assertEqual(await self.records(), before)
