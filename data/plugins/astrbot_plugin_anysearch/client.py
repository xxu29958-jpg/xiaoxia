from __future__ import annotations

import json
from typing import Any

import aiohttp


class AnySearchError(RuntimeError):
    """Raised when AnySearch returns an HTTP or JSON-RPC error."""


class AnySearchClient:
    endpoint = "https://api.anysearch.com/mcp"

    def __init__(
        self,
        api_key: str = "",
        timeout: int = 20,
        proxy: str = "",
        fallback_proxy: str = "",
    ) -> None:
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.proxy = proxy
        self.fallback_proxy = fallback_proxy
        self.last_route = "not_used"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout, connect=6)
        routes = [self.proxy or None]
        if self.fallback_proxy and self.fallback_proxy not in routes:
            routes.append(self.fallback_proxy)
        text = ""
        for route_index, route in enumerate(routes):
            try:
                async with aiohttp.ClientSession(
                    timeout=timeout, trust_env=not bool(route)
                ) as session:
                    async with session.post(
                        self.endpoint,
                        json=payload,
                        headers=self._headers(),
                        proxy=route,
                    ) as response:
                        if response.status == 408 or response.status >= 500:
                            raise aiohttp.ClientConnectionError(
                                "Search route unavailable."
                            )
                        if response.status >= 400:
                            raise AnySearchError(
                                f"AnySearch request unavailable (HTTP {response.status})."
                            )
                        chunks = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            chunks.extend(chunk)
                            if len(chunks) > 2_000_000:
                                raise AnySearchError(
                                    "AnySearch response exceeded the size limit."
                                )
                        try:
                            text = chunks.decode("utf-8")
                        except UnicodeDecodeError:
                            raise AnySearchError(
                                "AnySearch returned invalid text."
                            ) from None
                self.last_route = "mainland_fallback" if route_index else "primary"
                break
            except (TimeoutError, aiohttp.ClientError):
                if route_index + 1 == len(routes):
                    self.last_route = "unavailable"
                    raise AnySearchError(
                        "AnySearch connection failed through the configured routes."
                    ) from None

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AnySearchError("AnySearch returned invalid JSON.") from None

        if not isinstance(data, dict):
            raise AnySearchError("AnySearch returned an invalid response.")

        if "error" in data:
            raise AnySearchError("AnySearch returned an API error.")

        result = data.get("result", {})
        if not isinstance(result, dict):
            raise AnySearchError("AnySearch returned an invalid result.")
        if result.get("isError"):
            raise AnySearchError("AnySearch could not complete this request.")
        content = result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return str(item.get("text", ""))[:24000]

        return json.dumps(result, ensure_ascii=False, indent=2)

    async def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        freshness: str = "",
        content_types: list[str] | None = None,
    ) -> str:
        arguments: dict[str, Any] = {"query": query}
        if max_results is not None:
            arguments["max_results"] = max_results
        if freshness:
            arguments["freshness"] = freshness
        if content_types:
            arguments["content_types"] = content_types
        return await self._call("search", arguments)

    async def vertical_search(
        self,
        query: str,
        *,
        domain: str,
        sub_domain: str,
        zone: str = "",
        max_results: int | None = None,
        freshness: str = "",
        content_types: list[str] | None = None,
        sub_domain_params: dict[str, Any] | None = None,
    ) -> str:
        arguments: dict[str, Any] = {
            "query": query,
            "domain": domain,
            "sub_domain": sub_domain,
        }
        if zone:
            arguments["zone"] = zone
        if max_results is not None:
            arguments["max_results"] = max_results
        if freshness:
            arguments["freshness"] = freshness
        if content_types:
            arguments["content_types"] = content_types
        if sub_domain_params:
            arguments["sub_domain_params"] = sub_domain_params
        return await self._call("search", arguments)

    async def list_domains(
        self,
        *,
        domain: str = "",
        domains: list[str] | None = None,
    ) -> str:
        if domains:
            return await self._call("list_domains", {"domains": domains})
        return await self._call("list_domains", {"domain": domain})

    async def batch_search(self, queries: list[dict[str, Any]]) -> str:
        return await self._call("batch_search", {"queries": queries})

    async def extract(self, url: str) -> str:
        return await self._call("extract", {"url": url})
