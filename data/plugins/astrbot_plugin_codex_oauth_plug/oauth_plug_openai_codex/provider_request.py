from typing import Any

from .provider_runtime_compat import (
    provider_oauth_web_search,
)


class ProviderOAuthPlugOpenAICodexRequest:
    """Request; shared runtime resources belong to the facade."""

    def _build_responses_params(
        self,
        payloads: dict,
        tools,
        *,
        oauth_web_search: str | None = None,
    ) -> dict[str, Any]:
        payloads = dict(payloads)
        payload_search_mode = payloads.pop("_oauth_web_search", None)
        payloads.pop("_oauth_retry_rate_limits", None)
        if oauth_web_search in {None, "inherit"}:
            oauth_web_search = str(
                payload_search_mode or provider_oauth_web_search.get()
            )
        instructions, backend_input = self._convert_messages_to_backend_input(
            payloads.get("messages", []) or []
        )
        params: dict[str, Any] = {
            "model": payloads.get("model", self.get_model()),
            "input": backend_input,
            "instructions": instructions,
            "stream": True,
            "store": False,
        }
        function_tools: list[dict[str, Any]] = []
        if tools:
            tool_list = tools.get_func_desc_openai_style(
                omit_empty_parameter_field=False,
            )
            if tool_list:
                function_tools = self._convert_tools_to_backend_format(tool_list)

        custom_extra_body = self.provider_config.get("custom_extra_body", {})
        custom_tools: list[dict[str, Any]] = []
        if isinstance(custom_extra_body, dict):
            for key, value in custom_extra_body.items():
                if key in {"model", "input", "instructions"}:
                    continue
                if key == "tools":
                    if not isinstance(value, list):
                        raise ValueError("custom_extra_body.tools 必须是列表。")
                    custom_tools = list(value)
                    continue
                params[key] = value

        response_format = payloads.get("response_format")
        if response_format is not None:
            text = dict(params.get("text") or {})
            text["format"] = (
                {"type": "json_schema", **response_format["json_schema"]}
                if response_format.get("type") == "json_schema"
                else response_format
            )
            params["text"] = text

        request_search_mode = str(oauth_web_search or "inherit").strip().lower()
        if request_search_mode not in {"inherit", "disabled", "cached", "live"}:
            raise ValueError(
                "oauth_web_search 必须是 inherit、disabled、cached 或 live。"
            )
        if request_search_mode != "inherit":
            custom_tools = [
                tool
                for tool in custom_tools
                if not self._is_managed_web_search_tool(tool)
            ]

        merged_tools = self._merge_backend_tools(
            function_tools,
            custom_tools,
            self._configured_web_search_tool(request_search_mode),
        )
        if merged_tools:
            params["tools"] = merged_tools
        if payloads.get("tool_choice") is not None:
            params["tool_choice"] = payloads["tool_choice"]
        tool_choice = params.get("tool_choice")
        tool_choice_type = (
            str(tool_choice.get("type") or "").strip().lower()
            if isinstance(tool_choice, dict)
            else str(tool_choice or "").strip().lower()
        )
        if request_search_mode == "disabled" and tool_choice_type in {
            "web_search",
            "web_search_preview",
        }:
            raise ValueError("oauth_web_search=disabled 时不能选择托管搜索工具。")

        reasoning_value = params.get("reasoning")
        if reasoning_value is not None and not isinstance(reasoning_value, dict):
            raise ValueError("reasoning 必须是对象。")
        reasoning = dict(reasoning_value or {})

        configured_effort = params.pop("reasoning_effort", None)
        if configured_effort is not None and "effort" not in reasoning:
            reasoning["effort"] = configured_effort

        request_effort = payloads.get("reasoning_effort")
        if request_effort is not None:
            reasoning["effort"] = request_effort
        request_reasoning = payloads.get("reasoning")
        if request_reasoning is not None:
            if not isinstance(request_reasoning, dict):
                raise ValueError("reasoning 必须是对象。")
            reasoning.update(request_reasoning)

        if "effort" in reasoning:
            effort = str(reasoning["effort"] or "").strip().lower()
            if effort == "off":
                effort = "none"
            if effort == "ultra":
                raise ValueError(
                    "reasoning_effort=ultra 需要多代理调度，不能作为单次 Provider 请求发送。"
                )
            model = str(params["model"] or "").strip().lower()
            capability = self.model_capabilities.get(model)
            if capability:
                supported = capability["supported_reasoning_efforts"]
                if effort == "max" and effort not in supported and "xhigh" in supported:
                    effort = "xhigh"
                elif effort not in supported:
                    supported_text = ", ".join(supported)
                    raise ValueError(
                        f"模型 {model} 不支持 reasoning_effort={effort}；"
                        f"可用值：{supported_text}。"
                    )
            reasoning["effort"] = effort

        if reasoning:
            params["reasoning"] = reasoning
        else:
            params.pop("reasoning", None)
        params.pop("max_output_tokens", None)
        params.pop("temperature", None)
        model_name = str(params.get("model") or "").strip().lower()
        if model_name.startswith(
            ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna")
        ):
            params.pop("top_p", None)
            params.pop("top_logprobs", None)
        strips_reasoning_sampling = model_name.startswith(
            ("gpt-6.1-sol", "gpt-6-astra")
        ) or (
            model_name.startswith(("gpt-6-sol", "gpt-6-luna"))
            and reasoning.get("effort") != "none"
        )
        if strips_reasoning_sampling:
            include = params.get("include")
            if isinstance(include, list):
                filtered_include = [
                    item for item in include if item != "message.output_text.logprobs"
                ]
                if filtered_include:
                    params["include"] = filtered_include
                else:
                    params.pop("include", None)
            elif include == "message.output_text.logprobs":
                params.pop("include", None)
        return params
