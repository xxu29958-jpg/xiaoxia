from typing import Any

from astrbot.api.event import AstrMessageEvent

from .event_handlers_support import (
    LLM_REQUEST_ORIGIN_CHAT,
    LLM_REQUEST_ORIGIN_EXTRA_KEY,
    LLM_REQUEST_ORIGIN_PLUGIN,
    TRIGGER_SCOPE_CHAT_AND_PLUGIN,
    normalize_trigger_scope,
)


class EventHandlerMixinScope:
    """Scope; shared runtime resources belong to the facade."""

    async def _mark_llm_request_origin_impl(self, event: AstrMessageEvent) -> None:
        """在 AstrBot 构建默认请求前记录本轮 LLM 请求来源。"""
        origin = (
            LLM_REQUEST_ORIGIN_PLUGIN
            if event.get_extra("provider_request") is not None
            else LLM_REQUEST_ORIGIN_CHAT
        )
        event.set_extra(LLM_REQUEST_ORIGIN_EXTRA_KEY, origin)

        # AstrBot 对流式结果不会执行装饰钩子，因此要在代理开始消费
        # 响应流之前拦截该事件的发送过程。
        send_streaming = getattr(event, "send_streaming", None)
        if callable(send_streaming) and not event.get_extra(
            "meme_manager_stream_filter_installed"
        ):

            async def send_filtered_stream(generator, *args, **kwargs):
                filtered = self._filter_meme_stream(event, generator)
                try:
                    return await send_streaming(filtered, *args, **kwargs)
                finally:
                    await filtered.aclose()

            event.send_streaming = send_filtered_stream
            event.set_extra("meme_manager_stream_filter_installed", True)

    def _scope_allows_llm_origin(self, event: AstrMessageEvent) -> bool:
        """判断当前配置是否允许处理该来源的 LLM 请求。"""
        scope = normalize_trigger_scope(getattr(self, "trigger_scope", None))
        origin = event.get_extra(LLM_REQUEST_ORIGIN_EXTRA_KEY)
        if origin == LLM_REQUEST_ORIGIN_CHAT:
            return True
        return (
            scope == TRIGGER_SCOPE_CHAT_AND_PLUGIN
            and origin == LLM_REQUEST_ORIGIN_PLUGIN
        )

    def _should_attach_for_result(
        self,
        event: AstrMessageEvent,
        result: Any,
    ) -> bool:
        """仅允许配置范围内的内部聊天或插件 LLM 结果附加表情。"""
        if result is None:
            return False
        if not self._scope_allows_llm_origin(event):
            return False
        content_type = getattr(result, "result_content_type", None)
        content_type_name = str(getattr(content_type, "name", content_type) or "")
        if content_type_name == "STREAMING_FINISH":
            return True
        checker = getattr(result, "is_llm_result", None)
        if callable(checker):
            return bool(checker())
        return content_type_name == "LLM_RESULT"
