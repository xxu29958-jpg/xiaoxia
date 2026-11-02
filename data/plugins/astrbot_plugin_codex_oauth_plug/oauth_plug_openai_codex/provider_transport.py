from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from astrbot.core.provider.headers import build_conversation_headers

from .headers import build_codex_backend_headers
from .openai_oauth_sse import iter_json_sse_events
from .provider_runtime_compat import (
    ProviderRequestError,
)


class ProviderOAuthPlugOpenAICodexTransport:
    """Transport; shared runtime resources belong to the facade."""

    def _build_backend_headers(self) -> dict[str, str]:
        headers, _version = self._build_backend_headers_with_version()
        return headers

    def _build_backend_headers_with_version(self) -> tuple[dict[str, str], int]:
        self._sync_oauth_credentials_from_shared()
        attempted_version, credentials = self._oauth_shared_state.versioned_snapshot()
        self.provider_config.update(credentials)
        access_token = str(credentials.get("oauth_access_token") or "").strip()
        account_id = (
            str(credentials.get("oauth_account_id") or "") or self.account_id
        ).strip()
        if not access_token:
            raise Exception("当前 OAuth Source 尚未绑定 access token")
        if not account_id:
            raise Exception(
                "当前 OAuth Source 缺少 chatgpt_account_id，请重新绑定或导入完整 JSON 凭据"
            )

        custom_headers = self.provider_config.get("custom_headers")
        headers = build_codex_backend_headers(
            access_token,
            account_id,
            custom_headers=custom_headers if isinstance(custom_headers, dict) else None,
        )
        return headers, attempted_version

    async def _request_backend_once(
        self,
        payload: dict[str, Any],
        *,
        conversation_id: str | None = None,
    ) -> tuple[int, str, int]:
        self._sync_oauth_credentials_from_shared()
        headers = self._build_backend_headers()
        headers.update(build_conversation_headers(conversation_id))
        attempted_version = self._oauth_shared_state.version

        async with self._open_oauth_http_client(follow_redirects=True) as client:
            response = await client.post(
                f"{self.base_url}/responses",
                headers=headers,
                json=payload,
            )
            raw_text = await response.aread()
        text = raw_text.decode("utf-8", errors="replace")
        return response.status_code, text, attempted_version

    async def _request_backend(
        self, payload: dict[str, Any], *, conversation_id: str | None = None
    ) -> dict[str, Any]:
        if getattr(self, "_oauth_media_closed", False):
            raise RuntimeError("OAuth provider is closed")
        await self._ensure_fresh_oauth_token()
        status_code, text, attempted_version = await self._request_backend_once(
            payload, conversation_id=conversation_id
        )

        if status_code in {401, 403}:
            refreshed = await self._refresh_after_auth_failure(attempted_version)
            if refreshed:
                (
                    status_code,
                    text,
                    _attempted_version,
                ) = await self._request_backend_once(
                    payload, conversation_id=conversation_id
                )

        if status_code < 200 or status_code >= 300:
            raise ProviderRequestError(
                self._format_backend_error(status_code, text),
                status_code=status_code,
            )
        return self._parse_backend_response(text)

    @asynccontextmanager
    async def _open_oauth_http_client(
        self,
        *,
        follow_redirects: bool,
        timeout: float | None = None,
    ):
        if getattr(self, "_oauth_media_closed", False):
            raise RuntimeError("OAuth provider is closed")
        if not hasattr(self, "_oauth_stream_clients"):
            self._oauth_stream_clients = set()
        if not hasattr(self, "_oauth_http_clients"):
            self._oauth_http_clients = {}
        proxy = self.provider_config.get("proxy") or None
        request_timeout = self.timeout if timeout is None else timeout
        key = (proxy, request_timeout, follow_redirects)
        client = self._oauth_http_clients.get(key)
        if client is None:
            client = httpx.AsyncClient(
                trust_env=False,
                proxy=proxy,
                timeout=request_timeout,
                follow_redirects=follow_redirects,
                limits=httpx.Limits(keepalive_expiry=60),
            )
            self._oauth_http_clients[key] = client
            # Provider termination already closes these owned clients. Keeping the
            # pool between turns avoids a new proxy/TLS handshake for every reply.
            self._oauth_stream_clients.add(client)
        yield client

    @asynccontextmanager
    async def _open_oauth_stream_client(self):
        async with self._open_oauth_http_client(follow_redirects=False) as client:
            yield client

    async def _stream_backend_events(
        self,
        payload: dict[str, Any],
        *,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield decoded SSE events and refresh OAuth only before any output."""
        if getattr(self, "_oauth_media_closed", False):
            raise RuntimeError("OAuth provider is closed")
        await self._ensure_fresh_oauth_token()
        auth_retry_available = True
        while True:
            headers, attempted_version = self._build_backend_headers_with_version()
            headers.update(build_conversation_headers(conversation_id))
            retry_after_auth = False
            async with self._open_oauth_stream_client() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/responses",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code == 401 and auth_retry_available:
                        raw_body = await response.aread()
                        refreshed = await self._refresh_after_auth_failure(
                            attempted_version
                        )
                        if refreshed:
                            auth_retry_available = False
                            retry_after_auth = True
                        else:
                            text = raw_body.decode("utf-8", errors="replace")
                            raise ProviderRequestError(
                                self._format_backend_error(response.status_code, text),
                                status_code=response.status_code,
                            )
                    elif response.status_code < 200 or response.status_code >= 300:
                        raw_body = await response.aread()
                        text = raw_body.decode("utf-8", errors="replace")
                        raise ProviderRequestError(
                            self._format_backend_error(response.status_code, text),
                            status_code=response.status_code,
                        )
                    else:
                        async for event in iter_json_sse_events(response.aiter_lines()):
                            yield event
                            if event.get("type") in {
                                "response.completed",
                                "response.error",
                                "response.failed",
                                "response.incomplete",
                                "error",
                            }:
                                return
            if retry_after_auth:
                continue
            return
