"""Exercise the installed OAuth provider through AstrBot's public chat methods."""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(str(Path(__file__).resolve().parents[2]))
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "data/plugins/astrbot_plugin_codex_oauth_plug"),
]

from aiohttp import web  # noqa: E402
from oauth_plug_openai_codex.provider import ProviderOAuthPlugOpenAICodex  # noqa: E402
from oauth_plug_openai_codex.service import (  # noqa: E402
    PROVIDER_TYPE,
    OpenAICodexOAuthService,
    get_service,
    set_service,
)


class ConversationCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_stream_and_concurrent_sessions(self):
        received = []

        async def respond(request):
            received.append(request.headers.get("x-astrbot-conversation-id"))
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for event in (
                {"type": "response.output_text.delta", "delta": "hello"},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "local-response",
                        "status": "completed",
                        "output": [],
                        "output_text": "hello",
                    },
                },
            ):
                await response.write(("data: " + json.dumps(event) + "\n\n").encode())
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/responses", respond)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        previous = get_service()
        service = OpenAICodexOAuthService(
            {
                "runtime": {"base_url": f"http://127.0.0.1:{port}"},
                "oauth": {
                    "oauth_access_token": "local-test-token",
                    "oauth_account_id": "local-test-account",
                },
            }
        )
        set_service(service)
        try:
            provider = ProviderOAuthPlugOpenAICodex(
                {"id": "compat-test", "type": PROVIDER_TYPE, "model": "gpt-6.1-sol"},
                {},
            )
            with patch("oauth_plug_openai_codex.provider_usage.db_helper", None):
                responses = await asyncio.gather(
                    provider.text_chat(prompt="hello", conversation_id="qq-session"),
                    provider.text_chat(
                        prompt="hello", conversation_id="wechat-session"
                    ),
                )
                self.assertEqual([r.completion_text for r in responses], ["hello"] * 2)
                self.assertCountEqual(received, ["qq-session", "wechat-session"])
                chunks = [
                    chunk
                    async for chunk in provider.text_chat_stream(
                        prompt="hello", conversation_id="stream-session"
                    )
                ]
                self.assertEqual(chunks[-1].completion_text, "hello")
                self.assertEqual(received[-1], "stream-session")
                await provider.text_chat(prompt="hello")
                self.assertIsNone(received[-1])
        finally:
            await service.close()
            set_service(previous)
            await runner.cleanup()


if __name__ == "__main__":
    with patch.dict(
        "os.environ",
        {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    ):
        unittest.main()
