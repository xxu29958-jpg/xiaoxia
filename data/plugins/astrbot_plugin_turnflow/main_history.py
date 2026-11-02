import asyncio
import json
from html import escape
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .turn_state import (
    TurnSession,
)


class ContinuousMessagePluginHistory:
    """History; shared runtime resources belong to the facade."""

    def _format_message_batch(self, session: TurnSession) -> str:
        lines = ['<message_batch source="continuous_message">']
        for item in session.items:
            attrs = f'index="{item.index}" kind="{item.kind}"'
            item_lines = [f"  <item {attrs}>"]
            parts = item.parts or (
                ([{"type": "text", "text": item.text}] if item.text else [])
                + [{"type": "image", "url": url} for url in item.images]
            )
            img_idx = 0
            for part in parts:
                if part.get("type") == "text":
                    item_lines.append(
                        f"    <text>{escape(part.get('text', ''))}</text>"
                    )
                elif part.get("type") == "image":
                    img_idx += 1
                    item_lines.append(
                        f'    <image index="{img_idx}">{escape(part.get("url", ""))}</image>'
                    )
            item_lines.append("  </item>")
            lines.extend(item_lines)
        lines.append("</message_batch>")
        return "\n".join(lines)

    async def _snapshot_history(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        if session.history_snapshot is not None and session.history_conversation_id:
            return
        try:
            provider_request = event.get_extra("provider_request")
            conversation = (
                getattr(provider_request, "conversation", None)
                if provider_request
                else None
            )
            if conversation:
                cid = getattr(conversation, "cid", None)
                if cid:
                    session.history_conversation_id = cid
                if session.history_snapshot is None:
                    session.history_snapshot = json.loads(
                        getattr(conversation, "history", "[]") or "[]"
                    )
                if session.history_conversation_id:
                    self._record_history_generation(event, session)
                return
            if session.history_snapshot is not None:
                return
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if not conv_mgr:
                return
            cid = await conv_mgr.get_curr_conversation_id(event.unified_msg_origin)
            if not cid:
                session.history_snapshot = []
                return
            conversation = await conv_mgr.get_conversation(
                event.unified_msg_origin, cid
            )
            if not conversation:
                return
            session.history_conversation_id = cid
            session.history_snapshot = json.loads(
                getattr(conversation, "history", "[]") or "[]"
            )
            self._record_history_generation(event, session)
        except Exception as exc:
            logger.debug(f"[消息防抖动] 历史快照失败，后续将跳过历史回滚: {exc}")

    async def _reconcile_inherited_history(
        self,
        event: AstrMessageEvent,
        req: Any,
        session: TurnSession,
    ) -> bool:
        if not session.history_snapshot_inherited or session.history_reconciled:
            return True
        if not self._session_is_current_valid(session):
            return False
        snapshot = self._clone_history_snapshot(session.history_snapshot)
        cid = session.history_conversation_id
        if snapshot is None or not cid:
            session.history_reconciled = True
            return True

        conversation = getattr(req, "conversation", None)
        request_cid = getattr(conversation, "cid", None) if conversation else None
        if request_cid and str(request_cid) != str(cid):
            logger.warning(
                f"[消息防抖动] 继承历史会话ID不一致，已阻断请求 | "
                f"turn_id={session.turn_id} | inherited_cid={cid} | request_cid={request_cid}"
            )
            return False

        try:
            request_contexts = list(getattr(req, "contexts", []) or [])
            persisted_history: list[dict] = []
            if conversation is not None:
                raw_history = getattr(conversation, "history", "[]") or "[]"
                if isinstance(raw_history, str):
                    persisted_history = json.loads(raw_history)
                elif isinstance(raw_history, list):
                    persisted_history = self._clone_history_snapshot(raw_history) or []

            contexts_before_history: list[dict] = []
            contexts_after_history: list[dict] = []
            history_segment_found = False
            if persisted_history:
                segment_size = len(persisted_history)
                for start in range(len(request_contexts) - segment_size + 1):
                    if (
                        request_contexts[start : start + segment_size]
                        == persisted_history
                    ):
                        contexts_before_history = request_contexts[:start]
                        contexts_after_history = request_contexts[
                            start + segment_size :
                        ]
                        history_segment_found = True
                        break
            else:
                contexts_after_history = request_contexts
                history_segment_found = True
            if persisted_history and request_contexts and not history_segment_found:
                logger.warning(
                    f"[消息防抖动] 请求上下文中未找到持久历史区段，"
                    f"保留请求上下文，避免删除其他插件内容 | turn_id={session.turn_id}"
                )

            if conversation is not None:
                conversation.history = json.dumps(snapshot, ensure_ascii=False)
            if hasattr(req, "contexts") and (
                history_segment_found or not request_contexts
            ):
                req.contexts = (
                    contexts_before_history
                    + (self._clone_history_snapshot(snapshot) or [])
                    + contexts_after_history
                )
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if conv_mgr:
                await conv_mgr.update_conversation(
                    event.unified_msg_origin,
                    cid,
                    history=snapshot,
                )
            session.history_reconciled = True
            logger.info(
                f"[消息防抖动] 已在新请求前恢复继承历史基线 | "
                f"turn_id={session.turn_id} | predecessors={session.predecessor_turn_ids}"
            )
            return True
        except Exception as exc:
            logger.warning(
                f"[消息防抖动] 恢复继承历史基线失败，已阻断请求 | "
                f"turn_id={session.turn_id} | {exc}"
            )
            return False

    def _history_generation_key(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> tuple[str, str] | None:
        if not session.history_conversation_id:
            return None
        return (event.unified_msg_origin, str(session.history_conversation_id))

    def _record_history_generation(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        key = self._history_generation_key(event, session)
        if not key:
            return
        session.history_generation = self._history_generations.get(key, 0)

    def _advance_history_generation(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        key = self._history_generation_key(event, session)
        if not key:
            return
        current = self._history_generations.get(key, 0)
        next_generation = max(current, session.history_generation) + 1
        self._history_generations[key] = next_generation
        session.history_generation = next_generation

    def _history_generation_unchanged(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> bool:
        key = self._history_generation_key(event, session)
        if not key:
            return False
        return self._history_generations.get(key, 0) == session.history_generation

    async def _rollback_history(
        self, event: AstrMessageEvent, session: TurnSession, force: bool = False
    ) -> None:
        if session.history_snapshot is None or not session.history_conversation_id:
            return
        # Even immediate cleanup must never overwrite a newer turn.
        if not self._history_generation_unchanged(event, session):
            logger.info(
                f"[消息防抖动] 跳过旧轮次历史回滚，已有更新轮次完成 | turn_id={session.turn_id}"
            )
            return
        try:
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if not conv_mgr:
                return
            await conv_mgr.update_conversation(
                event.unified_msg_origin,
                session.history_conversation_id,
                history=session.history_snapshot,
            )
            logger.info(f"[消息防抖动] 已回滚失效轮次历史 | turn_id={session.turn_id}")
        except Exception as exc:
            logger.warning(
                f"[消息防抖动] 历史回滚失败 | turn_id={session.turn_id} | {exc}"
            )

    def _schedule_rollback_retries(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        for raw_delay in self.history_rollback_delays:
            try:
                delay = float(raw_delay)
            except (TypeError, ValueError):
                continue
            self._spawn_background(self._rollback_history_later(event, session, delay))

    async def _rollback_history_later(
        self, event: AstrMessageEvent, session: TurnSession, delay: float
    ) -> None:
        await asyncio.sleep(max(delay, 0.0))
        if session.invalidated or not self._session_is_current_valid(session):
            await self._rollback_history(event, session)
