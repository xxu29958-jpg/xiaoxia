import asyncio
from contextvars import ContextVar
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .caption_bridge import OrderedCaptionBridge
from .continuation_predictor import ContinuationPredictor
from .forward_handler import ForwardHandler
from .main_captions import ContinuousMessagePluginCaptions
from .main_delivery import ContinuousMessagePluginDelivery
from .main_flush import ContinuousMessagePluginFlush
from .main_history import ContinuousMessagePluginHistory
from .main_interruption import ContinuousMessagePluginInterruption
from .main_lifecycle import ContinuousMessagePluginLifecycle
from .main_recall import ContinuousMessagePluginRecall
from .main_seed import ContinuousMessagePluginSeed
from .main_support import CAPTION_FAILED_SENTINEL as CAPTION_FAILED_SENTINEL
from .main_support import INTERRUPTIBLE_DELIVERY_STATES as INTERRUPTIBLE_DELIVERY_STATES
from .message_parser import MessageParser
from .recall_adapter import RecallAdapter
from .turn_state import (
    EXTRA_CAPTION_HANDLED,
    EXTRA_SENT_IDS,
    EXTRA_TURN_ID,
    EXTRA_TURN_ITEMS,
    TurnSession,
    TurnStateStore,
)


@register(
    "astrbot_plugin_turnflow",
    "Yuimi-chaya",
    "私聊对话轮次管理：动态收集、回复中断、部分输出撤回与历史回滚保护",
    "1.0.0",
)
class ContinuousMessagePlugin(
    ContinuousMessagePluginLifecycle,
    ContinuousMessagePluginCaptions,
    ContinuousMessagePluginSeed,
    ContinuousMessagePluginInterruption,
    ContinuousMessagePluginHistory,
    ContinuousMessagePluginDelivery,
    ContinuousMessagePluginRecall,
    ContinuousMessagePluginFlush,
    Star,
):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        self.debounce_time = float(self.config.get("debounce_time", 2.0))
        self.command_prefixes = self.config.get("command_prefixes", ["/"])
        self.enable_plugin = self.config.get("enable", True)
        self.enable_forward_analysis = self.config.get("enable_forward_analysis", True)
        self.enable_typing_detection = self.config.get("enable_typing_detection", True)
        self.max_typing_wait = float(self.config.get("max_typing_wait", 60.0))
        self.max_collection_wait = max(
            self.debounce_time, float(self.config.get("max_collection_wait", 30.0))
        )
        self.enable_recall_filter = self.config.get("enable_recall_filter", True)
        self.history_rollback_delays = self.config.get(
            "history_rollback_delays", [0.2, 1.0, 3.0]
        )
        self.invalidated_turn_retention_seconds = max(
            10.0,
            float(self.config.get("invalidated_turn_retention_seconds", 180.0)),
        )

        reply_format = (
            '<quoted_message sender="{sender_name}">{full_text}</quoted_message>'
        )

        self.turns = TurnStateStore()
        self.sessions: dict[str, TurnSession] = self.turns.sessions

        image_comp = None
        plain_comp = None
        try:
            from astrbot.api.message_components import Image, Plain

            image_comp = Image
            plain_comp = Plain
        except ImportError:
            try:
                from astrbot.api.message import Image, Plain

                image_comp = Image
                plain_comp = Plain
            except ImportError:
                logger.error("[消息防抖动] 严重: 组件导入失败")

        self.parser = MessageParser(
            image_component=image_comp,
            plain_component=plain_comp,
            plugin_config=self.config,
        )
        self.forward_handler = ForwardHandler(reply_format=reply_format)
        self.predictor = ContinuationPredictor(self.config)
        self.caption_bridge = OrderedCaptionBridge(context, self.config)
        self.recall_adapter = RecallAdapter(self.config)
        self._send_wrappers: dict[str, list[dict]] = {}
        self._bot_dispatchers: dict[tuple[int, str], dict] = {}
        self._event_wrappers: dict[str, list[tuple[Any, str, Any]]] = {}
        self._history_generations: dict[tuple[str, str], int] = {}
        self._caption_tasks: dict[tuple[str, str, str], asyncio.Task] = {}
        self._unsent_cleanup_tasks: dict[str, asyncio.Task] = {}
        self._send_context = ContextVar("continuous_message_send", default=None)
        self._background_tasks: set[asyncio.Task] = set()
        self._pipeline_owners: dict[str, asyncio.Task] = {}
        self._terminated = False

        logger.info(
            f"[TurnFlow] v1.0.0 加载 | 防抖: {self.debounce_time}s "
            f"| 动态防抖: {self.predictor.enabled} | 有序图片转述: {self.caption_bridge.enabled} "
            f"| 拟人化撤回: {self.recall_adapter.enabled}"
        )

    async def terminate(self):
        self._terminated = True
        self._pipeline_owners.clear()
        pending_tasks = set(self._background_tasks)
        pending_tasks.update(self._caption_tasks.values())
        pending_tasks.update(self._unsent_cleanup_tasks.values())
        for session in (
            list(self.sessions.values())
            + list(self.turns.pending_turns.values())
            + list(self.turns.active_turns.values())
        ):
            pending_tasks.update(
                task for task in (session.timer_task, session.cleanup_task) if task
            )
        for session in list(self.sessions.values()):
            if session.timer_task:
                session.timer_task.cancel()
        for session in list(self.turns.pending_turns.values()) + list(
            self.turns.active_turns.values()
        ):
            if session.timer_task:
                session.timer_task.cancel()
            if session.cleanup_task:
                session.cleanup_task.cancel()
        for turn_id in list(self._event_wrappers.keys()):
            self._restore_event_capture(turn_id)
        for turn_id in list(self._send_wrappers.keys()):
            self._restore_send_capture(turn_id)
        for task in list(self._unsent_cleanup_tasks.values()):
            if not task.done():
                task.cancel()
        self._unsent_cleanup_tasks.clear()
        self._cancel_caption_tasks_all()
        tasks = list(pending_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for key, dispatcher_info in list(self._bot_dispatchers.items()):
            try:
                setattr(
                    dispatcher_info["bot"],
                    dispatcher_info["method_name"],
                    dispatcher_info["original"],
                )
            except Exception:
                pass
            self._bot_dispatchers.pop(key, None)

    async def _caption_before_core_image_flow(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> str | None:
        req = self._caption_probe_request(event, session)
        try:
            return await self.caption_bridge.build_ordered_caption_text(
                event,
                req,
                session.items_as_dicts(),
                should_continue=lambda: self._session_is_current_valid(session),
            )
        except Exception as exc:
            logger.warning(f"[消息防抖动] 结算阶段图片转述失败，降级为保留原图: {exc}")
            return ""

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=50)
    async def handle_private_msg(self, event: AstrMessageEvent):
        if not self.enable_plugin or self.debounce_time <= 0:
            return

        uid = event.unified_msg_origin

        if self.enable_typing_detection and self.parser.is_typing_event(event):
            raw = event.message_obj.raw_message
            status_text = raw.get("status_text", "")
            is_typing = "正在输入" in status_text
            session = self.turns.get_session(uid)
            if session:
                session.is_typing = is_typing
                duration = (
                    self.max_typing_wait if is_typing else self._calc_delay(session)
                )
                self._reset_timer(session, duration)
                logger.debug(
                    f"[消息防抖动] 输入状态更新 | is_typing={is_typing} | timer={duration}s | uid={uid}"
                )
            event.stop_event()
            return

        if self.enable_recall_filter and self.parser.is_recall_event(event):
            await self._handle_recall_event(event)
            return

        parts = self.parser.parse_message_parts(event.message_obj)
        raw_text = "".join(part["text"] for part in parts if part["type"] == "text")
        current_urls = [
            part["url"] for part in parts if part["type"] == "image" and part.get("url")
        ]
        has_image = bool(current_urls)
        if not raw_text:
            raw_text = (event.message_str or "").strip()
            if raw_text:
                parts.insert(0, {"type": "text", "text": raw_text})
        original_text = raw_text
        original_image_count = len(current_urls)
        raw_text, current_urls, has_image = await self._append_forward_or_reply(
            event, raw_text, current_urls
        )
        if raw_text != original_text:
            prefix = (
                raw_text[: -(len(original_text) + 1)] if original_text else raw_text
            )
            parts.insert(0, {"type": "text", "text": prefix})
        parts.extend(
            {"type": "image", "url": url} for url in current_urls[original_image_count:]
        )

        if self.parser.is_command(raw_text, self.command_prefixes):
            session = self.turns.get_session(uid)
            if session:
                if session.timer_task:
                    session.timer_task.cancel()
                session.flush_event.set()
            self._stop_active_turns(uid, exclude_event=event)
            return

        if not raw_text and not has_image:
            return

        message_id = self.parser.get_message_id(event)
        session = self.turns.get_session(uid)
        if session and session.cancelled_reason == "all_recalled":
            self.turns.pop_session(uid)
            session = None
        if session:
            item = session.add_item(message_id, raw_text, current_urls, parts=parts)
            self._start_caption_tasks_for_item(event, session, item)
            delay = self._calc_delay(session)
            self._reset_timer(session, delay)
            logger.debug(
                f"[消息防抖动] 追加消息 | turn_id={session.turn_id} | message_id={message_id} "
                f"| items={len(session.items)} | delay={delay}s | 图片={len(current_urls)}"
            )
            event.stop_event()
            return

        source_sessions = [
            source
            for source in self.turns.sessions_for_uid(uid)
            if self._session_is_interruptible(source)
        ]
        session = self.turns.create_session(uid)
        seed_items = self._collect_seed_items(uid)
        self._inherit_history_baseline(session, source_sessions)
        self._stop_active_turns(uid, exclude_event=event)
        if seed_items:
            session.seed_from(seed_items)
            self._start_caption_tasks_for_session(event, session)
            logger.info(
                f"[消息防抖动] 新轮次继承旧用户消息 | inherited={len(seed_items)} | uid={uid}"
            )
        item = session.add_item(message_id, raw_text, current_urls, parts=parts)
        self._start_caption_tasks_for_item(event, session, item)
        delay = self._calc_delay(
            session, completed_audio=bool(event.get_extra("voice_input", False))
        )
        self._reset_timer(session, delay)
        logger.info(
            f"[消息防抖动] 开始收集 | turn_id={session.turn_id} | delay={delay}s | uid={uid}"
        )

        await self._flush_collected_session(event, session)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        if not self.turns.is_valid(turn_id) or not session:
            event.stop_event()
            logger.info(f"[消息防抖动] 已阻断失效 LLM 请求 | turn_id={turn_id}")
            return
        session.delivery_state = "llm_request"
        self._cancel_unsent_cleanup(turn_id)
        self._track_pipeline_completion(event, session)
        if not await self._reconcile_inherited_history(event, req, session):
            self.turns.invalidate(turn_id)
            self._schedule_invalidated_cleanup(session)
            event.stop_event()
            return
        if not session.history_snapshot_inherited:
            # The core session lock is held here; pre-debounce snapshots can
            # predate the previous turn's final history write.
            session.history_snapshot = None
            session.history_conversation_id = None
            event.set_extra("provider_request", req)
        await self._snapshot_history(event, session)
        self._advance_history_generation(event, session)
        self._install_event_capture(event, session)
        self._install_send_capture(event, session)
        if not getattr(req, "session_id", ""):
            req.session_id = event.unified_msg_origin
        if not self.turns.is_valid(turn_id):
            event.stop_event()
            return
        if event.get_extra(EXTRA_CAPTION_HANDLED, False):
            original_images = set(session.all_images())
            req.image_urls = [
                url for url in (req.image_urls or []) if url not in original_images
            ]
        else:
            await self.caption_bridge.apply(
                event,
                req,
                event.get_extra(EXTRA_TURN_ITEMS, []),
                should_continue=lambda: self._session_is_current_valid(session),
            )
        if not self.turns.is_valid(turn_id):
            req.image_urls = []
            event.stop_event()

    @filter.on_llm_response(priority=100000)
    async def on_llm_response(self, event: AstrMessageEvent, resp) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        if not session:
            return
        if not self._session_is_current_valid(session):
            session.delivery_state = "invalidated_response"
            try:
                result_chain = getattr(resp, "result_chain", None)
                if result_chain is not None and hasattr(result_chain, "chain"):
                    result_chain.chain = []
                resp.completion_text = ""
                for attr in ("tools_call_args", "tools_call_name", "tools_call_ids"):
                    if hasattr(resp, attr):
                        setattr(resp, attr, [])
            except Exception as exc:
                logger.debug(
                    f"[消息防抖动] 清空迟到失效响应时发生兼容性异常 | "
                    f"turn_id={turn_id} | {exc}"
                )
            event.stop_event()
            await self._rollback_history(event, session)
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
            self._restore_send_capture(turn_id)
            logger.info(
                f"[消息防抖动] 已阻断迟到的失效 LLM 响应并安排返回后回滚 | "
                f"turn_id={turn_id}"
            )
            return

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        if not session and event.get_extra("continuous_message_committed", False):
            return
        if self.turns.is_valid(turn_id) and session:
            session.delivery_state = "decorating"
            self._install_event_capture(event, session)
            self._install_send_capture(event, session)
            cleanup_delay = None
            try:
                result = event.get_result()
                if not getattr(
                    result, "async_stream", None
                ) and not self._result_has_sendable_content(result):
                    cleanup_delay = 0.5
            except Exception:
                pass
            if cleanup_delay is not None:
                self._schedule_unsent_cleanup(session, delay=cleanup_delay)
            return
        self._restore_send_capture(turn_id)
        self._cancel_unsent_cleanup(turn_id)
        result = event.get_result()
        if result is not None:
            result.chain = []
        if session:
            await self._rollback_history(event, session)
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
        event.stop_event()
        logger.info(f"[消息防抖动] 已清空失效轮次发送前结果 | turn_id={turn_id}")

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        sent_ids = list(event.get_extra(EXTRA_SENT_IDS, []) or [])
        if session:
            for msg_id in sent_ids:
                if msg_id not in session.sent_message_ids:
                    session.sent_message_ids.append(msg_id)
        if session and session.delivery_state == "send_failed":
            self._cancel_unsent_cleanup(turn_id)
            self._restore_send_capture(turn_id)
            await self._rollback_history(event, session, force=True)
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
            return
        if session and self._session_is_current_valid(session):
            self._cancel_unsent_cleanup(turn_id)
            if session.send_depth:
                return
            if session.delivery_state == "provider_failed":
                return
            self._commit_turn(event, session)
            return
        if session:
            self._cancel_unsent_cleanup(turn_id)
            self._restore_send_capture(turn_id)
            recalled = await self.recall_adapter.recall_messages(
                event, session.sent_message_ids
            )
            if not recalled and session.sent_message_ids:
                logger.warning(
                    f"[消息防抖动] 失效轮次有已发消息但未能撤回 | turn_id={turn_id}"
                )
            await self._rollback_history(
                event, session, force=session.delivery_state == "send_failed"
            )
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
