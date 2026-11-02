import json
from typing import Any

from astrbot import logger


class ProviderOAuthPlugOpenAICodexInput:
    """Input; shared runtime resources belong to the facade."""

    def _convert_message_content(self, raw_content: Any) -> str | list[dict[str, Any]]:
        if isinstance(raw_content, str):
            return raw_content
        if isinstance(raw_content, dict):
            raw_content = [raw_content]
        if not isinstance(raw_content, list):
            return str(raw_content) if raw_content is not None else ""

        content_parts: list[dict[str, Any]] = []
        for part in raw_content:
            if not isinstance(part, dict):
                raise ValueError(
                    f"OAuth_plug 不支持非对象消息内容：{type(part).__name__}。"
                )
            part_type = part.get("type")
            if part_type == "text":
                content_parts.append(
                    {
                        "type": "input_text",
                        "text": str(part.get("text") or ""),
                    }
                )
            elif part_type == "image_url":
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                if image_url:
                    content_parts.append(
                        {
                            "type": "input_image",
                            "image_url": str(image_url),
                        }
                    )
            else:
                unsupported_type = str(part_type or "unknown")
                raise ValueError(
                    "OAuth_plug 尚未适配消息内容类型 "
                    f"{unsupported_type}，不能静默丢弃。"
                )
        if not content_parts:
            return ""
        if len(content_parts) == 1 and content_parts[0]["type"] == "input_text":
            return content_parts[0]["text"]
        return content_parts

    def _stringify_tool_output(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    def _extract_instructions(self, message: dict[str, Any]) -> str:
        content = self._convert_message_content(message.get("content"))
        if isinstance(content, str):
            return content.strip()
        parts: list[str] = []
        for item in content:
            if item.get("type") == "input_text" and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(part for part in parts if part).strip()

    def _convert_messages_to_backend_input(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        instructions_parts: list[str] = []
        response_items: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            if role in {"system", "developer"}:
                instruction = self._extract_instructions(message)
                if instruction:
                    instructions_parts.append(instruction)
                continue

            content = message.get("content")
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "").strip()
                if not call_id:
                    logger.warning("检测到缺少 tool_call_id 的工具回传，已忽略。")
                    continue
                response_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": self._stringify_tool_output(content),
                    }
                )
                continue

            tool_calls = message.get("tool_calls") or []
            normalized_role = role if role in {"user", "assistant"} else "user"
            if content not in (None, "", []):
                response_items.append(
                    {
                        "type": "message",
                        "role": normalized_role,
                        "content": self._convert_message_content(content),
                    }
                )

            if role == "assistant" and isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, str):
                        tool_call = json.loads(tool_call)
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function") or {}
                    name = str(function.get("name") or "").strip()
                    arguments = function.get("arguments") or "{}"
                    call_id = str(tool_call.get("id") or "").strip()
                    if not name or not call_id:
                        continue
                    if not isinstance(arguments, str):
                        arguments = json.dumps(
                            arguments, ensure_ascii=False, default=str
                        )
                    response_items.append(
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments,
                        }
                    )
        return "\n\n".join(
            part for part in instructions_parts if part
        ).strip(), response_items
