from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .turn_state import (
    EXTRA_SENT_IDS,
    EXTRA_TURN_HISTORY,
    EXTRA_TURN_ID,
    EXTRA_TURN_ITEMS,
    TurnSession,
)


class ContinuousMessagePluginDelivery:
    """Delivery; shared runtime resources belong to the facade."""

    def _attach_turn_extras(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        items = session.items_as_dicts()
        event.set_extra(EXTRA_TURN_ID, session.turn_id)
        event.set_extra(EXTRA_TURN_ITEMS, items)
        event.set_extra(EXTRA_TURN_HISTORY, session.history_snapshot)
        event.set_extra(EXTRA_SENT_IDS, session.sent_message_ids)

    @staticmethod
    def _event_raw_message(event: AstrMessageEvent) -> dict[str, Any]:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        return raw if isinstance(raw, dict) else {}

    def _send_targets_event(
        self,
        event: AstrMessageEvent,
        action_name: str,
        args: tuple,
        kwargs: dict[str, Any],
    ) -> bool:
        raw = self._event_raw_message(event)
        expected_user_id = raw.get("user_id")
        expected_group_id = raw.get("group_id")
        target_user_id = kwargs.get("user_id")
        target_group_id = kwargs.get("group_id")
        if target_user_id is None and args and action_name == "send_private_msg":
            target_user_id = args[0]
        if target_group_id is None and args and action_name == "send_group_msg":
            target_group_id = args[0]
        if action_name == "send_group_msg" or target_group_id is not None:
            return (
                expected_group_id is not None
                and target_group_id is not None
                and str(target_group_id) == str(expected_group_id)
            )
        return (
            action_name in {"send_private_msg", "send_msg"}
            and expected_group_id is None
            and target_user_id is not None
            and expected_user_id is not None
            and str(target_user_id) == str(expected_user_id)
        )

    def _install_send_capture(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        bot = getattr(event, "bot", None)
        if bot is None:
            return
        if session.turn_id in self._send_wrappers:
            return
        registrations: list[dict] = []
        for name in ("send_private_msg", "send_group_msg"):
            registration = self._register_bot_send_capture(bot, name, event, session)
            if registration:
                registrations.append(registration)
        registration = self._register_bot_send_capture(
            bot, "call_action", event, session
        )
        if registration:
            registrations.append(registration)
        if not registrations:
            return
        self._send_wrappers[session.turn_id] = registrations

    def _register_bot_send_capture(
        self,
        bot: Any,
        method_name: str,
        event: AstrMessageEvent,
        session: TurnSession,
    ) -> dict | None:
        original = getattr(bot, method_name, None)
        if not original:
            return None
        key = (id(bot), method_name)
        dispatcher_info = self._bot_dispatchers.get(key)
        if dispatcher_info is None:
            dispatcher_info = {
                "bot": bot,
                "method_name": method_name,
                "original": original,
                "registrations": [],
                "dispatcher": None,
            }

            async def dispatcher(*args, **kwargs):
                return await self._dispatch_bot_send(dispatcher_info, *args, **kwargs)

            dispatcher_info["dispatcher"] = dispatcher
            self._bot_dispatchers[key] = dispatcher_info
            setattr(bot, method_name, dispatcher)

        registration = {
            "turn_id": session.turn_id,
            "event": event,
            "session": session,
            "method_name": method_name,
        }
        dispatcher_info["registrations"].append(registration)
        return registration

    async def _dispatch_bot_send(self, dispatcher_info: dict, *args, **kwargs):
        method_name = dispatcher_info["method_name"]
        original = dispatcher_info["original"]
        owner = self._send_context.get()
        if owner is None or not owner["active"]:
            return await original(*args, **kwargs)
        owner_event, owner_session = owner["event"], owner["session"]
        registrations = list(dispatcher_info["registrations"])
        action_name = method_name
        target_args = args
        target_kwargs = dict(kwargs)

        if method_name == "call_action":
            action_name = str(args[0] if args else kwargs.get("action"))
            if not action_name.startswith("send_"):
                return await original(*args, **kwargs)
            target_args = args[1:]
            params = target_kwargs.get("params")
            if isinstance(params, dict):
                target_kwargs.update(params)

        matched = [
            registration
            for registration in registrations
            if registration["event"] is owner_event
            and registration["session"] is owner_session
            and self._send_targets_event(
                registration["event"], action_name, target_args, target_kwargs
            )
        ]
        if not matched:
            return await original(*args, **kwargs)

        valid_matched = [
            registration
            for registration in matched
            if self._session_is_current_valid(registration["session"])
        ]
        if valid_matched:
            registration = valid_matched[-1]
            session = registration["session"]
            event = registration["event"]
            before_count = len(session.sent_message_ids)
            session.delivery_state = "sending"
            try:
                result = await original(*args, **kwargs)
            except Exception as exc:
                await self._handle_send_failure(event, session, exc)
                raise
            self._collect_message_ids(result, session.sent_message_ids)
            await self._recall_new_ids_if_invalid(event, session, before_count)
            return result

        registration = matched[-1]
        registration["event"].stop_event()
        return None

    async def _recall_new_ids_if_invalid(
        self, event: AstrMessageEvent, session: TurnSession, before_count: int
    ) -> None:
        if self._session_is_current_valid(session):
            return
        new_ids = session.sent_message_ids[before_count:]
        if new_ids:
            await self.recall_adapter.recall_messages(event, new_ids)
        await self._rollback_history(event, session)
        self._schedule_rollback_retries(event, session)

    async def _handle_send_failure(
        self, event: AstrMessageEvent, session: TurnSession, exc: BaseException
    ) -> None:
        already_failed = session.delivery_state == "send_failed"
        session.delivery_state = "send_failed"
        self._cancel_unsent_cleanup(session.turn_id)
        self.turns.invalidate(session.turn_id)
        event.stop_event()
        # The core can continue its segmented-send loop after this exception.
        # Keep event guards installed so the remaining segments are suppressed.
        self._restore_send_capture(session.turn_id)
        if already_failed:
            return
        await self._rollback_history(event, session, force=True)
        self._schedule_rollback_retries(event, session)
        self._schedule_invalidated_cleanup(session)
        logger.warning(
            f"[消息防抖动] 本轮发送失败，已回滚历史 | turn_id={session.turn_id} | {exc}"
        )

    @staticmethod
    def _is_source_exception(exc: BaseException, sources: list[BaseException]) -> bool:
        seen = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            if any(exc is source for source in sources):
                return True
            exc = exc.__cause__
        return False

    async def _recall_failed_attempt(
        self, event, session: TurnSession, start: int
    ) -> None:
        message_ids = session.sent_message_ids[start:]
        if not message_ids:
            return
        recalled = await self.recall_adapter.recall_messages(event, message_ids)
        if recalled:
            session.sent_message_ids[:] = [
                message_id
                for message_id in session.sent_message_ids
                if message_id not in recalled
            ]
        if len(recalled) < len(message_ids):
            logger.warning(
                f"[消息防抖动] 上游失败后部分分段未能撤回 | turn_id={session.turn_id}"
            )

    def _install_event_capture(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        if session.turn_id in self._event_wrappers:
            return
        wrappers: list[tuple[Any, str, Any]] = []
        original_send = getattr(event, "send", None)
        original_send_streaming = getattr(event, "send_streaming", None)

        if original_send:

            async def send_wrapped(message, *args, **kwargs):
                # None closes the native platform turn; it sends no stale content.
                if message is None:
                    return await original_send(message, *args, **kwargs)
                if not self._session_is_current_valid(session):
                    event.stop_event()
                    return None
                self._cancel_unsent_cleanup(session.turn_id)
                before_count = len(session.sent_message_ids)
                session.delivery_state = "sending"
                session.send_depth += 1
                lease = {"event": event, "session": session, "active": True}
                token = self._send_context.set(lease)
                try:
                    result = await original_send(message, *args, **kwargs)
                except Exception as exc:
                    await self._handle_send_failure(event, session, exc)
                    raise
                finally:
                    lease["active"] = False
                    self._send_context.reset(token)
                    session.send_depth -= 1
                session.send_succeeded = True
                self._collect_message_ids(result, session.sent_message_ids)
                await self._recall_new_ids_if_invalid(event, session, before_count)
                return result

            wrappers.append((event, "send", original_send))
            setattr(event, "send", send_wrapped)

        if original_send_streaming:

            async def send_streaming_wrapped(generator, *args, **kwargs):
                if not self._session_is_current_valid(session):
                    event.stop_event()
                    return None
                self._cancel_unsent_cleanup(session.turn_id)
                session.delivery_state = "streaming"
                session.send_depth += 1
                source_errors: list[BaseException] = []

                async def guarded_generator():
                    try:
                        async for chunk in generator:
                            if not self._session_is_current_valid(session):
                                event.stop_event()
                                break
                            yield chunk
                    except Exception as exc:
                        source_errors.append(exc)
                        raise
                    finally:
                        close = getattr(generator, "aclose", None)
                        if close is not None:
                            await close()

                lease = {"event": event, "session": session, "active": True}
                token = self._send_context.set(lease)
                guarded = guarded_generator()
                try:
                    before_count = len(session.sent_message_ids)
                    try:
                        result = await original_send_streaming(guarded, *args, **kwargs)
                    except Exception as exc:
                        if self._is_source_exception(exc, source_errors):
                            if not session.invalidated:
                                session.delivery_state = "provider_failed"
                            await self._recall_failed_attempt(
                                event, session, before_count
                            )
                            logger.warning(
                                f"[消息防抖动] 上游流异常，保留框架重试/备用模型处理 | "
                                f"turn_id={session.turn_id} | {type(exc).__name__}: {exc}"
                            )
                        else:
                            await self._handle_send_failure(event, session, exc)
                        raise
                    self._collect_message_ids(result, session.sent_message_ids)
                    await self._recall_new_ids_if_invalid(event, session, before_count)
                    if source_errors:
                        if not session.invalidated:
                            session.delivery_state = "provider_failed"
                        await self._recall_failed_attempt(event, session, before_count)
                    elif self._session_is_current_valid(session):
                        self._commit_turn(event, session)
                    return result
                finally:
                    lease["active"] = False
                    self._send_context.reset(token)
                    session.send_depth -= 1
                    await guarded.aclose()

            wrappers.append((event, "send_streaming", original_send_streaming))
            setattr(event, "send_streaming", send_streaming_wrapped)

        self._event_wrappers[session.turn_id] = wrappers

    def _commit_turn(self, event, session: TurnSession) -> None:
        if not self._session_is_current_valid(session):
            return
        self._cancel_unsent_cleanup(session.turn_id)
        session.delivery_state = "committed"
        event.set_extra("continuous_message_committed", True)
        self._advance_history_generation(event, session)
        self._restore_send_capture(session.turn_id)
        self._restore_event_capture(session.turn_id)
        self._pipeline_owners.pop(session.turn_id, None)
        self.turns.finish(session.turn_id)

    def _restore_event_capture(self, turn_id: str) -> None:
        wrappers = self._event_wrappers.pop(turn_id, [])
        for obj, name, original in wrappers:
            try:
                setattr(obj, name, original)
            except Exception:
                pass

    def _restore_send_capture(self, turn_id: str) -> None:
        registrations = self._send_wrappers.pop(turn_id, [])
        touched_keys = set()
        for registration in registrations:
            bot = getattr(registration.get("event"), "bot", None)
            method_name = registration.get("method_name")
            if bot is not None and method_name:
                touched_keys.add((id(bot), method_name))
        for key in touched_keys:
            dispatcher_info = self._bot_dispatchers.get(key)
            if not dispatcher_info:
                continue
            dispatcher_info["registrations"] = [
                registration
                for registration in dispatcher_info["registrations"]
                if registration.get("turn_id") != turn_id
            ]
            if dispatcher_info["registrations"]:
                continue
            try:
                setattr(
                    dispatcher_info["bot"],
                    dispatcher_info["method_name"],
                    dispatcher_info["original"],
                )
            except Exception:
                pass
            self._bot_dispatchers.pop(key, None)
