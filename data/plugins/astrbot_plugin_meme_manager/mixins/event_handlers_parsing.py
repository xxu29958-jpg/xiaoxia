import copy
import re
from typing import Any

from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain

from ..backend.meme_parser import MemeParser
from ..backend.meme_parser.text_safety import (
    _protected_reference_spans,
)
from ..backend.semantic.models import (
    runtime_category_mapping,
)


class EventHandlerMixinParsing:
    """Parsing; shared runtime resources belong to the facade."""

    def _normalize_outgoing_message_components(self, message: Any) -> list:
        """将外部传入消息统一为组件列表。"""
        if isinstance(message, MessageChain):
            components = message.chain or []
        elif isinstance(message, list):
            components = message
        elif isinstance(message, str):
            components = [Plain(message)]
        else:
            raise TypeError("message must be str, list or MessageChain")
        normalized = []
        for component in components:
            if isinstance(component, Plain):
                if normalized and isinstance(normalized[-1], Plain):
                    normalized[-1].text += component.text or ""
                else:
                    normalized.append(Plain(component.text or ""))
            else:
                normalized.append(component)
        return normalized

    def _clean_outgoing_plain_text(self, text: str) -> str:
        cleaned = MemeParser.parse(text or "", strip_references=True).text
        if self.content_cleanup_rule and self.content_cleanup_rule != r"&&[a-zA-Z]*&&":
            protected = _protected_reference_spans(cleaned)
            edits = [
                match.span()
                for match in re.finditer(self.content_cleanup_rule, cleaned)
                if not any(
                    match.start() < end and match.end() > start
                    for start, end in protected
                )
            ]
            for start, end in reversed(edits):
                cleaned = cleaned[:start] + cleaned[end:]
        if getattr(self, "filter_all_tags", False):
            cleaned = MemeParser.parse(cleaned, filter_all=True).text
        return cleaned

    def _make_meme_parser(
        self, categories, *, semantic=False, fallbacks=True, final_cleanup=False
    ):
        """Build an isolated parser from the current plugin settings.

        Args:
            categories: Allowed keys from the request's resource pack.
            semantic: Whether to accept semantic IDs instead of categories.
            fallbacks: Whether standalone and repeated categories are permitted.
            final_cleanup: Whether this parser handles final delivery text.

        Returns:
            A new parser with no shared mutable request state.
        """
        return MemeParser(
            categories,
            semantic=semantic,
            alternative=self._read_config_value(
                ("generation", "markup", "enable_alternative"), default=True
            ),
            loose=fallbacks
            and self._read_config_value(
                ("generation", "matching", "enable_loose_matching"), default=False
            ),
            repeated=fallbacks
            and self._read_config_value(
                ("generation", "markup", "enable_repeated_detection"), default=True
            ),
            remove_invalid=self.remove_invalid_alternative_markup,
            strip_references=True,
            filter_all=final_cleanup and getattr(self, "filter_all_tags", False),
        )

    def _extract_marked_emotions_from_text(
        self, text: str, valid_emoticons: set[str]
    ) -> tuple[str, list[str]]:
        """Parse compatibility messages without standalone fallback detection.

        Args:
            text: Original compatibility message text.
            valid_emoticons: Allowed keys in the selected resource pack.

        Returns:
            Visible text and valid markers in source order.
        """
        parser = self._make_meme_parser(valid_emoticons, fallbacks=False)
        cleaned = parser.feed(text or "") + parser.finish()
        return cleaned, [token.value for token in parser.tokens if token.valid]

    async def _filter_meme_stream(self, event, source):
        """Filter visible deltas before the platform consumes the async stream.

        Args:
            event: Request event supplying the selected resource pack.
            source: Original asynchronous MessageChain stream.

        Yields:
            Message chains with parsed visible text; other components are retained.
        """
        parser = None
        last_text_chunk = None
        try:
            async for chunk in source:
                if chunk is None:
                    continue
                if getattr(chunk, "type", None) not in {None, "text"}:
                    yield chunk
                    continue
                if not isinstance(chunk, MessageChain):
                    yield chunk
                    continue
                # 请求钩子在源生成器内运行，必须等钩子初始化完成后
                # 再解析表情包和语义模式。
                event.set_extra("meme_manager_stream_filtered", True)
                if parser is None:
                    context = self._resolve_runtime_pack_context(event=event)
                    mapping = context.get("category_mapping")
                    categories = runtime_category_mapping(
                        mapping if isinstance(mapping, dict) else self.category_mapping
                    )
                    parser = self._make_meme_parser(
                        categories,
                        semantic=self._semantic_mode_active(event),
                        final_cleanup=True,
                    )
                last_text_chunk = chunk
                components = []
                for component in chunk.chain:
                    if isinstance(component, Plain):
                        visible = parser.feed(component.text or "")
                        if visible:
                            components.append(Plain(visible))
                    else:
                        # 非文本组件会结束连续的文本片段。
                        visible = parser.finish()
                        if visible:
                            components.append(Plain(visible))
                        components.append(component)
                        parser = self._make_meme_parser(
                            categories,
                            semantic=self._semantic_mode_active(event),
                            final_cleanup=True,
                        )
                if components:
                    filtered = copy.copy(chunk)
                    filtered.chain = components
                    yield filtered
            remaining = parser.finish() if parser is not None else ""
            if remaining:
                final_chunk = (
                    copy.copy(last_text_chunk)
                    if last_text_chunk is not None
                    else MessageChain()
                )
                final_chunk.chain = [Plain(remaining)]
                yield final_chunk
        finally:
            if hasattr(source, "aclose"):
                await source.aclose()

    def _limit_meme_selection(self, selections: list[str]) -> list[str]:
        """Truncate a reply's selected memes before building or sending images.

        Args:
            selections: Ordered category names or semantic image IDs.

        Returns:
            The first configured number of selections, or all for a negative limit.
        """
        limit = getattr(self, "max_memes_per_message", -1)
        return selections[:limit] if limit >= 0 else selections
