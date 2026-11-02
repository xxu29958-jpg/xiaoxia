import json
from typing import Any
from urllib.parse import quote, urlsplit

from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse


class ProviderOAuthPlugOpenAICodexResponse:
    """Response; shared runtime resources belong to the facade."""

    @staticmethod
    def _extract_url_citations(response: Any) -> list[tuple[str, str]]:
        if not isinstance(response, dict):
            return []
        citations: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for item in response.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []) or []:
                if not isinstance(content, dict):
                    continue
                for annotation in content.get("annotations", []) or []:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") not in {"url_citation", "source"}:
                        continue
                    url = str(annotation.get("url") or "").strip()
                    if not url.startswith(("https://", "http://")) or url in seen_urls:
                        continue
                    if any(ord(char) < 32 for char in url):
                        continue
                    try:
                        parsed_url = urlsplit(url)
                        if not parsed_url.hostname or parsed_url.username:
                            continue
                    except ValueError:
                        continue
                    title = str(annotation.get("title") or url).strip() or url
                    title = " ".join(title.split())[:512]
                    title = (
                        title.replace("\\", "\\\\")
                        .replace("[", "\\[")
                        .replace("]", "\\]")
                    )
                    seen_urls.add(url)
                    citations.append((title, quote(url, safe=":/?#@!$&'*+,;=%~.-_")))
        return citations

    async def _parse_responses_completion(self, response: Any, tools) -> LLMResponse:
        llm_response = LLMResponse("assistant")
        output_text = ""
        if isinstance(response, dict):
            output_text = str(response.get("output_text") or "").strip()
        else:
            output_text = (getattr(response, "output_text", None) or "").strip()
        output_items = list(
            response.get("output", [])
            if isinstance(response, dict)
            else getattr(response, "output", []) or []
        )
        reasoning_parts: list[str] = []
        tool_args: list[dict[str, Any]] = []
        tool_names: list[str] = []
        tool_ids: list[str] = []
        message_text_parts: list[str] = []

        for item in output_items:
            item_type = (
                item.get("type")
                if isinstance(item, dict)
                else getattr(item, "type", None)
            )
            if item_type == "reasoning":
                summaries = (
                    item.get("summary", [])
                    if isinstance(item, dict)
                    else getattr(item, "summary", []) or []
                )
                for summary in summaries:
                    text = (
                        summary.get("text")
                        if isinstance(summary, dict)
                        else getattr(summary, "text", None)
                    )
                    if text:
                        reasoning_parts.append(str(text))
            elif item_type == "function_call":
                arguments = (
                    item.get("arguments", "{}")
                    if isinstance(item, dict)
                    else getattr(item, "arguments", "{}")
                )
                try:
                    parsed_args = (
                        json.loads(arguments)
                        if isinstance(arguments, str)
                        else arguments
                    )
                except Exception:
                    parsed_args = {}
                tool_args.append(parsed_args if isinstance(parsed_args, dict) else {})
                tool_names.append(
                    str(
                        item.get("name", "")
                        if isinstance(item, dict)
                        else getattr(item, "name", "") or ""
                    )
                )
                tool_ids.append(
                    str(
                        item.get("call_id", "")
                        if isinstance(item, dict)
                        else getattr(item, "call_id", "") or ""
                    )
                )
            elif item_type == "message" and not output_text:
                content_items = (
                    item.get("content", [])
                    if isinstance(item, dict)
                    else getattr(item, "content", []) or []
                )
                item_text_parts: list[str] = []
                for content in content_items:
                    ctype = (
                        content.get("type")
                        if isinstance(content, dict)
                        else getattr(content, "type", None)
                    )
                    if ctype in {"output_text", "text"}:
                        text = (
                            content.get("text")
                            if isinstance(content, dict)
                            else getattr(content, "text", None)
                        )
                        if text:
                            item_text_parts.append(str(text))
                if item_text_parts:
                    message_text_parts.append("".join(item_text_parts).strip())

        output_text = output_text or "\n".join(message_text_parts)
        citations = self._extract_url_citations(response)
        if output_text:
            if citations:
                citation_text = "\n".join(
                    f"- [{title}]({url})" for title, url in citations
                )
                output_text += f"\n\n来源：\n{citation_text}"
            llm_response.result_chain = MessageChain().message(output_text)

        if reasoning_parts:
            llm_response.reasoning_content = "\n".join(
                part for part in reasoning_parts if part
            )

        if tool_args:
            llm_response.role = "tool"
            llm_response.tools_call_args = tool_args
            llm_response.tools_call_name = tool_names
            llm_response.tools_call_ids = tool_ids

        if llm_response.completion_text is None and not llm_response.tools_call_args:
            raise Exception(f"账号态 responses 响应无法解析：{response}。")

        llm_response.raw_completion = response
        response_id = (
            response.get("id")
            if isinstance(response, dict)
            else getattr(response, "id", None)
        )
        if response_id:
            llm_response.id = response_id
        usage = self._extract_response_usage(
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        if usage is not None:
            llm_response.usage = usage
        return llm_response
