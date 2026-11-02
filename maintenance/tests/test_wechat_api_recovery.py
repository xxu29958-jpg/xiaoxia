"""Exercise real HTTP delivery boundaries: safe reconnect, never blind POST replay."""

from pathlib import Path

import asyncio
import socket
import sys
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_local_network import network_routes
from astrbot_plugin_local_network.wechat_api import request_wechat_json

from astrbot.core.platform.sources.weixin_oc.weixin_oc_client import WeixinOCClient


class WeChatApiRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.request_paths = []
        self.mode = "ok"

        async def receiver(request):
            self.request_paths.append(request.raw_path)
            self.received.append(await request.json())
            if self.mode == "disconnect":
                request.transport.close()
                return web.Response()
            if self.mode == "reject":
                return web.json_response({"error": "rejected"}, status=403)
            if self.mode == "slow":
                await asyncio.sleep(0.1)
            return web.json_response({"ret": 0, "accepted": True})

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", receiver)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.live_proxy = f"http://127.0.0.1:{port}"
        # Reserve an unlistening port throughout the test; no other process can bind it.
        self.refused = socket.socket()
        self.refused.bind(("127.0.0.1", 0))
        self.dead_proxy = f"http://127.0.0.1:{self.refused.getsockname()[1]}"
        self.client = WeixinOCClient(
            adapter_id="test",
            base_url=self.live_proxy,
            cdn_base_url="",
            api_timeout_ms=1000,
        )
        self.enterContext(
            patch.object(
                WeixinOCClient,
                "ensure_http_session",
                network_routes.ensure_wechat_proxy_session,
            )
        )
        self.enterContext(
            patch.object(
                network_routes,
                "WECHAT_ROUTES",
                (self.dead_proxy, None),
            )
        )
        # An inherited proxy must not turn a direct fallback into the same outage.
        self.enterContext(
            patch.dict(
                "os.environ",
                {
                    "http_proxy": self.dead_proxy,
                    "HTTP_PROXY": self.dead_proxy,
                    "https_proxy": self.dead_proxy,
                    "HTTPS_PROXY": self.dead_proxy,
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
            )
        )
        await self.client.ensure_http_session()
        self.addAsyncCleanup(self.client._http_session.close)
        self.addAsyncCleanup(self.runner.cleanup)
        self.addCleanup(self.refused.close)

    async def send(self, timeout=None):
        return await request_wechat_json(
            self.client,
            "POST",
            "/sendmessage",
            payload={"client_id": "stable-test-id", "text": "local-only"},
            timeout_ms=timeout,
        )

    async def test_connect_failure_delivers_once_on_fallback(self):
        result = await self.send()
        self.assertEqual(result, {"ret": 0, "accepted": True})
        self.assertEqual(
            self.received, [{"client_id": "stable-test-id", "text": "local-only"}]
        )
        self.assertEqual(self.request_paths, ["/sendmessage"])

    async def test_healthy_gateway_delivers_once_without_direct_retry(self):
        with patch.object(network_routes, "WECHAT_ROUTES", (self.live_proxy, None)):
            await self.send()
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.request_paths, [self.live_proxy + "/sendmessage"])

    async def test_unknown_or_rejected_delivery_is_never_replayed(self):
        for mode in ("disconnect", "reject", "slow"):
            with self.subTest(mode=mode):
                self.request_paths.clear()
                self.received.clear()
                self.mode = mode
                with patch.object(
                    network_routes, "WECHAT_ROUTES", (self.live_proxy, None)
                ):
                    with self.assertRaises(
                        (aiohttp.ClientError, RuntimeError, TimeoutError)
                    ):
                        await self.send(timeout=30 if mode == "slow" else 1000)
                self.assertEqual(len(self.received), 1)
                self.assertEqual(self.request_paths, [self.live_proxy + "/sendmessage"])


if __name__ == "__main__":
    unittest.main()
