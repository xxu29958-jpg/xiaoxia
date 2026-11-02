"""Real socket guard checks, including IP pinning and upstream failure recovery."""

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.browser_gateway import BrowserGateway
from astrbot_plugin_ada_companion.public_transport import public_addresses


@pytest.mark.asyncio
async def test_gateway_blocks_private_targets_and_pins_public_dns():
    received = []

    async def upstream(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            received.append(head.decode())
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            payload = await reader.read(64)
            writer.write(payload)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    proxy = await asyncio.start_server(upstream, "127.0.0.1", 0)
    proxy_url = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
    gateway = BrowserGateway("http://127.0.0.1:1", proxy_url)

    async def dns(route, proxy, host):
        address = "127.0.0.1" if host == "rebinding.example.com" else "93.184.216.34"
        return public_addresses(
            {"Status": 0, "Answer": [{"type": 1, "data": address}]}
        )[0]

    gateway.transport._resolve_address = dns
    address = await gateway.start()
    port = urlsplit(address).port
    try:
        for host in (
            "127.0.0.1",
            "169.254.169.254",
            "rebinding.example.com",
            "host.internal",
        ):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            assert b"502" in await reader.readuntil(b"\r\n\r\n")
            writer.close()
            await writer.wait_closed()
        assert received == []
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT public.example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert b"200" in await reader.readuntil(b"\r\n\r\n")
        writer.write(b"opaque TLS bytes")
        await writer.drain()
        assert await reader.read(64) == b"opaque TLS bytes"
        assert received[0].startswith("CONNECT 93.184.216.34:443 ")
        assert gateway.transport.used_routes == {"mainland_fallback"}
        writer.close()
        await writer.wait_closed()
    finally:
        await gateway.close()
        proxy.close()
        await proxy.wait_closed()
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)
