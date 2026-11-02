import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_ada_companion import online_memes as online
from astrbot_plugin_ada_companion.meme_sources import MAX_BYTES


def picture():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {"online_meme_enabled": True, "online_meme_daily_limit": 2}
        self.collector = online.OnlineMemes(self.root, self.config)

    async def test_unknown_private_chat_cannot_collect(self):
        from astrbot_plugin_ada_companion.web_tools import WebTools

        plugin = object.__new__(WebTools)
        plugin.config = {"owner_sessions": ["wechat:owner"]}
        from astrbot_plugin_ada_companion.owner_memory import OwnerMemory

        plugin.owners = OwnerMemory(
            Path(self.temp.name) / "owners.json", ["wechat:owner"]
        )
        plugin.owner_memory = plugin.owners
        event = SimpleNamespace(
            unified_msg_origin="qq:stranger",
            get_platform_name=lambda: "qq_official",
            is_private_chat=lambda: True,
        )
        result = json.loads(
            await plugin.collect_online_meme(event, "https://github.com/a")
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "private_owner_only")

    def test_private_urls_credentials_and_untrusted_hosts_are_rejected(self):
        for url in [
            "http://github.com/a.jpg",
            "file:///a.png",
            "https://localhost/x",
            "https://localhost.localdomain/x",
            "https://127.0.0.1/x",
            "https://198.18.0.1/x",
            "https://[::1]/x",
            "https://a.local/x",
            "https://user:secret@github.com/a",
            "https://github.com:6185/a",
            "https://github.com.evil.invalid/a.png",
        ]:
            with self.subTest(url=url), self.assertRaises(online.CollectionError):
                online.public_url(url, image=True)

    def test_page_parser_requires_real_image_references(self):
        url = "https://raw.githubusercontent.com/user/pack/main/a.png"
        page = json.dumps(
            {
                "content": f'![cat]({url} "cat") [not-an-image](https://github.com/x.jpg) ![private](https://127.0.0.1/a.png)'
            }
        )
        self.assertEqual(online.page_images(page), [url])

    def test_binary_validation(self):
        self.assertEqual(online.validate_image(picture()), ".png")
        for content in [b"<html>403</html>", b"x" * (MAX_BYTES + 1)]:
            with self.assertRaises(online.CollectionError):
                online.validate_image(content)

    def test_limit_survives_reload_and_disabled_makes_no_reservation(self):
        self.collector.reserve()
        reloaded = online.OnlineMemes(self.root, self.config)
        reloaded.reserve()
        with self.assertRaises(online.CollectionError):
            reloaded.reserve()
        self.assertEqual(reloaded.status()["attempts_today"], 2)
        self.config["online_meme_enabled"] = False
        with self.assertRaises(online.CollectionError):
            reloaded.reserve()
        self.assertEqual(reloaded.status()["attempts_today"], 2)

    async def test_redirect_to_private_host_is_never_requested(self):
        class Response:
            status = 302
            headers = {"Location": "https://127.0.0.1/private"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class Session(Response):
            calls = []

            def get(self, url, **kwargs):
                self.calls.append(url)
                return Response()

        session = Session()
        session.close = AsyncMock()
        with (
            patch.object(online.aiohttp, "ClientSession", return_value=session),
            patch(
                "astrbot_plugin_ada_companion.public_transport.PublicTransport._resolve_address",
                new=AsyncMock(return_value="93.184.216.34"),
            ),
        ):
            with self.assertRaises(online.CollectionError):
                await online.download_image("https://github.com/user/pack/raw/a.png")
        self.assertEqual(len(session.calls), 1)

    async def exercise_native_boundary(
        self, fail_index=False, is_meme=True, duplicate=False, confidence=0.99
    ):
        pack = {
            "pack_id": "test-pack",
            "pack_dir": self.root / "pack",
            "category_mapping": {"happy": "happy reaction"},
        }
        (pack["pack_dir"] / "memes" / "happy").mkdir(parents=True)
        old_file = pack["pack_dir"] / "memes" / "happy" / "old.png"
        old_file.write_bytes(b"preserve original")
        metadata = {"images": {"old": {"caption": "original"}}}
        persisted = copy.deepcopy(metadata)

        def save_metadata(root, value):
            persisted.clear()
            persisted.update(copy.deepcopy(value))

        def save_collected(root, destination, decision):
            persisted["images"]["new"] = {"caption": "new meme"}

        manager = SimpleNamespace(
            status=lambda _: {"index_ready": True, "worker_alive": False},
            begin_external_pack_operation=lambda *args: None,
            end_external_pack_operation=lambda *args: None,
            rebuild_index=AsyncMock(
                side_effect=[RuntimeError("embedding offline"), {}]
                if fail_index
                else None
            ),
        )
        meme = SimpleNamespace(
            semantic_task_manager=manager,
            semantic_vision_provider_id="vision",
            _resolve_runtime_pack_context=lambda **kwargs: pack,
        )
        decision = {
            "is_meme": is_meme,
            "meme_confidence": confidence,
            "category_confidence": 0.99,
            "category": "happy",
            "caption": "happy cat",
            "tags": ["happy"],
        }
        classifier = SimpleNamespace(_classify=AsyncMock(return_value=decision))
        storage = SimpleNamespace(
            load_metadata=lambda _: copy.deepcopy(persisted),
            reconcile_metadata=lambda _: copy.deepcopy(persisted),
            save_metadata=save_metadata,
            save_collected_image_semantic=save_collected,
        )
        modules = {
            "auto_collect": SimpleNamespace(
                AutoCollectManager=lambda *args: classifier
            ),
            "semantic.models": SimpleNamespace(
                REVIEW_CATEGORY="review", semantic_entry_id=lambda *args: "new"
            ),
            "semantic.storage": storage,
        }
        url = "https://raw.githubusercontent.com/user/pack/main/cat.png"
        with (
            patch.object(
                self.collector,
                "native",
                side_effect=lambda meme, suffix: modules[suffix],
            ),
            patch.object(
                self.collector,
                "candidate",
                side_effect=[{"id": "meme:old"}]
                if duplicate
                else [None, {"id": "meme:new"}],
            ),
            patch.object(online, "download_image", AsyncMock(return_value=picture())),
        ):
            try:
                result = await self.collector.collect(meme, None, None, url, url)
            except (RuntimeError, online.CollectionError):
                result = None
        self.assertEqual(old_file.read_bytes(), b"preserve original")
        self.assertEqual(persisted["images"]["old"], metadata["images"]["old"])
        return (
            result,
            persisted,
            manager,
            classifier,
            list(pack["pack_dir"].rglob("web-*")),
        )

    async def test_index_failure_rolls_back_only_new_addition(self):
        result, state, manager, _, files = await self.exercise_native_boundary(
            fail_index=True
        )
        self.assertIsNone(result)
        self.assertEqual(set(state["images"]), {"old"})
        self.assertEqual(files, [])
        self.assertEqual(manager.rebuild_index.await_count, 2)
        self.assertEqual(self.collector.status()["collected_total"], 0)

    async def test_non_meme_never_enters_library(self):
        result, state, manager, _, files = await self.exercise_native_boundary(
            is_meme=False
        )
        self.assertIsNone(result)
        self.assertEqual(files, [])
        manager.rebuild_index.assert_not_awaited()

    async def test_clear_animal_reaction_can_be_collected_but_uncertain_images_cannot(
        self,
    ):
        result, _, manager, _, files = await self.exercise_native_boundary(
            confidence=0.76
        )
        self.assertTrue(result["indexed"])
        self.assertEqual(len(files), 1)
        manager.rebuild_index.assert_awaited_once()

    async def test_low_confidence_never_enters_the_search_index(self):
        result, _, manager, _, files = await self.exercise_native_boundary(
            confidence=0.40
        )
        self.assertIsNone(result)
        self.assertEqual(files, [])
        manager.rebuild_index.assert_not_awaited()

    async def test_exact_duplicate_reuses_candidate_without_vision_or_index_write(self):
        result, _, manager, classifier, files = await self.exercise_native_boundary(
            duplicate=True
        )
        self.assertTrue(result["duplicate"])
        classifier._classify.assert_not_awaited()
        manager.rebuild_index.assert_not_awaited()
        self.assertEqual(files, [])

    async def test_success_records_source_and_indexes_before_confirmation(self):
        result, state, manager, _, files = await self.exercise_native_boundary()
        self.assertTrue(result["indexed"])
        self.assertEqual(len(files), 1)
        self.assertEqual(set(state["images"]), {"old", "new"})
        manager.rebuild_index.assert_awaited_once()
        self.assertEqual(self.collector.status()["collected_total"], 1)


if __name__ == "__main__":
    unittest.main()
