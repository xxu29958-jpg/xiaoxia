"""Automatic reply reactions use the same visual selector as scheduled messages."""

import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import LLMResponse

from ..backend.semantic.models import (
    compact_semantic_query,
    extract_visible_semantic_reply,
)
from ..backend.semantic.query import remember_candidates
from ..meme_usage import event_scope
from ..reaction_decision import decide_reaction


class EventHandlerMixinSelection:
    async def _decide_event_reaction(self, event, text):
        """Apply one shared decision before either automatic or tool-selected images.

        Args:
            event: Current native chat event with the trusted owner scope.
            text: Already generated reply; image markers are not emotional evidence.

        Returns:
            Requested/react intent, or None for a normal text-only reply.
        """
        event.set_extra("meme_manager_reaction", None)
        try:
            turn = self.meme_usage.observe_event(event)
            if event.get_extra("meme_manager_suppress"):
                return None
            current = event.get_extra("turnflow_user_text")
            if current is None:
                current = event.get_message_str()
            decision = await decide_reaction(
                self.context,
                self.semantic_vision_provider_id
                or await self._resolve_emotion_llm_provider_id(event),
                text=extract_visible_semantic_reply(text),
                current_user=current,
                recent_context="\n".join(
                    event.get_extra("meme_manager_emotion_context_lines") or []
                ),
                automatic_allowed=self.meme_usage.automatic_ready(
                    event_scope(event), turn
                ),
                choice=event.get_extra("ada_reply_reaction"),
            )
            event.set_extra("meme_manager_reaction", decision)
            logger.info(
                "Sticker intent: %s", decision["action"] if decision else "skip"
            )
            return decision
        except Exception as exc:
            logger.warning(
                "Optional sticker cadence unavailable: %s", type(exc).__name__
            )
            return None

    async def _resp_semantic_llm_impl(
        self, event: AstrMessageEvent, response: LLMResponse, text: str
    ):
        """Consider a real reaction image without requiring a chat-model tool call.

        Args:
            event: Current chat event and current user's output preferences.
            response: The original response, decorated through the normal pipeline.
            text: Visible text to preserve when no appropriate picture is available.
        """
        visible_text = extract_visible_semantic_reply(text)
        current = event.get_extra("turnflow_user_text")
        if current is None:
            current = event.get_message_str()
        query = compact_semantic_query(current + " " + visible_text)
        selected_id = ""
        if event.get_extra("meme_manager_suppress"):
            return await self._resp_semantic_impl(event, response, visible_text)
        try:
            selected = await asyncio.wait_for(
                self.select_context_meme(
                    text=visible_text,
                    query=query,
                    session_id=event.session_id,
                    usage_scope=event_scope(event),
                    persona_id=self._resolve_persona_id(event=event),
                    reaction=event.get_extra("meme_manager_reaction"),
                    conversation_hint=(
                        self._build_emotion_llm_injection_suffix(event)
                        + "\nCurrent user: "
                        + current[:1000]
                    ),
                ),
                timeout=35,
            )
            if selected:
                remember_candidates(event, [selected["record"]])
                selected_id = selected["record"]["id"]
                if (
                    event.get_extra("ada_meme_only")
                    and selected["reaction"]["action"] == "requested"
                ):
                    text = ""
        except Exception as exc:
            logger.warning(
                "Automatic visual meme selection unavailable: %s", type(exc).__name__
            )
        if not selected_id and event.get_extra("ada_library_sticker"):
            text = "这张没挑出来，图库里暂时没有合适的。"
        marked_text = text + (f"\n&&{selected_id}&&" if selected_id else "")
        return await self._resp_semantic_impl(event, response, marked_text)
