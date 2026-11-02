"""Public-only CONNECT gateway; Chromium retains its own TLS and HTTP stack."""

import asyncio
import base64
import contextlib
import time
from collections import deque
from urllib.parse import unquote, urlsplit

import aiohttp

from .public_transport import PublicTransport, browse_url
from .web_notebook import BackpackError


class BrowserGateway:
    def __init__(self, proxy, fallback):
        self.transport = PublicTransport(proxy, fallback)
        self.server = None
        self.tasks = set()
        self.bytes = 0
        self.connections = 0
        self.last_error = ""
        self.recent_connections = deque()
        self.fallback_hosts = set()

    def prefer_fallback(self, host):
        if len(self.transport.routes) < 2 or host in self.fallback_hosts:
            return False
        self.fallback_hosts.add(host)
        return True

    async def start(self):
        await self.transport.__aenter__()
        self.server = await asyncio.start_server(
            self._serve, "127.0.0.1", 0, limit=16384
        )
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = self.tasks - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.transport.__aexit__()

    async def _tunnel(self, host):
        """Resolve and pin the CONNECT target before forwarding any browser bytes."""
        routes = self.transport.routes
        if host in self.fallback_hosts:
            routes = list(reversed(routes))
        for route, proxy in routes:
            upstream = None
            try:
                async with asyncio.timeout(15):
                    address = await self.transport._resolve_address(route, proxy, host)
                    if proxy:
                        parsed = urlsplit(proxy)
                        if parsed.scheme != "http":
                            raise BackpackError(
                                "Browser upstream must be an HTTP CONNECT proxy."
                            )
                        reader, upstream = await asyncio.open_connection(
                            parsed.hostname, parsed.port or 80
                        )
                        headers = (
                            f"CONNECT {address}:443 HTTP/1.1\r\nHost: {address}:443\r\n"
                        )
                        if parsed.username:
                            credential = f"{unquote(parsed.username)}:{unquote(parsed.password or '')}"
                            token = base64.b64encode(credential.encode()).decode()
                            headers += f"Proxy-Authorization: Basic {token}\r\n"
                        upstream.write((headers + "\r\n").encode())
                        await upstream.drain()
                        response = await reader.readuntil(b"\r\n\r\n")
                        status = response.split(b" ", 2)[1]
                        if status in (b"401", b"403", b"407"):
                            raise BackpackError(
                                "The configured proxy denied the connection."
                            )
                        if status != b"200":
                            raise OSError("Upstream CONNECT failed")
                    else:
                        reader, upstream = await asyncio.open_connection(address, 443)
                    self.transport.used_routes.add(route)
                    return reader, upstream
            except BackpackError:
                if upstream:
                    upstream.close()
                raise
            except (
                OSError,
                aiohttp.ClientError,
                TimeoutError,
                asyncio.IncompleteReadError,
            ):
                if upstream:
                    upstream.close()
        raise BackpackError("No configured public network route is reachable.")

    async def _pipe(self, reader, writer):
        while True:
            async with asyncio.timeout(60):
                chunk = await reader.read(65536)
            if not chunk:
                return
            self.bytes += len(chunk)
            if self.bytes > 200 * 1024 * 1024:
                raise BackpackError("Browser session traffic limit reached.")
            writer.write(chunk)
            await writer.drain()

    async def _serve(self, reader, writer):
        """Reject non-public destinations, including redirects and subresources."""
        task = asyncio.current_task()
        self.tasks.add(task)
        upstream = None
        pumps = []
        connected = False
        try:
            now = time.monotonic()
            while self.recent_connections and self.recent_connections[0] < now - 60:
                self.recent_connections.popleft()
            if len(self.tasks) > 64 or len(self.recent_connections) >= 600:
                raise BackpackError("Browser connection limit reached.")
            self.recent_connections.append(now)
            self.connections += 1
            async with asyncio.timeout(10):
                head = await reader.readuntil(b"\r\n\r\n")
            first = head.split(b"\r\n", 1)[0].decode("ascii")
            method, target, version = first.split(" ")
            if method != "CONNECT" or version != "HTTP/1.1":
                raise BackpackError("Only public HTTPS pages are enabled.")
            if not target.endswith(":443"):
                raise BackpackError("Only HTTPS port 443 is enabled.")
            host = urlsplit(
                browse_url("https://" + target + "/", document=False)
            ).hostname
            remote, upstream = await self._tunnel(host)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            connected = True
            pumps = [
                asyncio.create_task(self._pipe(reader, upstream)),
                asyncio.create_task(self._pipe(remote, writer)),
            ]
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for pump in done:
                pump.result()
        except Exception as exc:
            self.last_error = (
                str(exc) if isinstance(exc, BackpackError) else type(exc).__name__
            )
            if not connected:
                with contextlib.suppress(Exception):
                    writer.write(
                        b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
                    )
                    await writer.drain()
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    with contextlib.suppress(Exception):
                        await stream.wait_closed()
            self.tasks.discard(task)
