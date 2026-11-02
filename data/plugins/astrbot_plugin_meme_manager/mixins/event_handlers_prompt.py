from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest


class EventHandlerMixinPrompt:
    """Prompt; shared runtime resources belong to the facade."""

    async def _inject_meme_prompt_impl(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        if not self._scope_allows_llm_origin(event):
            self._remove_semantic_tool(req)
            req.system_prompt = self._strip_meme_prompt(req.system_prompt)
            event.set_extra("meme_manager_semantic_active", False)
            event.set_extra("meme_manager_semantic_mode", "")
            return
        event.set_extra("meme_manager_resolved_persona_id", None)
        try:
            # 与 AstrBot 的会话覆盖和继承默认人格选择保持一致。
            (
                persona_id,
                _,
                _,
                _,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=event.unified_msg_origin,
                conversation_persona_id=getattr(req.conversation, "persona_id", None),
                platform_name=event.get_platform_name(),
                provider_settings=self.context.get_config(event.unified_msg_origin).get(
                    "provider_settings", {}
                ),
            )
            event.set_extra("meme_manager_resolved_persona_id", persona_id or "")
        except Exception:
            logger.warning(
                "无法解析表情选择使用的当前人格",
                exc_info=True,
            )
        self._apply_request_prompt(req, event)
        if hasattr(event, "set_extra"):
            event.set_extra(
                "meme_manager_emotion_context_lines",
                self._collect_emotion_context_lines_from_request(req),
            )
            reply_model = str(getattr(req, "model", "") or "").strip()
            if reply_model:
                event.set_extra("meme_manager_reply_model", reply_model)

        if not self.emotion_llm_enabled:
            return

        if not str(self.emotion_llm_provider_id or "").strip():
            # AstrBot 的本轮 Provider/模型覆盖只存在于事件和 ProviderRequest 中。
            # 必须在回复请求发出前缓存，不能仅查询会话默认 Provider。
            await self._resolve_emotion_llm_provider_id(event)

    async def _resolve_emotion_llm_provider_id(self, event: AstrMessageEvent) -> str:
        """返回情感辅助模型；配置留空时复用本轮真实回复 Provider。"""
        configured_id = str(self.emotion_llm_provider_id or "").strip()
        if configured_id:
            return configured_id

        cached_id = str(event.get_extra("meme_manager_reply_provider_id") or "").strip()
        if cached_id:
            return cached_id

        selected_id = str(event.get_extra("selected_provider") or "").strip()
        if selected_id:
            get_provider = getattr(self.context, "get_provider_by_id", None)
            if not callable(get_provider) or get_provider(selected_id) is not None:
                event.set_extra("meme_manager_reply_provider_id", selected_id)
                logger.info(
                    "[meme_manager] 情感模型未单独配置，本轮复用事件指定回复模型: %s",
                    selected_id,
                )
                return selected_id
            logger.warning(
                "[meme_manager] 事件指定的回复模型不存在，回退会话模型: %s",
                selected_id,
            )

        try:
            provider_id = str(
                await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
                or ""
            ).strip()
        except Exception as exc:
            logger.warning(
                "[meme_manager] 情感模型未配置，且无法取得当前回复模型: %s",
                exc,
            )
            return ""

        if not provider_id:
            logger.warning("[meme_manager] 情感模型未配置，当前回复模型也不可用")
            return ""

        event.set_extra("meme_manager_reply_provider_id", provider_id)
        logger.info(
            "[meme_manager] 情感模型未单独配置，本轮复用会话回复模型: %s",
            provider_id,
        )
        return provider_id
