"""Retry API connection establishment without replaying ambiguous deliveries."""

import json

import aiohttp

from astrbot.api import logger

from . import network_routes


async def request_wechat_json(
    client,
    method,
    endpoint,
    *,
    params=None,
    payload=None,
    token_required=False,
    timeout_ms=None,
    headers=None,
):
    """Preserve native API semantics and retry only before a request can be sent.

    Args:
        client: The native client which owns credentials and the HTTP session.
        method: Original HTTP method.
        endpoint: Native API endpoint, never a model-selected URL.
        params: Original query parameters.
        payload: Original JSON body, including its stable message identifier.
        token_required: Whether the native client must supply its credential.
        timeout_ms: Native request timeout override.
        headers: Native additional headers.

    Returns:
        The native JSON response, including any server-side failure code.

    Raises:
        Exception: Connection, authorization, response or decoding failure. A timeout
            after connection is deliberately not retried: delivery may have happened.
    """
    await client.ensure_http_session()
    seconds = (timeout_ms if timeout_ms is not None else client.api_timeout_ms) / 1000
    timeout = aiohttp.ClientTimeout(
        total=seconds,
        connect=min(6, seconds / 2) if seconds > 0 else 6,
    )
    request_headers = client._build_base_headers(token_required=token_required)
    request_headers.update(headers or {})
    for attempt, proxy in enumerate(network_routes.WECHAT_ROUTES):
        try:
            async with client._http_session.request(
                method,
                client._resolve_url(endpoint),
                params=params,
                json=payload,
                headers=request_headers,
                timeout=timeout,
                proxy=proxy,
                allow_redirects=False,
            ) as response:
                if response.status >= 300:
                    raise RuntimeError(
                        f"WeChat API rejected request (HTTP {response.status})."
                    )
                text = await response.text()
                return json.loads(text) if text else {}
        except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError):
            raise
        except (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError) as exc:
            if attempt:
                raise
            logger.warning(
                "WeChat API gateway failed before sending (%s); retrying directly.",
                type(exc).__name__,
            )
