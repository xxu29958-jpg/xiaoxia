"""Verify actual preview delivery and rejection of invalid local image evidence."""

import base64
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.provider.entities import LLMResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_meme_manager.backend.semantic.preview import visual_candidates


class VisualCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "reaction.gif"
        Image.new("RGB", (64, 64), "red").save(
            self.path,
            save_all=True,
            append_images=[Image.new("RGB", (64, 64), "blue")],
            duration=100,
            loop=0,
        )
        self.record = {
            "id": "meme:123456789abc",
            "entry_id": "entry",
            "content_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
            "caption": "An intentionally unreliable caption",
            "tags": [],
        }
        self.metadata = {"images": {"entry": {"relative_path": "reaction.gif"}}}

    def render(self):
        with patch(
            "astrbot_plugin_meme_manager.backend.semantic.preview.load_metadata",
            return_value=self.metadata,
        ):
            return visual_candidates(self.root, [self.record])

    def test_actual_frames_are_delivered_with_the_correct_candidate_label(self):
        result = self.render()
        self.assertEqual(
            json.loads(result.content[0].text)["candidates"][0]["id"], self.record["id"]
        )
        self.assertIn(self.record["id"], result.content[1].text)
        preview = Image.open(io.BytesIO(base64.b64decode(result.content[2].data)))
        self.assertEqual(preview.size, (640, 320))
        self.assertGreater(preview.getpixel((160, 160))[0], 240)
        self.assertGreater(preview.getpixel((480, 160))[2], 240)

    def test_changed_file_cannot_be_selected_using_stale_caption_evidence(self):
        self.path.write_bytes(b"changed")
        result = self.render()
        self.assertEqual(json.loads(result.content[0].text)["candidates"], [])
        self.assertEqual(len(result.content), 1)

    def test_outside_paths_are_not_read_or_attached(self):
        self.metadata["images"]["entry"]["relative_path"] = "../secret.jpg"
        self.assertEqual(json.loads(self.render().content[0].text)["candidates"], [])


class ImageOnlyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_selection_explanation_is_removed_only_with_current_valid_image(self):
        from astrbot_plugin_ada_companion.lifecycle import Lifecycle
        from astrbot_plugin_ada_companion.main import AdaCompanion
        from astrbot_plugin_ada_companion.response_media import ResponseMedia

        plugin = object.__new__(AdaCompanion)
        plugin.lifecycle = Lifecycle()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        plugin.media = ResponseMedia({}, Path(temp.name), None)
        values = {
            "ada_meme_only": True,
            "meme_manager_semantic_candidates": {"meme:123456789abc": {}},
        }
        event = SimpleNamespace(
            get_extra=lambda key, default=None: values.get(key, default)
        )
        response = LLMResponse(
            role="assistant",
            result_chain=MessageChain(
                [
                    Plain(
                        "I selected a happy picture with ID 123.\n&&meme:123456789abc&&"
                    )
                ]
            ),
        )
        await plugin.media.image_only_meme_reply(event, response)
        self.assertEqual(response.completion_text, "&&meme:123456789abc&&")
        self.assertEqual(
            response.result_chain.get_plain_text(), "&&meme:123456789abc&&"
        )

    async def test_failed_or_mixed_request_keeps_the_actual_answer(self):
        from astrbot_plugin_ada_companion.lifecycle import Lifecycle
        from astrbot_plugin_ada_companion.main import AdaCompanion
        from astrbot_plugin_ada_companion.response_media import ResponseMedia

        plugin = object.__new__(AdaCompanion)
        plugin.lifecycle = Lifecycle()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        plugin.media = ResponseMedia({}, Path(temp.name), None)
        for values, text in [
            ({"ada_meme_only": True}, "No suitable image is available."),
            ({"ada_meme_only": False}, "Here is the answer to the actual question."),
        ]:
            event = SimpleNamespace(
                get_extra=lambda key, default=None: values.get(key, default)
            )
            response = LLMResponse(
                role="assistant", result_chain=MessageChain([Plain(text)])
            )
            await plugin.media.image_only_meme_reply(event, response)
            self.assertEqual(response.completion_text, text)


if __name__ == "__main__":
    unittest.main()
