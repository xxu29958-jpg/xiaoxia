"""Validated public GET transport with bounded routing and pinned addresses."""

import asyncio
import ipaddress
import json
import re
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

import aiohttp
from yarl import URL

from .web_notebook import BackpackError

MAX_RESOURCE = 6 * 1024 * 1024
MAX_PAGE_BYTES = 20 * 1024 * 1024
BLOCKED_PATHS = {
    "login",
    "signin",
    "sign-in",
    "sign_in",
    "logout",
    "oauth",
    "authorize",
    "callback",
    "checkout",
    "cart",
    "purchase",
    "payment",
    "upload",
    "delete",
    "unsubscribe",
    "download",
}


BILI_SUBTITLE_HOSTS = {
    "aisubtitle.hdslb.com",
    "i0.hdslb.com",
    "i1.hdslb.com",
    "i2.hdslb.com",
    "subtitle.bilibili.com",
}


def browse_url(value: str, document=True, *, subtitle_signature=False) -> str:
    """Normalize a public URL before DNS, redirects, navigation or storage.

    Args:
        value: An untrusted URL.
        document: Whether to apply account, action and attachment restrictions.

    Returns:
        A normalized HTTP(S) URL without a fragment or tracking parameters.

    Raises:
        BackpackError: The URL is outside the read-only public browsing scope.
    """
    try:
        if (
            not isinstance(value, str)
            or len(value) > 2000
            or re.search(r"[\x00-\x20\\]", value)
        ):
            raise ValueError
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".").encode("idna").decode().lower()
        if parts.scheme not in ("https", "http") or parts.username or parts.password:
            raise ValueError
        if parts.port not in (None, 443 if parts.scheme == "https" else 80):
            raise ValueError
        if "." not in host or any(
            host == x or host.endswith("." + x)
            for x in (
                "localhost",
                "local",
                "internal",
                "lan",
                "home",
                "test",
                "invalid",
                "localdomain",
            )
        ):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError
        path = unquote(unquote(parts.path)).lower()
        if document and (
            set(re.split(r"[/;.]", path)) & BLOCKED_PATHS
            or re.search(
                r"\.(exe|msi|zip|7z|rar|dmg|apk|pdf|docx?|xlsx?|mp[34]|wav)$", path
            )
        ):
            raise BackpackError(
                "Account, purchase, upload and file-download pages are not enabled."
            )
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        for key, value in pairs:
            public_signature = (
                subtitle_signature
                and not document
                and parts.scheme == "https"
                and parts.netloc in BILI_SUBTITLE_HOSTS
                and re.fullmatch(
                    r"/bfs/(?:subtitle|ai_subtitle)/[A-Za-z0-9_./-]+", parts.path
                )
                and not any(segment in {".", ".."} for segment in parts.path.split("/"))
                and key == "auth_key"
                and re.fullmatch(r"[0-9]{10,13}-[0-9]+-[0-9]+-[a-fA-F0-9]{32}", value)
                and sum(name == "auth_key" for name, _ in pairs) == 1
            )
            if not public_signature and re.search(
                r"(^|_)(token|password|passwd|secret|session|authorization|code|key)($|_)",
                key,
                re.I,
            ):
                raise BackpackError(
                    "URLs containing credentials or authorization parameters are not accepted."
                )
            if (
                document
                and key.lower() in ("action", "do", "cmd")
                and value.lower() not in ("view", "read", "search", "list")
            ):
                raise BackpackError("Action URLs are not enabled.")
        query = "&".join(
            x
            for x in parts.query.split("&")
            if not x.lower().startswith(("utm_", "fbclid=", "gclid="))
        )
        return urlunsplit((parts.scheme, host, parts.path or "/", query, ""))
    except BackpackError:
        raise
    except (ValueError, UnicodeError):
        raise BackpackError(
            "Only public HTTP(S) websites without credentials or private addresses are allowed."
        ) from None


def public_addresses(data: dict) -> list[str]:
    """Require every returned address to be globally routable before pinning it."""
    addresses = [
        x.get("data", "") for x in data.get("Answer", []) if x.get("type") in (1, 28)
    ]
    try:
        if (
            data.get("Status") != 0
            or not addresses
            or any(not ipaddress.ip_address(x).is_global for x in addresses)
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise BackpackError(
            "DNS did not resolve exclusively to public addresses; access blocked."
        ) from None
    return addresses


class PublicTransport:
    """Fetch page resources through a validated, IP-pinned HTTP proxy tunnel."""

    def __init__(
        self,
        proxy: str | None,
        fallback: str | None,
        images_only=False,
        referer="",
        *,
        json_only=False,
        subtitle_signatures=False,
    ):
        self.json_only = json_only
        self.subtitle_signatures = bool(json_only and subtitle_signatures)
        self.referer = browse_url(referer, document=False) if referer else ""
        self.images_only = images_only
        self.routes = [("primary", proxy)]
        if fallback and fallback != proxy:
            self.routes.append(("mainland_fallback", fallback))
        self.cache = {}
        self.used_routes = set()
        self.bytes = 0
        self.requests = 0
        self.semaphore = asyncio.Semaphore(6)
        self.client = None

    async def __aenter__(self):
        self.client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12, connect=6),
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
        )
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.close()

    async def load(self, url: str, document: bool) -> tuple[str, dict]:
        """Follow bounded public redirects before fulfilling a browser request.

        Args:
            url: The originally observed public URL.
            document: Whether the response must be HTML.

        Returns:
            The final validated URL and its bounded response.
        """
        for _ in range(5):
            payload = await self.fetch(url, document)
            if payload["status"] == 200:
                return url, payload
            url = payload["headers"]["location"]
        raise BackpackError("Too many redirects; no page was opened.")

    async def fetch(self, url: str, document: bool) -> dict:
        """Fetch a bounded resource; redirects are validated but not followed here.

        Args:
            url: Public resource URL.
            document: Whether this is the main HTML document.

        Returns:
            A safe response for Chromium's intercepted request.

        Raises:
            BackpackError: A policy, size, protocol or network check failed.
        """
        url = browse_url(
            url, document=document, subtitle_signature=self.subtitle_signatures
        )
        self.requests += 1
        if self.requests > 80 or self.bytes >= MAX_PAGE_BYTES:
            raise BackpackError("This page exceeded its request or byte limit.")
        async with self.semaphore:
            for route_name, proxy in self.routes:
                try:
                    parts = urlsplit(url)
                    host = parts.hostname
                    address = await self._resolve_address(route_name, proxy, host)
                    # CONNECT targets this verified IP; TLS still validates the original hostname.
                    pinned = URL(url).with_host(address)
                    async with self.client.get(
                        pinned,
                        proxy=proxy,
                        allow_redirects=False,
                        server_hostname=host,
                        headers={
                            "Host": host,
                            **({"Referer": self.referer} if self.referer else {}),
                            "User-Agent": "Mozilla/5.0 AdaPublicBrowser/1.0",
                            "Accept": (
                                "application/json"
                                if self.json_only
                                else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"
                            ),
                        },
                    ) as response:
                        return await self._read_response(
                            response, url, document, route_name
                        )
                except BackpackError:
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    continue
        raise BackpackError(
            "The public website could not be reached on the configured routes."
        )

    async def _resolve_address(self, route_name, proxy, host):
        cache_key = (route_name, host)
        if cache_key not in self.cache:
            resolver = (
                "https://223.5.5.5/resolve"
                if route_name == "mainland_fallback"
                else "https://1.1.1.1/dns-query"
            )
            async with self.client.get(
                resolver,
                params={"name": host, "type": "A"},
                headers={"Accept": "application/dns-json"},
                proxy=proxy,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise aiohttp.ClientConnectionError("DNS service unavailable")
                raw = await response.content.read(32769)
                if len(raw) > 32768:
                    raise BackpackError("DNS response exceeded its size limit.")
                self.cache[cache_key] = public_addresses(json.loads(raw))
        return self.cache[cache_key][0]

    async def _read_response(self, response, url, document, route_name):
        if response.status == 408 or response.status >= 500:
            raise aiohttp.ClientConnectionError("Public route unavailable")
        self.used_routes.add(route_name)
        if response.status in (301, 302, 303, 307, 308):
            target = browse_url(
                urljoin(url, response.headers.get("Location", "")),
                document=document,
            )
            return {
                "status": response.status,
                "headers": {"location": target},
                "body": b"",
            }
        if response.status != 200:
            raise BackpackError(
                f"Website returned HTTP {response.status}; access was not bypassed."
            )
        mime = response.headers.get("Content-Type", "").lower().split(";")[0].strip()
        disposition = response.headers.get("Content-Disposition", "").lower()
        allowed = (
            mime in ("text/html", "application/xhtml+xml")
            if document
            else (
                mime.startswith(("image/", "font/"))
                or mime
                in (
                    "text/css",
                    "application/font-woff",
                    "application/vnd.ms-fontobject",
                    "application/rss+xml",
                    "application/atom+xml",
                    "application/xml",
                    "text/xml",
                )
            )
        )
        if self.images_only:
            allowed = mime in {
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
                "application/octet-stream",
            }
        if self.json_only:
            allowed = mime in {"application/json", "text/json"}
        if not allowed or ("attachment" in disposition and not self.images_only):
            raise BackpackError(
                "File downloads and unsupported page resources are blocked."
            )
        resource_limit = 1024 * 1024 if self.json_only else MAX_RESOURCE
        if response.content_length and response.content_length > resource_limit:
            raise BackpackError("Page resource exceeded its byte limit.")
        content = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            content.extend(chunk)
            self.bytes += len(chunk)
            if len(content) > resource_limit or self.bytes > MAX_PAGE_BYTES:
                raise BackpackError("Page resource exceeded its byte limit.")
        headers = {
            "content-type": response.headers.get("Content-Type", mime),
            "cache-control": "no-store",
        }
        if document:
            headers["content-security-policy"] = (
                "script-src 'none'; connect-src 'none'; form-action 'none'; object-src 'none'; frame-src 'none'; base-uri 'self'"
            )
        return {
            "status": 200,
            "headers": headers,
            "body": bytes(content),
        }
