"""Verify inbound media recovery through the native decryptor and message parser."""

import base64
import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from Crypto.Cipher import AES
from PIL import Image as PillowImage

from astrbot.api.message_components import Image, Plain
from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import WeixinOCAdapter
from astrbot.core.platform.sources.weixin_oc.weixin_oc_client import WeixinOCClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_local_network.main import LocalNetworkPlugin
from astrbot_plugin_local_network.wechat_media import UNSUPPORTED_MESSAGE_NOTICE


class Response:
    def __init__(self, value):
        self.value = value
        self.status = value if isinstance(value, int) else 200

    async def __aenter__(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return self.value if isinstance(self.value, bytes) else b""


class Session:
    closed = False

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(kwargs)
        return Response(self.responses.pop(0))


class WeChatMediaRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_download = WeixinOCClient.download_cdn_bytes
        self.original_resolver = WeixinOCAdapter._resolve_inbound_media_component
        self.original_message_text = WeixinOCAdapter._message_text_from_item_list
        self.original_handler = WeixinOCAdapter._handle_inbound_message
        self.plugin = object.__new__(LocalNetworkPlugin)
        await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = WeixinOCClient(
            adapter_id="test",
            base_url="https://ilinkai.weixin.qq.com",
            cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
            api_timeout_ms=15000,
        )
        self.adapter = object.__new__(WeixinOCAdapter)
        self.adapter.client = self.client
        self.adapter.meta = lambda: SimpleNamespace(id="test")

        def save(content, *, prefix, file_name, fallback_suffix):
            target = self.root / file_name
            target.write_bytes(content)
            return target

        self.adapter._save_inbound_media = save
        buffer = io.BytesIO()
        PillowImage.new("RGB", (32, 32), "red").save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=[PillowImage.new("RGB", (32, 32), "blue")],
            duration=100,
            loop=0,
        )
        self.raw = buffer.getvalue()
        key = b"test-media-key16"
        self.encrypted = AES.new(key, AES.MODE_ECB).encrypt(
            self.client.pkcs7_pad(self.raw)
        )
        self.item = {
            "type": 2,
            "image_item": {
                "media": {
                    "encrypt_query_param": "private-test-reference",
                    "aes_key": base64.b64encode(key).decode(),
                }
            },
        }

    async def test_failed_primary_recovers_actual_animated_image_component(self):
        session = Session(aiohttp.ClientConnectionError("offline"), self.encrypted)
        self.client._http_session = session
        parts = await self.adapter._item_list_to_components([self.item])
        self.assertEqual(len(parts), 1)
        self.assertIsInstance(parts[0], Image)
        target = Path(await parts[0].convert_to_file_path())
        self.assertEqual(target.read_bytes(), self.raw)
        with PillowImage.open(target) as image:
            self.assertEqual(image.n_frames, 2)
        self.assertEqual(
            [c["proxy"] for c in session.calls],
            ["http://127.0.0.1:17898", None],
        )

    async def test_both_routes_failed_do_not_silently_remove_the_image(self):
        self.client._http_session = Session(
            aiohttp.ClientConnectionError("private-test-reference"),
            TimeoutError(),
        )
        parts = await self.adapter._item_list_to_components([self.item])
        self.assertEqual(len(parts), 1)
        self.assertIsInstance(parts[0], Plain)
        self.assertIn("图片未能下载", parts[0].text)
        self.assertNotIn("private-test-reference", parts[0].text)

    async def test_authorization_failure_is_not_retried_on_another_route(self):
        session = Session(403)
        self.client._http_session = session
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            await self.client.download_cdn_bytes("private-test-reference")
        self.assertEqual(len(session.calls), 1)

    async def test_server_error_can_recover_once(self):
        session = Session(503, self.encrypted)
        self.client._http_session = session
        self.assertEqual(
            await self.client.download_and_decrypt_media(
                "private-test-reference", self.item["image_item"]["media"]["aes_key"]
            ),
            self.raw,
        )
        self.assertEqual(len(session.calls), 2)

    async def test_missing_media_reference_is_reported_without_network(self):
        session = Session()
        self.client._http_session = session
        parts = await self.adapter._item_list_to_components(
            [{"type": 2, "image_item": {}}]
        )
        self.assertIsInstance(parts[0], Plain)
        self.assertEqual(session.calls, [])

    async def test_healthy_primary_does_not_duplicate_download(self):
        session = Session(self.encrypted)
        self.client._http_session = session
        parts = await self.adapter._item_list_to_components([self.item])
        self.assertIsInstance(parts[0], Image)
        self.assertEqual(len(session.calls), 1)

    async def test_observed_unsupported_sticker_reaches_event_without_claiming_pixels(
        self,
    ):
        session = Session()
        self.client._http_session = session
        self.adapter._context_tokens = {}
        self.adapter._context_tokens_revision = 0
        self.adapter._context_tokens_dirty = False
        self.adapter._recent_messages = {}
        self.adapter._recent_message_cache_size = 10
        self.adapter._recent_session_cache_ttl_s = 1800
        self.adapter._max_recent_message_sessions = 10
        events = []
        self.adapter.create_event = lambda message: message
        self.adapter.commit_event = events.append
        message = {
            "message_id": "observed-shape-fixture",
            "from_user_id": "test-user",
            "context_token": "private-test-context",
            "message_type": 1,
            "message_state": 2,
            "item_list": [
                {
                    "type": 8,
                    "is_completed": True,
                    "unsupported_item": {"text": "untrusted-placeholder-text"},
                }
            ],
        }
        original = copy.deepcopy(message)
        await self.adapter._handle_inbound_message(message)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.message_str, UNSUPPORTED_MESSAGE_NOTICE)
        self.assertEqual(len(event.message), 1)
        self.assertIsInstance(event.message[0], Plain)
        self.assertEqual(event.message[0].text, UNSUPPORTED_MESSAGE_NOTICE)
        self.assertNotIn("untrusted-placeholder-text", event.message_str)
        self.assertNotIn("private-test-context", event.message_str)
        self.assertIs(event.raw_message, message)
        self.assertEqual(message, original)
        cached = self.adapter._recent_messages["test-user"].messages[-1]
        self.assertEqual(cached.message_str, UNSUPPORTED_MESSAGE_NOTICE)
        self.assertEqual(session.calls, [])

    async def test_mixed_text_and_sticker_preserve_original_text_and_order(self):
        items = [
            {"type": 1, "text_item": {"text": "[呲牙]这个好笑吗"}},
            {"type": 8, "unsupported_item": {"text": "unsupported"}},
        ]
        parts = await self.adapter._item_list_to_components(items)
        self.assertEqual(
            [p.text for p in parts], ["[呲牙]这个好笑吗", UNSUPPORTED_MESSAGE_NOTICE]
        )
        self.assertEqual(
            self.adapter._message_text_from_item_list(items),
            "[呲牙]这个好笑吗\n" + UNSUPPORTED_MESSAGE_NOTICE,
        )

    async def test_builtin_face_text_does_not_trigger_missing_image_notice(self):
        items = [{"type": 1, "text_item": {"text": "[猪头]"}}]
        parts = await self.adapter._item_list_to_components(items)
        self.assertEqual([p.text for p in parts], ["[猪头]"])
        self.assertEqual(self.adapter._message_text_from_item_list(items), "[猪头]")

    async def test_reload_restores_native_functions(self):
        await self.plugin.terminate()
        self.assertIs(WeixinOCClient.download_cdn_bytes, self.original_download)
        self.assertIs(
            WeixinOCAdapter._resolve_inbound_media_component, self.original_resolver
        )
        self.assertIs(
            WeixinOCAdapter._message_text_from_item_list, self.original_message_text
        )
        self.assertIs(WeixinOCAdapter._handle_inbound_message, self.original_handler)


if __name__ == "__main__":
    unittest.main()
