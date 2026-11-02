from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

from astrbot.core.provider.entities import LLMResponse

from .provider_runtime_compat import (
    ProviderRequestError,
    retry_provider_request,
)


class ProviderOAuthPlugOpenAICodexStream:
    """Stream; shared runtime resources belong to the facade."""

    async def _query(
        self,
        payloads: dict,
        tools,
        *,
        request_max_retries: int | None = None,
        conversation_id: str | None = None,
        retry_rate_limits: bool | None = None,
        oauth_web_search: str | None = None,
    ) -> LLMResponse:
        if retry_rate_limits is None:
            retry_rate_limits = payloads.get("_oauth_retry_rate_limits")
        params = self._build_responses_params(
            payloads,
            tools,
            oauth_web_search=oauth_web_search,
        )
        response = await retry_provider_request(
            "OpenAI OAuth",
            lambda: self._request_backend(params, conversation_id=conversation_id),
            retry_rate_limits=retry_rate_limits,
            max_attempts=request_max_retries,
        )
        try:
            return await self._parse_responses_completion(response, tools)
        except Exception as exc:
            usage = self._extract_response_usage(
                response.get("usage")
                if isinstance(response, dict)
                else getattr(response, "usage", None)
            )
            if usage is not None:
                setattr(exc, "_astrbot_token_usage", usage)
            raise

    async def _query_stream(
        self,
        payloads: dict,
        tools,
        *,
        request_max_retries: int | None = None,
        conversation_id: str | None = None,
        retry_rate_limits: bool | None = None,
        oauth_web_search: str | None = None,
    ) -> AsyncGenerator[LLMResponse, None]:
        del request_max_retries, retry_rate_limits
        params = self._build_responses_params(
            payloads,
            tools,
            oauth_web_search=oauth_web_search,
        )
        output_text_parts: list[str] = []
        reasoning_parts: list[str] = []
        output_items: dict[str, dict[str, Any]] = {}
        completed_response: dict[str, Any] | None = None

        async with aclosing(
            self._stream_backend_events(params, conversation_id=conversation_id)
        ) as event_stream:
            async for event in event_stream:
                chunk, completed = self._apply_stream_event(
                    event,
                    output_text_parts,
                    reasoning_parts,
                    output_items,
                )
                if completed is not None:
                    completed_response = completed
                if chunk is not None:
                    yield chunk

        if completed_response is None:
            raise Exception(
                "Codex backend stream ended without response.completed event"
            )
        if not completed_response.get("output") and output_items:
            completed_response["output"] = list(output_items.values())
        if output_text_parts and not completed_response.get("output_text"):
            completed_response["output_text"] = "".join(output_text_parts)
        if reasoning_parts:
            existing_output = list(completed_response.get("output") or [])
            if not any(
                isinstance(item, dict) and item.get("type") == "reasoning"
                for item in existing_output
            ):
                existing_output.append(
                    {
                        "type": "reasoning",
                        "summary": [
                            {"type": "summary_text", "text": "".join(reasoning_parts)}
                        ],
                    }
                )
                completed_response["output"] = existing_output
        final_response = await self._parse_responses_completion(
            completed_response, tools
        )
        if not output_text_parts and final_response.completion_text:
            yield LLMResponse(
                role="assistant",
                completion_text=final_response.completion_text,
                is_chunk=True,
            )
        elif output_text_parts:
            citations = self._extract_url_citations(completed_response)
            if citations:
                citation_text = "\n".join(
                    f"- [{title}]({url})" for title, url in citations
                )
                yield LLMResponse(
                    role="assistant",
                    completion_text=f"\n\n来源：\n{citation_text}",
                    is_chunk=True,
                )
        yield final_response

    def _apply_stream_event(
        self,
        event: dict[str, Any],
        output_text_parts: list[str],
        reasoning_parts: list[str],
        output_items: dict[str, dict[str, Any]],
    ) -> tuple[LLMResponse | None, dict[str, Any] | None]:
        event_type = event.get("type")
        if event_type in {
            "error",
            "response.error",
            "response.failed",
            "response.incomplete",
        }:
            status_code = self._extract_stream_error_status_code(event)
            if status_code is None:
                error = RuntimeError(f"Codex backend stream ended with {event_type}")
            else:
                error = ProviderRequestError(
                    f"Codex backend stream ended with {event_type}",
                    status_code=status_code,
                )
            response = event.get("response")
            if isinstance(response, dict):
                usage = self._extract_response_usage(response.get("usage"))
                if usage is not None:
                    setattr(error, "_astrbot_token_usage", usage)
            raise error
        if event_type == "response.output_text.delta":
            delta = str(event.get("delta") or "")
            if delta:
                output_text_parts.append(delta)
                return (
                    LLMResponse(
                        role="assistant",
                        completion_text=delta,
                        is_chunk=True,
                    ),
                    None,
                )
        elif event_type in {
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            delta = str(event.get("delta") or "")
            if delta:
                reasoning_parts.append(delta)
                return (
                    LLMResponse(
                        role="assistant",
                        reasoning_content=delta,
                        is_chunk=True,
                    ),
                    None,
                )
        elif event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict):
                item_id = str(item.get("id") or event.get("output_index") or "")
                if item_id:
                    output_items[item_id] = dict(item)
        elif event_type == "response.function_call_arguments.delta":
            item_id = str(event.get("item_id") or event.get("output_index") or "")
            delta = str(event.get("delta") or "")
            item = output_items.setdefault(
                item_id,
                {
                    "type": "function_call",
                    "call_id": str(event.get("call_id") or ""),
                    "name": str(event.get("name") or ""),
                    "arguments": "",
                },
            )
            item["arguments"] = str(item.get("arguments") or "") + delta
            if delta:
                return (
                    LLMResponse(
                        role="tool",
                        tools_call_args=[{"_arguments_delta": delta}],
                        tools_call_name=[str(item.get("name") or "")],
                        tools_call_ids=[str(item.get("call_id") or item_id)],
                        is_chunk=True,
                    ),
                    None,
                )
        elif event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                item_id = str(item.get("id") or event.get("output_index") or "")
                output_items[item_id] = dict(item)
        elif event_type == "response.completed":
            response = event.get("response")
            return None, dict(response) if isinstance(response, dict) else {}
        return None, None

    @staticmethod
    def _extract_stream_error_status_code(event: dict[str, Any]) -> int | None:
        candidates = [event]
        for key in ("error", "response"):
            value = event.get(key)
            if isinstance(value, dict):
                candidates.append(value)
                nested_error = value.get("error")
                if isinstance(nested_error, dict):
                    candidates.append(nested_error)
        for candidate in candidates:
            for key in ("status_code", "status"):
                value = candidate.get(key)
                if isinstance(value, int):
                    return value
            code = str(candidate.get("code") or "").strip().lower()
            if code in {
                "rate_limit_exceeded",
                "rate_limit_error",
                "insufficient_quota",
                "credit_balance_exhausted",
                "429",
            }:
                return 429
        return None
