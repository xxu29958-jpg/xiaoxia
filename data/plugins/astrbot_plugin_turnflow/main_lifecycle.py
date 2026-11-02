import asyncio
import time

from astrbot.api import logger

from .turn_state import (
    TurnSession,
)


class ContinuousMessagePluginLifecycle:
    """Lifecycle; shared runtime resources belong to the facade."""

    def _spawn_background(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def completed(finished):
            self._background_tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.warning(f"[消息防抖动] 后台清理失败: {finished.exception()}")

        task.add_done_callback(completed)
        return task

    def _track_pipeline_completion(self, event, session: TurnSession) -> None:
        owner = asyncio.current_task()
        if owner is None or session.turn_id in self._pipeline_owners:
            return
        self._pipeline_owners[session.turn_id] = owner

        def completed(task):
            if self._pipeline_owners.pop(session.turn_id, None) is not task:
                return
            if not self._terminated:
                failed = task.cancelled() or task.exception() is not None
                self._spawn_background(self._finish_pipeline(event, session, failed))

        owner.add_done_callback(completed)

    async def _finish_pipeline(
        self, event, session: TurnSession, failed: bool = False
    ) -> None:
        if self.turns.get_turn(session.turn_id) is not session:
            return
        if failed or session.delivery_state == "provider_failed":
            self.turns.invalidate(session.turn_id)
            self._cancel_unsent_cleanup(session.turn_id)
            await self._recall_failed_attempt(event, session, 0)
        if session.invalidated:
            await self._rollback_history(event, session)
            self._restore_send_capture(session.turn_id)
            self._schedule_invalidated_cleanup(session)
            return
        if session.send_succeeded:
            self._commit_turn(event, session)
            return
        session.delivery_state = "finished_without_commit"
        self._cancel_unsent_cleanup(session.turn_id)
        self._restore_event_capture(session.turn_id)
        self._restore_send_capture(session.turn_id)
        self.turns.finish(session.turn_id)

    async def _timer_coroutine(self, session: TurnSession, duration: float):
        try:
            await asyncio.sleep(duration)
            if self.turns.get_session(session.uid) is session:
                session.flush_event.set()
        except asyncio.CancelledError:
            pass

    def _reset_timer(self, session: TurnSession, duration: float) -> None:
        if session.timer_task:
            session.timer_task.cancel()
        remaining = self.max_collection_wait - (
            time.monotonic() - session.collection_started_at
        )
        duration = max(0.0, min(duration, remaining))
        session.timer_task = asyncio.create_task(
            self._timer_coroutine(session, duration)
        )

    def _calc_delay(self, session: TurnSession) -> float:
        tail_text = session.items[-1].text if session.items else ""
        has_image = bool(session.items[-1].images) if session.items else False
        return self.predictor.delay(
            tail_text, has_image=has_image, item_count=len(session.items)
        )
