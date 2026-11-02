from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_anysearch.client import AnySearchClient, AnySearchError


class Response:
    def __init__(self, status, content):
        self.status = status
        self.body = content
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        yield self.body


class RouteTests(unittest.IsolatedAsyncioTestCase):
    def session(self, responses):
        calls = []

        class Session(Response):
            def __init__(self, **kwargs):
                pass

            def post(self, url, **kwargs):
                calls.append(kwargs.get("proxy"))
                value = responses[len(calls) - 1]
                if isinstance(value, Exception):
                    raise value
                return value

        return Session, calls

    async def test_connection_failure_falls_back_once(self):
        ok = Response(
            200,
            json.dumps(
                {"result": {"content": [{"type": "text", "text": "found"}]}}
            ).encode(),
        )
        session, calls = self.session(
            [aiohttp.ClientConnectionError("private proxy detail"), ok]
        )
        client = AnySearchClient(proxy="primary", fallback_proxy="fallback")
        with patch("astrbot_plugin_anysearch.client.aiohttp.ClientSession", session):
            self.assertEqual(await client.search("public term"), "found")
        self.assertEqual(calls, ["primary", "fallback"])
        self.assertEqual(client.last_route, "mainland_fallback")

    async def test_auth_quota_and_rate_limit_errors_do_not_change_routes_or_leak_body(
        self,
    ):
        for code in (401, 402, 403, 429):
            with self.subTest(code=code):
                session, calls = self.session(
                    [Response(code, b'{"api_key":"must-not-leak"}')]
                )
                client = AnySearchClient(proxy="primary", fallback_proxy="fallback")
                with patch(
                    "astrbot_plugin_anysearch.client.aiohttp.ClientSession", session
                ):
                    with self.assertRaises(AnySearchError) as raised:
                        await client.search("public term")
                self.assertNotIn("must-not-leak", str(raised.exception))
                self.assertEqual(calls, ["primary"])

    async def test_oversize_response_stops_without_another_download(self):
        session, calls = self.session([Response(200, b"x" * 2_000_001)])
        client = AnySearchClient(proxy="primary", fallback_proxy="fallback")
        with patch("astrbot_plugin_anysearch.client.aiohttp.ClientSession", session):
            with self.assertRaisesRegex(AnySearchError, "size limit"):
                await client.search("public term")
        self.assertEqual(calls, ["primary"])


if __name__ == "__main__":
    unittest.main()
