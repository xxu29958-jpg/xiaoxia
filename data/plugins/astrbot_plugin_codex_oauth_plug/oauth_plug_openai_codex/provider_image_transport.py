import asyncio
import json
import math
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlsplit

from .provider_runtime_compat import (
    ProviderRequestError,
)
from .provider_support import (
    IMAGE_WEBSOCKET_MAX_EVENTS,
    IMAGE_WEBSOCKET_MAX_MESSAGE_BYTES,
    IMAGE_WEBSOCKET_MAX_TRANSCRIPT_BYTES,
    OpenAIOAuthImageStreamError,
)


class ProviderOAuthPlugOpenAICodexImageTransport:
    """Image transport; shared runtime resources belong to the facade."""

    async def _request_image_backend_once(
        self,
        payload: dict[str, Any],
        request_timeout: float | None = None,
    ) -> tuple[int, str, int]:
        self._sync_oauth_credentials_from_shared()
        headers = self._build_backend_headers()
        attempted_version = self._oauth_shared_state.version

        text_parts: list[str] = []
        async with self._open_oauth_http_client(
            follow_redirects=True,
            timeout=request_timeout,
        ) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/responses",
                headers=headers,
                json=payload,
            ) as response:
                async for line in response.aiter_lines():
                    text_parts.append(line)
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    raw = stripped[5:].strip()
                    if not raw:
                        continue
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = event.get("type")
                    if event_type in {
                        "response.completed",
                        "response.error",
                        "response.failed",
                    }:
                        break

        return response.status_code, "\n".join(text_parts), attempted_version

    async def _request_image_backend(
        self,
        payload: dict[str, Any],
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        if getattr(self, "_oauth_media_closed", False):
            raise RuntimeError("OAuth provider is closed")
        await self._ensure_fresh_oauth_token()
        first_result = await self._request_image_backend_once(payload, request_timeout)
        if len(first_result) == 2:
            status_code, text = first_result
            self._sync_oauth_credentials_from_shared()
            attempted_version = self._oauth_shared_state.version
        else:
            status_code, text, attempted_version = first_result

        if status_code in {401, 403}:
            refreshed = await self._refresh_after_auth_failure(attempted_version)
            if refreshed:
                retry_result = await self._request_image_backend_once(
                    payload,
                    request_timeout,
                )
                status_code, text = retry_result[:2]

        if status_code < 200 or status_code >= 300:
            raise ProviderRequestError(
                self._format_backend_error(status_code, text),
                status_code=status_code,
            )
        return self._parse_backend_response(text)

    async def _request_image_backend_websocket(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Submit one image request with a total deadline and no ambiguous replay."""
        import aiohttp

        deadline_seconds = float(self.timeout if timeout is None else timeout)
        if (
            isinstance(timeout, bool)
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError("timeout 必须是有限正浮点数。")
        base = urlsplit(self.base_url)
        if (
            base.scheme not in {"https", "http"}
            or not base.netloc
            or base.query
            or base.fragment
        ):
            raise ValueError("图片 WebSocket 后端地址无效。")
        scheme = "wss" if base.scheme == "https" else "ws"
        url = f"{scheme}://{base.netloc}{base.path.rstrip('/')}/responses"
        submitted = False

        async def reject_redirect(_session, _context, params):
            raise OpenAIOAuthImageStreamError(
                "request_rejected", status_code=params.response.status
            )

        try:
            async with asyncio.timeout(deadline_seconds):
                if getattr(self, "_oauth_media_closed", False):
                    raise OpenAIOAuthImageStreamError("provider_closed")
                await self._ensure_fresh_oauth_token()
                if not hasattr(self, "_oauth_stream_clients"):
                    self._oauth_stream_clients = set()
                for auth_attempt in range(2):
                    if getattr(self, "_oauth_media_closed", False):
                        raise OpenAIOAuthImageStreamError("provider_closed")
                    headers, attempted_version = (
                        self._build_backend_headers_with_version()
                    )
                    trace = aiohttp.TraceConfig()
                    trace.on_request_redirect.append(reject_redirect)
                    resources = AsyncExitStack()
                    self._oauth_stream_clients.add(resources)
                    try:
                        async with resources:
                            session = await resources.enter_async_context(
                                aiohttp.ClientSession(
                                    timeout=aiohttp.ClientTimeout(total=None),
                                    trace_configs=[trace],
                                )
                            )
                            if getattr(self, "_oauth_media_closed", False):
                                raise OpenAIOAuthImageStreamError("provider_closed")
                            websocket = await resources.enter_async_context(
                                session.ws_connect(
                                    url,
                                    headers=headers,
                                    proxy=self.provider_config.get("proxy") or None,
                                    heartbeat=20,
                                    max_msg_size=IMAGE_WEBSOCKET_MAX_MESSAGE_BYTES,
                                )
                            )
                            if getattr(self, "_oauth_media_closed", False):
                                raise OpenAIOAuthImageStreamError("provider_closed")
                            request = {
                                key: value
                                for key, value in payload.items()
                                if key not in {"stream", "background"}
                            }
                            submitted = True
                            await websocket.send_json(
                                {"type": "response.create", **request}
                            )
                            lines: list[str] = []
                            received_bytes = 0
                            async for message in websocket:
                                if message.type != aiohttp.WSMsgType.TEXT:
                                    raise OpenAIOAuthImageStreamError("outcome_unknown")
                                received_bytes += len(message.data.encode("utf-8"))
                                if (
                                    received_bytes
                                    > IMAGE_WEBSOCKET_MAX_TRANSCRIPT_BYTES
                                    or len(lines) >= IMAGE_WEBSOCKET_MAX_EVENTS
                                ):
                                    raise OpenAIOAuthImageStreamError("outcome_unknown")
                                event = json.loads(message.data)
                                if not isinstance(event, dict):
                                    raise OpenAIOAuthImageStreamError("outcome_unknown")
                                lines.append("data: " + message.data)
                                event_type = event.get("type")
                                if event_type in {
                                    "error",
                                    "response.error",
                                    "response.failed",
                                    "response.incomplete",
                                }:
                                    status = self._extract_stream_error_status_code(
                                        event
                                    )
                                    reason = (
                                        "upstream_unavailable"
                                        if status and status >= 500
                                        else "request_rejected"
                                    )
                                    if status == 429:
                                        reason = "rate_limited"
                                    raise OpenAIOAuthImageStreamError(
                                        reason, status_code=status
                                    )
                                if event_type == "response.completed":
                                    return self._parse_backend_response(
                                        "\n".join(lines)
                                    )
                            raise OpenAIOAuthImageStreamError("outcome_unknown")
                    except aiohttp.WSServerHandshakeError as error:
                        if error.status == 401 and auth_attempt == 0:
                            if await self._refresh_after_auth_failure(
                                attempted_version
                            ):
                                continue
                        reason = (
                            "rate_limited"
                            if error.status == 429
                            else "request_rejected"
                        )
                        raise OpenAIOAuthImageStreamError(
                            reason,
                            status_code=error.status,
                            retryable=error.status == 429 or error.status >= 500,
                        ) from None
                    finally:
                        self._oauth_stream_clients.discard(resources)
        except asyncio.CancelledError:
            raise
        except OpenAIOAuthImageStreamError:
            raise
        except TimeoutError:
            raise OpenAIOAuthImageStreamError(
                "outcome_unknown" if submitted else "timeout",
                retryable=not submitted,
            ) from None
        except (aiohttp.ClientError, OSError):
            raise OpenAIOAuthImageStreamError(
                "outcome_unknown" if submitted else "connection_failed",
                retryable=not submitted,
            ) from None
        except Exception:
            raise OpenAIOAuthImageStreamError(
                "outcome_unknown" if submitted else "provider_failed",
            ) from None
        raise OpenAIOAuthImageStreamError("request_rejected")
