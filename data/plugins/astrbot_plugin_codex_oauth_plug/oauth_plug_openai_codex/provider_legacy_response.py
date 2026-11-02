import json
from typing import Any

from .provider_runtime_compat import (
    ProviderRequestError,
)


class ProviderOAuthPlugOpenAICodexLegacyResponse:
    """Legacy response; shared runtime resources belong to the facade."""

    def _format_backend_error(self, status_code: int, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return f"Codex backend request failed: status={status_code}"
        try:
            data = json.loads(stripped)
            return f"Codex backend request failed: status={status_code}, body={data}"
        except Exception:
            return (
                f"Codex backend request failed: status={status_code}, body={stripped}"
            )

    def _parse_backend_response(self, text: str) -> dict[str, Any]:
        completed_response: dict[str, Any] | None = None
        error_payload: dict[str, Any] | None = None
        output_text_parts: list[str] = []
        output_text_done: str | None = None
        output_items: list[dict[str, Any]] = []
        output_item_ids: set[str] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type in {
                "error",
                "response.error",
                "response.failed",
                "response.incomplete",
            }:
                error_payload = event
            elif event_type == "response.output_text.delta":
                delta = event.get("delta")
                if delta:
                    output_text_parts.append(str(delta))
            elif event_type == "response.output_text.done":
                text_value = event.get("text")
                if text_value is not None:
                    output_text_done = str(text_value)
            elif event_type == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict):
                    item_id = str(item.get("id") or "")
                    dedupe_key = item_id or f"index:{len(output_items)}"
                    if dedupe_key not in output_item_ids:
                        output_item_ids.add(dedupe_key)
                        output_items.append(item)
            if event_type == "response.completed":
                response = event.get("response")
                if isinstance(response, dict):
                    completed_response = response
                else:
                    completed_response = event
        merged_output_text = (
            output_text_done
            if output_text_done is not None
            else "".join(output_text_parts)
        )
        if completed_response:
            if not completed_response.get("output") and output_items:
                completed_response["output"] = output_items
            if merged_output_text and not completed_response.get("output_text"):
                completed_response["output_text"] = merged_output_text
            return completed_response
        if error_payload:
            status_code = self._extract_stream_error_status_code(error_payload)
            message = f"Codex backend returned error event: {error_payload}"
            if status_code is not None:
                raise ProviderRequestError(message, status_code=status_code)
            raise RuntimeError(message)
        stripped = text.strip()
        if stripped.startswith("{"):
            data = json.loads(stripped)
            if isinstance(data, dict):
                if data.get("type") in {
                    "error",
                    "response.error",
                    "response.failed",
                    "response.incomplete",
                }:
                    status_code = self._extract_stream_error_status_code(data)
                    message = f"Codex backend returned error event: {data}"
                    if status_code is not None:
                        raise ProviderRequestError(
                            message,
                            status_code=status_code,
                        )
                    raise RuntimeError(message)
                if data.get("type") == "response.completed" and isinstance(
                    data.get("response"), dict
                ):
                    response = data["response"]
                    if not response.get("output") and output_items:
                        response["output"] = output_items
                    if merged_output_text and not response.get("output_text"):
                        response["output_text"] = merged_output_text
                    return response
                return data
        raise Exception(
            "Codex backend response did not contain response.completed event"
        )
