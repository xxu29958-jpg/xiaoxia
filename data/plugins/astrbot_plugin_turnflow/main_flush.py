from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .turn_state import (
    EXTRA_CAPTION_HANDLED,
    TurnSession,
)


class ContinuousMessagePluginFlush:
    """Flush; shared runtime resources belong to the facade."""

    async def _flush_collected_session(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> bool:
        await session.flush_event.wait()

        current = self.turns.pop_session(session.uid, expected=session)
        if current is not session:
            if session.timer_task:
                session.timer_task.cancel()
            self._cancel_caption_tasks_for_session(session)
            event.stop_event()
            return False
        if session.timer_task:
            session.timer_task.cancel()

        if session.cancelled_reason == "all_recalled" or not session.has_content():
            self._cancel_caption_tasks_for_session(session)
            event.stop_event()
            logger.info(
                f"[消息防抖动] 本轮消息已全部撤回，静默结束 | turn_id={session.turn_id}"
            )
            return False

        self.turns.mark_pending(session)
        merged_text = self._format_message_batch(session)
        all_images = session.all_images()

        if not merged_text and not all_images:
            self._cancel_caption_tasks_for_session(session)
            self.turns.finish(session.turn_id)
            event.stop_event()
            return False

        if not self._session_is_current_valid(session):
            self._cancel_caption_tasks_for_session(session)
            event.stop_event()
            return False

        if session.all_images():
            caption_ready = await self._wait_prestarted_captions(event, session)
            if caption_ready is None:
                self._schedule_invalidated_cleanup(session)
                event.stop_event()
                return False
            caption_text = await self._caption_before_core_image_flow(event, session)
            if caption_text is None:
                self._schedule_invalidated_cleanup(session)
                event.stop_event()
                return False
            if caption_text:
                merged_text = (
                    f"{merged_text}\n{caption_text}" if merged_text else caption_text
                )
                original_images = set(session.all_images())
                all_images = [url for url in all_images if url not in original_images]
                event.set_extra(EXTRA_CAPTION_HANDLED, True)

        if not self._session_is_current_valid(session):
            event.stop_event()
            return False

        self._attach_turn_extras(event, session)
        await self._snapshot_history(event, session)

        self.turns.activate(session)
        self._attach_turn_extras(event, session)
        # Expose original text separately from the model's XML transport envelope.
        # Downstream search must not embed or truncate the envelope as user content.
        event.set_extra(
            "turnflow_user_text",
            "\n".join(
                part.get("text", "")
                for item in session.items
                for part in (item.parts or [{"type": "text", "text": item.text}])
                if part.get("type") == "text"
            ),
        )
        self.parser.reconstruct_event(event, merged_text, all_images)
        logger.info(
            f"[消息防抖动] 结算触发 | turn_id={session.turn_id} | items={len(session.items)} "
            f"| 图片={len(all_images)}"
        )
        return True
