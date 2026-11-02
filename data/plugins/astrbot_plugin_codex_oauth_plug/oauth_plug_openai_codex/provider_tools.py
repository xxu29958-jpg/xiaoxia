from typing import Any


class ProviderOAuthPlugOpenAICodexTools:
    """Tools; shared runtime resources belong to the facade."""

    def _convert_tools_to_backend_format(
        self, tool_list: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        backend_tools: list[dict[str, Any]] = []
        for tool in tool_list:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                backend_tools.append(tool)
                continue
            function = tool.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            backend_tool = {
                "type": "function",
                "name": name,
                "description": str(function.get("description") or "").strip(),
                "parameters": function.get("parameters")
                or {"type": "object", "properties": {}},
            }
            backend_tools.append(backend_tool)
        return backend_tools

    @staticmethod
    def _tool_identity(tool: dict[str, Any]) -> str:
        tool_type = str(tool.get("type") or "").strip()
        if tool_type == "function":
            name = str(tool.get("name") or "").strip()
            if not name:
                raise ValueError("function 工具缺少 name。")
            return f"function:{name}"
        if tool_type == "mcp":
            label = str(tool.get("server_label") or "").strip()
            if not label:
                raise ValueError("mcp 工具缺少 server_label。")
            return f"mcp:{label}"
        if not tool_type:
            raise ValueError("工具定义缺少 type。")
        return tool_type

    def _merge_backend_tools(
        self,
        *tool_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        by_identity: dict[str, dict[str, Any]] = {}
        for group in tool_groups:
            for tool in group:
                if not isinstance(tool, dict):
                    raise ValueError("工具定义必须是对象。")
                identity = self._tool_identity(tool)
                existing = by_identity.get(identity)
                if existing is None:
                    copied = dict(tool)
                    by_identity[identity] = copied
                    merged.append(copied)
                    continue
                if existing != tool:
                    raise ValueError(f"工具 {identity} 存在冲突定义。")
        return merged

    def _configured_web_search_tool(
        self, request_mode: str = "inherit"
    ) -> list[dict[str, Any]]:
        mode = str(request_mode or "inherit").strip().lower()
        if mode == "inherit":
            mode = (
                str(self.provider_config.get("oauth_web_search") or "disabled")
                .strip()
                .lower()
            )
        if mode == "disabled":
            return []
        if mode not in {"cached", "live"}:
            raise ValueError("oauth_web_search 必须是 disabled、cached 或 live。")
        tool: dict[str, Any] = {
            "type": "web_search",
            "external_web_access": mode == "live",
        }
        domains = self.provider_config.get("oauth_web_search_domains") or []
        if not isinstance(domains, list):
            raise ValueError("oauth_web_search_domains 必须是字符串列表。")
        normalized_domains = []
        for domain in domains:
            if not isinstance(domain, str) or not domain.strip():
                raise ValueError("oauth_web_search_domains 必须是非空字符串列表。")
            normalized = domain.strip().lower()
            if "://" in normalized or "/" in normalized:
                raise ValueError(
                    "oauth_web_search_domains 仅接受域名，不接受 URL 或路径。"
                )
            if normalized not in normalized_domains:
                normalized_domains.append(normalized)
        if normalized_domains:
            tool["filters"] = {"allowed_domains": normalized_domains}
        return [tool]

    @staticmethod
    def _is_managed_web_search_tool(tool: dict[str, Any]) -> bool:
        return str(tool.get("type") or "").strip().lower() in {
            "web_search",
            "web_search_preview",
        }
