"""Consecutive chat turns reuse a real connection until the provider closes."""

import asyncio

import pytest
from oauth_plug_openai_codex.provider import ProviderOAuthPlugOpenAICodex


def make_provider():
    return ProviderOAuthPlugOpenAICodex(
        provider_config={
            "id": "pool-test",
            "type": "oauth_plug_openai_codex_chat_completion",
            "model": "gpt-6.1-sol",
            "oauth_access_token": "test-token",
            "oauth_account_id": "test-account",
        },
        provider_settings={},
    )


@pytest.mark.asyncio
async def test_two_turns_use_one_socket_and_termination_closes_it():
    connections = 0
    disconnected = asyncio.Event()

    async def handle(reader, writer):
        nonlocal connections
        connections += 1
        try:
            while await reader.readline():
                while await reader.readline() != b"\r\n":
                    pass
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            disconnected.set()

    provider = make_provider()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    address = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        async with server:
            for _ in range(2):
                async with provider._open_oauth_http_client(
                    follow_redirects=False
                ) as client:
                    async with client.stream("GET", address) as response:
                        assert await response.aread() == b"ok"
            assert connections == 1
            assert not client.is_closed
            await provider.terminate()
            assert client.is_closed
            await asyncio.wait_for(disconnected.wait(), timeout=2)
    finally:
        await provider.terminate()


@pytest.mark.asyncio
async def test_stream_and_media_timeouts_keep_their_own_clients():
    provider = make_provider()
    try:
        async with provider._open_oauth_http_client(
            follow_redirects=False
        ) as chat:
            pass
        async with provider._open_oauth_http_client(
            follow_redirects=True, timeout=12.5
        ) as media:
            assert media is not chat
            assert media.timeout.read == 12.5
            assert media.follow_redirects is True
        async with provider._open_oauth_http_client(
            follow_redirects=False
        ) as next_chat:
            assert next_chat is chat
    finally:
        await provider.terminate()
    assert chat.is_closed and media.is_closed
