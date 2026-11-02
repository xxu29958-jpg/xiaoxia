import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_ada_companion.dialogue import DialogueState


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "dialogue.json"
        self.state = DialogueState(self.path)

    def save(self, **kwargs):
        return self.state.update(
            "qq:owner",
            "这个梗以后就叫周日特种兵",
            action="save",
            kind="shared_joke",
            title="周日特种兵",
            content="双方约定的玩笑，不是服役经历",
            evidence_quote=kwargs.pop("evidence_quote", "这个梗以后就叫周日特种兵"),
            now=100,
            **kwargs,
        )

    def test_callback_survives_reload_with_source_and_kind(self):
        self.assertTrue(self.save()["ok"])
        restored = DialogueState(self.path).visible("qq:owner", 200)
        self.assertEqual(restored[0]["kind"], "shared_joke")
        self.assertEqual(restored[0]["evidence_quote"], "这个梗以后就叫周日特种兵")

    def test_stranger_and_other_platform_cannot_read_or_close(self):
        saved = self.save()
        self.assertEqual(self.state.visible("wechat:owner", 200), [])
        denied = self.state.update(
            "qq:stranger",
            "别再提这事",
            action="resolve",
            item_id=saved["id"],
            evidence_quote="别再提这事",
            now=200,
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(len(self.state.visible("qq:owner", 200)), 1)

    def test_invented_evidence_is_not_saved(self):
        self.assertFalse(self.save(evidence_quote="用户当过兵")["ok"])
        self.assertFalse(self.path.exists())

    def test_resolution_removes_callback_from_future_context(self):
        saved = self.save()
        self.state.update(
            "qq:owner",
            "这个梗别再提了",
            action="resolve",
            item_id=saved["id"],
            evidence_quote="这个梗别再提了",
            now=200,
        )
        self.assertEqual(DialogueState(self.path).visible("qq:owner", 300), [])

    def test_expired_callback_is_not_revived_by_read(self):
        self.save()
        self.assertEqual(self.state.visible("qq:owner", 31 * 86400), [])

    def test_failed_persistence_does_not_claim_or_keep_memory(self):
        with (
            patch.object(Path, "replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            self.save()
        self.assertEqual(self.state.visible("qq:owner", 200), [])

    def test_corrupt_file_is_preserved(self):
        self.path.write_text("broken", encoding="utf-8")
        self.state = DialogueState(self.path)
        self.assertFalse(self.save()["ok"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken")

    def test_valid_json_with_invalid_shape_is_preserved_and_disabled(self):
        for contents in ("[]", '{"version":1,"sessions":{"qq:owner":[{}]}}'):
            with self.subTest(contents=contents):
                self.path.write_text(contents, encoding="utf-8")
                state = DialogueState(self.path)
                self.assertFalse(state.healthy)
                self.assertEqual(state.visible("qq:owner"), [])
                self.assertEqual(self.path.read_text(encoding="utf-8"), contents)


if __name__ == "__main__":
    unittest.main()
