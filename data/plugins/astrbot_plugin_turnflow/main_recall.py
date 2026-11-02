from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .message_parser import IS_AIOCQHTTP

if IS_AIOCQHTTP:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )


class ContinuousMessagePluginRecall:
    """Recall; shared runtime resources belong to the facade."""

    @staticmethod
    def _collect_message_ids(result: Any, target: list[Any]) -> None:
        if isinstance(result, dict):
            for key in ("message_id", "msg_id"):
                if result.get(key) is not None and result[key] not in target:
                    target.append(result[key])
            data = result.get("data")
            if isinstance(data, dict):
                for key in ("message_id", "msg_id"):
                    if data.get(key) is not None and data[key] not in target:
                        target.append(data[key])
        elif isinstance(result, list):
            for item in result:
                ContinuousMessagePluginRecall._collect_message_ids(item, target)

    async def _append_forward_or_reply(
        self, event: AstrMessageEvent, raw_text: str, current_urls: list[str]
    ):
        if not (
            self.enable_forward_analysis
            and IS_AIOCQHTTP
            and isinstance(event, AiocqhttpMessageEvent)
        ):
            return raw_text, current_urls, bool(current_urls)
        forward_text = ""
        forward_images: list[str] = []
        forward_id = await self.forward_handler.detect_forward_message(event)
        if forward_id:
            try:
                (
                    forward_text,
                    forward_images,
                ) = await self.forward_handler.extract_forward_content(
                    event, forward_id
                )
                if forward_text or forward_images:
                    logger.info(
                        f"[消息防抖动] 检测到合并转发 | 文本: {len(forward_text)}字 | 图片: {len(forward_images)}张"
                    )
            except Exception as exc:
                logger.error(f"[消息防抖动] 提取合并转发失败: {exc}")
        else:
            reply_text, reply_images = await self.forward_handler.extract_reply_content(
                event
            )
            if reply_text or reply_images:
                forward_text = reply_text
                forward_images = reply_images
        if forward_text:
            raw_text = forward_text + ("\n" + raw_text if raw_text else "")
        if forward_images:
            current_urls.extend(forward_images)
        return raw_text, current_urls, bool(current_urls)

    async def _handle_recall_event(self, event: AstrMessageEvent) -> None:
        recalled_mid = self.parser.get_recalled_message_id(event)
        uid = event.unified_msg_origin
        session = self.turns.get_session(uid)
        if recalled_mid is not None and session:
            removed_items = self._find_items_by_message_id(session, recalled_mid)
            for item in removed_items:
                self._cancel_caption_tasks_for_item(session, item)
            if session.remove_message(recalled_mid):
                logger.info(
                    f"[消息防抖动] 已过滤撤回消息 | message_id={recalled_mid} | 剩余 {len(session.items)} 条"
                )
                if not session.items:
                    session.cancelled_reason = "all_recalled"
                    if session.timer_task:
                        session.timer_task.cancel()
                    self._cancel_caption_tasks_for_session(session)
                    if self.turns.get_session(uid) is session:
                        self.turns.pop_session(uid)
                    session.flush_event.set()
            else:
                logger.debug(
                    f"[消息防抖动] 收到撤回通知但未找到待合并消息 | message_id={recalled_mid}"
                )
            event.stop_event()
            return

        seed_items, matched_active = self._collect_seed_items_excluding_message(
            uid, recalled_mid
        )
        if recalled_mid is not None and matched_active and seed_items:
            source_sessions = [
                source
                for source in self.turns.sessions_for_uid(uid)
                if self._session_is_interruptible(source)
            ]
            new_session = self.turns.create_session(uid)
            self._inherit_history_baseline(new_session, source_sessions)
            self._stop_active_turns(uid, exclude_event=event)
            new_session.seed_from(seed_items)
            self._start_caption_tasks_for_session(event, new_session)
            delay = self._calc_delay(new_session)
            self._reset_timer(new_session, delay)
            logger.info(
                f"[消息防抖动] 活跃轮次消息被撤回，继承剩余用户消息重新防抖 | "
                f"message_id={recalled_mid} | inherited={len(seed_items)} | delay={delay}s"
            )
            await self._flush_collected_session(event, new_session)
            return

        if recalled_mid is not None and matched_active:
            invalidated = self._stop_active_turns(uid, exclude_event=event)
            if invalidated:
                logger.info(
                    f"[消息防抖动] 活跃轮次消息已全部撤回，静默中断 | message_id={recalled_mid}"
                )
        elif recalled_mid is not None:
            logger.debug(
                f"[消息防抖动] 收到不属于当前防抖轮次的撤回通知，已静默忽略 | message_id={recalled_mid}"
            )
        event.stop_event()
