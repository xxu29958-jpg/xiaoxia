"""Connection routing and bounded recovery for the native WeChat client."""

import asyncio
import socket

import aiohttp

from astrbot.api import logger
from astrbot.core.platform.sources.weixin_oc.weixin_oc_client import WeixinOCClient

WECHAT_ROUTES = ("http://127.0.0.1:17898", None)


def prefer_ipv6_resolver(original):
    class PreferIPv6Resolver(original):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            """Resolve normally, then prefer IPv6 when both families exist.

            Args:
                host: Hostname requested by aiohttp.
                port: Destination port.
                family: Address-family constraint from the connector.

            Returns:
                All original addresses with IPv6 addresses first.
            """
            addresses = await super().resolve(host, port, family)
            return sorted(
                addresses, key=lambda address: address["family"] != socket.AF_INET6
            )

    return PreferIPv6Resolver


async def ensure_wechat_proxy_session(client: WeixinOCClient) -> None:
    """Create a session whose explicit routes can fall back to a direct connection.

    Args:
        client: WeChat client whose session is being initialized.
    """
    if client._http_session is None or client._http_session.closed:
        client._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=client.api_timeout_ms / 1000),
            trust_env=False,
        )


async def download_wechat_media(client, encrypted_query_param):
    """Try the owned mainland gateway, then retry an inbound media GET directly.

    Args:
        client: The existing WeChat client; API and send routes stay intact.
        encrypted_query_param: Opaque CDN reference, never logged.

    Returns:
        Downloaded bytes for the native media decryptor.

    Raises:
        RuntimeError: The CDN rejected the request or both routes failed.
    """
    await client.ensure_http_session()
    timeout = aiohttp.ClientTimeout(total=12, connect=6)
    for attempt, proxy in enumerate(WECHAT_ROUTES):
        try:
            async with client._http_session.get(
                client._build_cdn_download_url(encrypted_query_param),
                timeout=timeout,
                proxy=proxy,
                allow_redirects=False,
            ) as response:
                if response.status == 200:
                    return await response.read()
                if response.status != 408 and response.status < 500:
                    raise RuntimeError(
                        f"WeChat media download rejected (HTTP {response.status})."
                    )
                failure = f"HTTP {response.status}"
        except (
            aiohttp.ClientConnectionError,
            aiohttp.ClientPayloadError,
            asyncio.TimeoutError,
        ) as exc:
            failure = type(exc).__name__
        if attempt == 0:
            logger.warning(
                "WeChat media gateway failed (%s); retrying directly.",
                failure,
            )
    raise RuntimeError(f"WeChat media unavailable on both routes ({failure}).")
