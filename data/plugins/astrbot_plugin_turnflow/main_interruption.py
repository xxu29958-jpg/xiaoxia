import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .main_support import INTERRUPTIBLE_DELIVERY_STATES
from .turn_state import (
    TurnSession,
)


class ContinuousMessagePluginInterruption:
    """Interruption; shared runtime resources belong to the facade."""

    def _session_is_current_valid(self, session: TurnSession) -> bool:
        return (
            bool(session)
            and not session.invalidated
            and self.turns.is_valid(session.turn_id)
            and self.turns.get_turn(session.turn_id) is session
        )

    def _session_is_interruptible(self, session: TurnSession) -> bool:
        return (
            self._session_is_current_valid(session)
            and session.delivery_state in INTERRUPTIBLE_DELIVERY_STATES
        )

    def _stop_active_turns(
        self, uid: str, exclude_event: AstrMessageEvent | None = None
    ) -> list[str]:
        sessions_to_recall = [
            session
            for session in self.turns.sessions_for_uid(uid)
            if self._session_is_interruptible(session)
        ]
        if not sessions_to_recall:
            return []
        invalidated = []
        for session in sessions_to_recall:
            if session.turn_id in self.turns.invalid_turns:
                continue
            self.turns.invalidate(session.turn_id)
            invalidated.append(session.turn_id)
        if not invalidated:
            return []
        for session in sessions_to_recall:
            self._cancel_caption_tasks_for_session(session)
        try:
            from astrbot.core.utils.active_event_registry import active_event_registry

            active_event_registry.stop_all(uid, exclude=exclude_event)
        except Exception as exc:
            logger.debug(
                f"[消息防抖动] active_event_registry 不可用，已降级为本地失效标记: {exc}"
            )
        logger.info(f"[消息防抖动] 已标记旧轮次失效 | uid={uid} | turns={invalidated}")
        for session in sessions_to_recall:
            self._cancel_unsent_cleanup(session.turn_id)
        if exclude_event is not None:
            for session in sessions_to_recall:
                self._spawn_background(
                    self._recall_invalidated_turn(exclude_event, session)
                )
        return invalidated

    async def _recall_invalidated_turn(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        await asyncio.sleep(0)
        if session.sent_message_ids:
            await self.recall_adapter.recall_messages(event, session.sent_message_ids)
        await self._rollback_history(event, session)
        self._schedule_rollback_retries(event, session)
        self._schedule_invalidated_cleanup(session)

    def _schedule_invalidated_cleanup(self, session: TurnSession) -> None:
        if session.cleanup_task and not session.cleanup_task.done():
            return
        session.cleanup_task = self._spawn_background(
            self._cleanup_invalidated_turn_later(
                session.turn_id,
                self.invalidated_turn_retention_seconds,
            )
        )

    async def _cleanup_invalidated_turn_later(self, turn_id: str, delay: float) -> None:
        await asyncio.sleep(max(delay, 0.0))
        session = self.turns.get_turn(turn_id)
        if session and session.invalidated:
            # Keep the per-event invalidation guard for late segmented sends.
            self._event_wrappers.pop(turn_id, None)
            self._restore_send_capture(turn_id)
            self.turns.finish(turn_id)

    def _cancel_unsent_cleanup(self, turn_id: str) -> None:
        task = self._unsent_cleanup_tasks.pop(turn_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_unsent_cleanup(
        self,
        session: TurnSession,
        delay: float = 8.0,
    ) -> None:
        turn_id = session.turn_id
        task = self._unsent_cleanup_tasks.get(turn_id)
        if task and not task.done():
            return
        self._unsent_cleanup_tasks[turn_id] = asyncio.create_task(
            self._cleanup_unsent_turn_later(turn_id, delay)
        )

    async def _cleanup_unsent_turn_later(self, turn_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(max(delay, 0.0))
            session = self.turns.get_turn(turn_id)
            if not session:
                return
            if not self._session_is_current_valid(session):
                return
            if session.sent_message_ids or session.send_depth:
                return
            owner = self._pipeline_owners.get(turn_id)
            if owner is not None and not owner.done():
                return
            if session.delivery_state not in {"llm_request", "decorating"}:
                return
            session.delivery_state = "empty_result"
            self._restore_event_capture(turn_id)
            self._restore_send_capture(turn_id)
            self.turns.finish(turn_id)
            logger.info(
                f"[消息防抖动] 本轮未进入发送阶段，已清理空结果轮次 | turn_id={turn_id}"
            )
        except asyncio.CancelledError:
            raise
        finally:
            self._unsent_cleanup_tasks.pop(turn_id, None)

    @staticmethod
    def _result_has_sendable_content(result: Any) -> bool:
        if result is None:
            return False
        chain = getattr(result, "chain", None)
        if chain is None:
            return True
        for component in list(chain or []):
            if isinstance(component, str):
                if component.strip():
                    return True
                continue
            if isinstance(component, dict):
                comp_type = str(component.get("type", "")).lower()
                data = component.get("data") or {}
                text = str(data.get("text", component.get("text", ""))).strip()
                if text:
                    return True
                if comp_type and comp_type not in {"reply", "at", "plain", "text"}:
                    return True
                continue
            comp_type = type(component).__name__.lower()
            text = str(getattr(component, "text", "") or "").strip()
            if text:
                return True
            if comp_type not in {"reply", "at", "plain", "text"}:
                return True
        return False
