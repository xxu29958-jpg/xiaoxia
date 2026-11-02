import asyncio
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .main_support import CAPTION_FAILED_SENTINEL
from .turn_state import (
    TurnSession,
)


class ContinuousMessagePluginCaptions:
    """Captions; shared runtime resources belong to the facade."""

    def _caption_probe_request(self, event: AstrMessageEvent, session: TurnSession):
        req = type("ContinuousMessageCaptionProbe", (), {})()
        req.session_id = event.unified_msg_origin
        req.image_urls = session.all_images()
        req.extra_user_content_parts = []
        return req

    @staticmethod
    def _caption_task_key(
        session: TurnSession, item, image_url: str
    ) -> tuple[str, str, str]:
        return (session.turn_id, str(item.item_id), str(image_url))

    def _cancel_caption_tasks_all(self) -> None:
        for task in list(self._caption_tasks.values()):
            if not task.done():
                task.cancel()
        self._caption_tasks.clear()

    def _cancel_caption_tasks_for_session(self, session: TurnSession) -> None:
        prefix = session.turn_id
        for key, task in list(self._caption_tasks.items()):
            if key[0] != prefix:
                continue
            if not task.done():
                task.cancel()
            self._caption_tasks.pop(key, None)

    def _cancel_caption_tasks_for_item(self, session: TurnSession, item) -> None:
        for image_url in list(item.images):
            key = self._caption_task_key(session, item, image_url)
            task = self._caption_tasks.pop(key, None)
            if task and not task.done():
                task.cancel()

    def _find_items_by_message_id(self, session: TurnSession, message_id: Any) -> list:
        return [
            item for item in session.items if str(item.message_id) == str(message_id)
        ]

    def _start_caption_tasks_for_item(
        self, event: AstrMessageEvent, session: TurnSession, item
    ) -> None:
        if not item.images:
            return
        req = self._caption_probe_request(event, session)
        if not self.caption_bridge.get_caption_plan(event, req):
            return
        for image_url in item.images:
            if item.image_captions.get(image_url) is not None:
                continue
            key = self._caption_task_key(session, item, image_url)
            existing = self._caption_tasks.get(key)
            if existing and not existing.done():
                continue
            task = asyncio.create_task(
                self._caption_image_task(event, session, item, image_url)
            )
            self._caption_tasks[key] = task

    def _start_caption_tasks_for_session(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        for item in session.items:
            self._start_caption_tasks_for_item(event, session, item)

    async def _caption_image_task(
        self, event: AstrMessageEvent, session: TurnSession, item, image_url: str
    ) -> None:
        key = self._caption_task_key(session, item, image_url)
        req = self._caption_probe_request(event, session)
        try:
            caption = await self.caption_bridge.caption_image_for_request(
                event,
                req,
                image_url,
                should_continue=lambda: (
                    image_url in item.images
                    and item in session.items
                    and self._session_accepts_caption(session)
                ),
            )
            if caption is None:
                return
            if not (
                image_url in item.images
                and item in session.items
                and self._session_accepts_caption(session)
            ):
                return
            item.image_captions[image_url] = caption
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if (
                image_url in item.images
                and item in session.items
                and self._session_accepts_caption(session)
            ):
                item.image_captions[image_url] = CAPTION_FAILED_SENTINEL
            detail = self.caption_bridge.format_exception(exc)
            logger.warning(
                f"[消息防抖动] 预转述图片失败 | turn_id={session.turn_id} | {detail}"
            )
        finally:
            self._caption_tasks.pop(key, None)

    def _session_accepts_caption(self, session: TurnSession) -> bool:
        if session.cancelled_reason == "all_recalled" or session.invalidated:
            return False
        if self.turns.get_session(session.uid) is session:
            return True
        return self._session_is_current_valid(session)

    async def _wait_prestarted_captions(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> bool | None:
        self._start_caption_tasks_for_session(event, session)
        while True:
            if not self._session_accepts_caption(session):
                self._cancel_caption_tasks_for_session(session)
                return None
            pending = []
            for item in session.items:
                for image_url in item.images:
                    if item.image_captions.get(image_url) is not None:
                        continue
                    task = self._caption_tasks.get(
                        self._caption_task_key(session, item, image_url)
                    )
                    if task and not task.done():
                        pending.append(task)
            if not pending:
                return True
            done, _ = await asyncio.wait(pending, timeout=0.2)
            if not done:
                continue
            for task in done:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
