"""Original callback behaviors now exercise the native database authority."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_ada_companion.dialogue import DialogueState
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_memory_companion.core.working_state import WorkingState

from maintenance.tests.working_state_support import incoming, native_dialogue, save

QQ = "qq:FriendMessage:owner"


class DialogueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "dialogue.json"
        owners = OwnerMemory(self.path.with_name("owners.json"), [QQ])
        self.state = native_dialogue(self.path, self.addCleanup, owners)

    async def save(self, **kwargs):
        return await save(
            self.state,
            QQ,
            "这个梗以后就叫周日特种兵",
            action="save",
            kind="shared_joke",
            title="周日特种兵",
            content="双方约定的玩笑，不是服役经历",
            evidence_quote=kwargs.pop("evidence_quote", "这个梗以后就叫周日特种兵"),
            now=100,
            **kwargs,
        )

    async def test_callback_survives_reload_with_source_and_kind(self):
        self.assertTrue((await self.save())["ok"])
        restored = await DialogueState(
            self.path, self.state.owners, self.state.services
        ).visible(QQ, 200)
        self.assertEqual(restored[0]["kind"], "shared_joke")
        self.assertEqual(restored[0]["evidence_quote"], "这个梗以后就叫周日特种兵")
        self.assertEqual(restored[0]["source"]["kind"], "user_message")

    async def test_stranger_and_other_platform_cannot_read_or_close(self):
        saved = await self.save()
        self.assertFalse(
            (await self.state.snapshot("wechat:FriendMessage:owner"))["ok"]
        )
        denied = await self.state.update(
            incoming(self.state, "qq:FriendMessage:stranger", "别再提这事"),
            action="resolve",
            item_id=saved["id"],
            evidence_quote="别再提这事",
            now=200,
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(len(await self.state.visible(QQ, 200)), 1)

    async def test_invented_evidence_is_not_saved(self):
        self.assertFalse((await self.save(evidence_quote="用户当过兵"))["ok"])
        self.assertEqual(await self.state.visible(QQ, 200), [])
        self.assertFalse(self.path.exists())

    async def test_resolution_removes_callback_from_future_context(self):
        saved = await self.save()
        result = await save(
            self.state,
            QQ,
            "这个梗别再提了",
            action="resolve",
            item_id=saved["id"],
            evidence_quote="这个梗别再提了",
            now=200,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(await self.state.visible(QQ, 300), [])

    async def test_expired_callback_is_not_revived_by_read(self):
        await self.save()
        self.assertEqual(await self.state.visible(QQ, 31 * 86400), [])

    async def test_failed_persistence_does_not_claim_or_keep_memory(self):
        with patch.object(WorkingState, "write", side_effect=OSError("disk failure")):
            self.assertFalse((await self.save())["ok"])
        self.assertEqual(await self.state.visible(QQ, 200), [])

    async def test_corrupt_file_is_preserved(self):
        self.path.write_text("broken", encoding="utf-8")
        self.assertFalse((await self.save())["ok"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken")

    async def test_valid_json_with_invalid_shape_is_preserved_and_disabled(self):
        for contents in ("[]", '{"version":1,"sessions":{"qq:owner":[{}]}}'):
            with self.subTest(contents=contents):
                self.path.write_text(contents, encoding="utf-8")
                self.assertFalse((await self.state.snapshot(QQ))["ok"])
                self.assertFalse(self.state.healthy)
                self.assertEqual(self.path.read_text(encoding="utf-8"), contents)
