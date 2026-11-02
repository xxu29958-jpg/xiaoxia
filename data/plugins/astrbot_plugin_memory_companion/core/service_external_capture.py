from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .bot_personal_dto import (
    BotPersonalValidationError,
    sanitize_bot_personal_value,
)
from .models import (
    EntityRef,
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceExternalCapture:
    """External capture; state and transaction ownership remain in the facade."""

    def _sanitize_visible_timeline_text(self, text: Any) -> str:
        cleaned = clean_text(text, 1200)
        if not cleaned:
            return ""
        cleaned = re.sub(
            r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(
            r"<reasoning>.*?</reasoning>", "", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(r"\[\[TTSBLOCK:[^\]]*\]\]", "", cleaned)
        cleaned = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", cleaned)
        cleaned = re.sub(
            r"<timer\b[^>]*>.*?</timer>", "", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = re.sub(
            r"<tts\b[^>]*>.*?</tts>", "", cleaned, flags=re.IGNORECASE | re.DOTALL
        )
        cleaned = clean_text(cleaned, 1000)
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        internal_markers = (
            "private companion",
            "send_message_to_user",
            "image_generation_task_result",
            "工具调用",
            "主动消息人格/世界观判定器",
            "主动私聊发送前的价值复核模型",
        )
        if any(marker in lowered or marker in cleaned for marker in internal_markers):
            return ""
        if re.search(
            r"^(已经|已|我已经).{0,12}(发送|发给|转给).{0,12}(用户|对方|你)",
            cleaned,
            re.IGNORECASE,
        ):
            return ""
        if re.fullmatch(
            r"(已发送|发送成功|已经发送给用户了)[。.!！]*", cleaned, re.IGNORECASE
        ):
            return ""
        return cleaned

    async def record_visible_turn(
        self,
        *,
        role: str,
        content: str,
        scope: str = "unknown",
        session_id: str = "",
        platform: str = "",
        user_id: str = "",
        user_name: str = "",
        group_id: str = "",
        message_id: str = "",
        source: str = "external",
        metadata: dict[str, Any] | None = None,
        occurred_at: str = "",
    ) -> str:
        text = self._sanitize_visible_timeline_text(content)
        if not text:
            return ""
        normalized_role = clean_text(role, 40).lower()
        if normalized_role in {"assistant", "bot", "self"}:
            event_type = "bot_response"
            bridge_ctx = SessionContext(
                bot_id=clean_text((metadata or {}).get("bot_id"), 120)
            )
            subject_id = self._bot_subject_id(bridge_ctx)
            object_id = clean_text(group_id or user_id or session_id, 120)
        else:
            event_type = "user_message"
            subject_id = clean_text(user_id, 120)
            object_id = clean_text(group_id if scope == "group" else user_id, 120)
        event_metadata = dict(metadata or {})
        if source:
            event_metadata.setdefault("source", clean_text(source, 80))
        if event_type == "bot_response":
            event_metadata.setdefault("owner_bot_id", subject_id)
        if user_name:
            event_metadata.setdefault("sender_name", clean_text(user_name, 120))
        if message_id:
            event_metadata.setdefault("message_id", clean_text(message_id, 120))
        normalized_session_id = clean_text(session_id, 200)
        normalized_scope = clean_text(scope, 40)
        scope_ctx = SessionContext(
            session_id=normalized_session_id,
            scope=normalized_scope,
            platform=clean_text(platform, 40),
            user_id=clean_text(user_id, 120),
            user_name=clean_text(user_name, 120),
            group_id=clean_text(group_id, 120),
            bot_id=clean_text(event_metadata.get("bot_id"), 120),
            persona_id=clean_text(event_metadata.get("persona_id"), 96),
            message_id=clean_text(message_id, 120),
        )
        for key, value in self._cross_window_event_metadata(scope_ctx).items():
            event_metadata.setdefault(key, value)
        if not self._scope_feature_enabled(scope_ctx, "capture"):
            return ""
        event_id = await self.store.add_timeline_event(
            event_type=event_type,
            session_id=normalized_session_id,
            scope=normalized_scope,
            subject_id=subject_id,
            object_id=object_id,
            content=text,
            metadata=event_metadata,
            occurred_at=occurred_at,
        )
        if event_id and normalized_session_id and event_type == "bot_response":
            summary_ctx = SessionContext(
                session_id=normalized_session_id,
                scope=normalized_scope,
                platform=clean_text(platform, 40),
                user_id=clean_text(user_id, 120),
                user_name=clean_text(user_name, 120),
                group_id=clean_text(group_id, 120),
                bot_id=clean_text(event_metadata.get("bot_id"), 120),
                persona_id=scope_ctx.persona_id,
                message_id=clean_text(message_id, 120),
                message_text=text,
            )
            self._schedule_session_summary(summary_ctx, reason="bridge_visible_turn")
        if event_id:
            self._schedule_source_semantic_maintenance(scope_ctx, event_id)
        return event_id

    async def record_external_event(self, **kwargs: Any) -> str:
        if not self.config.bool(
            "private_companion_bridge.accept_external_records", True
        ):
            raise RuntimeError("外部记忆写入已关闭")

        explicit_id = clean_text(
            kwargs.pop("memory_id", "") or kwargs.pop("id", ""), 120
        )
        record = self.classifier.external_record(**kwargs)
        if explicit_id:
            record.id = explicit_id
        elif not record.id:
            record.id = self.stable_id(
                kwargs.get("source_plugin", "external"),
                kwargs.get("session_id", ""),
                kwargs.get("content", ""),
            )
        self.importance.calibrate(record, source="external_bridge")

        memory_id = await self.store.insert_memory(record)
        self._schedule_memory_embedding(memory_id, record)
        if record.reality_level == "bot_action":
            timeline_content = ""
            if isinstance(record.metadata, dict):
                timeline_content = self._sanitize_visible_timeline_text(
                    record.metadata.get("clean_visible_text")
                    or record.metadata.get("visible_text")
                    or record.metadata.get("response_text")
                )
            if not timeline_content:
                timeline_content = self._sanitize_visible_timeline_text(record.content)
            await self.store.add_timeline_event(
                event_type=record.memory_type,
                session_id=record.session_id,
                scope=record.scope,
                subject_id=record.subject.id or "self",
                object_id=record.object.id,
                content=timeline_content or record.content,
                metadata={
                    "memory_id": memory_id,
                    "source_plugin": record.source_plugin,
                },
            )
        return memory_id

    async def record_external_memory(
        self,
        *,
        user_id: str = "",
        content: str = "",
        summary: str = "",
        payload: Mapping[str, Any] | None = None,
        memory_type: str = "external_memory",
        source_plugin: str = "external",
        occurred_at: str = "",
        idempotency_key: str = "",
        memory_id: str = "",
        importance: float = 0.62,
        confidence: float = 0.82,
        tags: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        long_term: bool = True,
    ) -> dict[str, Any]:
        """Store a user-scoped memory supplied by another plugin or app.

        This is deliberately narrower than ``record_external_event``: callers
        do not choose a group/session scope, and the record is always attached
        to the supplied user so it can be recalled from that user's private
        conversations.  High-frequency integrations can pass ``long_term``
        false until they have produced a useful summary.
        """
        result = {
            "ok": False,
            "state": "rejected",
            "memory_id": "",
            "deduplicated": False,
            "long_term": bool(long_term),
            "scope": "private",
            "visibility": "private_pair",
            "error_code": None,
        }
        if not self.config.bool(
            "private_companion_bridge.accept_external_records", True
        ):
            return {**result, "error_code": "external_records_disabled"}

        normalized_user_id = clean_text(user_id, 160)
        if not normalized_user_id:
            return {**result, "error_code": "user_id_required"}

        normalized_source = clean_text(source_plugin, 100) or "external"
        normalized_type = clean_text(memory_type, 80) or "external_memory"
        normalized_content = clean_text(content or summary, 4000)
        if payload is not None and not isinstance(payload, Mapping):
            return {**result, "error_code": "payload_invalid"}
        if metadata is not None and not isinstance(metadata, Mapping):
            return {**result, "error_code": "metadata_invalid"}
        try:
            supplied_payload = dict(payload) if isinstance(payload, Mapping) else {}
            supplied_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        except (TypeError, ValueError):
            return {**result, "error_code": "metadata_invalid"}
        if not normalized_content and supplied_payload:
            # A summary is still required for durable recall; accepting the
            # common aliases makes small integrations less verbose without
            # turning arbitrary telemetry into a long-term memory sentence.
            for key in ("summary", "content", "text", "label"):
                candidate = clean_text(supplied_payload.get(key), 4000)
                if candidate:
                    normalized_content = candidate
                    break
        if not normalized_content:
            return {**result, "error_code": "content_required"}

        try:
            safe_metadata = sanitize_bot_personal_value(
                supplied_metadata, path="metadata"
            )
            safe_payload = (
                sanitize_bot_personal_value(supplied_payload, path="payload")
                if supplied_payload
                else {}
            )
        except BotPersonalValidationError as exc:
            return {
                **result,
                "error_code": clean_text(exc.error_code, 80) or "metadata_invalid",
            }
        if not isinstance(safe_metadata, dict) or not isinstance(safe_payload, dict):
            return {**result, "error_code": "metadata_invalid"}

        normalized_key = clean_text(idempotency_key, 180)
        normalized_occurred_at = clean_text(occurred_at, 80)
        stable_memory_id = clean_text(memory_id, 120)
        if not stable_memory_id:
            stable_memory_id = self.stable_id(
                "external_memory",
                normalized_source,
                normalized_user_id,
                normalized_key or normalized_content,
                normalized_occurred_at,
            )
        existing = None
        getter = getattr(self.store, "get_memory", None)
        if callable(getter):
            try:
                existing = await getter(stable_memory_id)
            except Exception:
                existing = None

        safe_tags: list[str] = []
        for tag in tags or []:
            normalized_tag = clean_text(tag, 60)
            if normalized_tag and normalized_tag not in safe_tags:
                safe_tags.append(normalized_tag)
        if not safe_tags:
            safe_tags = ["external", normalized_type]
        if bool(long_term) and "long_term" not in safe_tags:
            safe_tags.append("long_term")

        safe_metadata.update(
            {
                "external_memory": True,
                "source_plugin": normalized_source,
                "idempotency_key": normalized_key,
                "long_term": bool(long_term),
                "memory_type": normalized_type,
                "owner_user_id": normalized_user_id,
            }
        )
        if safe_payload:
            safe_metadata.setdefault("structured_data", safe_payload)

        record = self.classifier.external_record(
            content=normalized_content,
            memory_type=normalized_type,
            subject=EntityRef(
                kind="user", id=normalized_user_id, role="external_memory_owner"
            ),
            object=EntityRef.bot_self(),
            # Keep this independent of the source platform so a memory
            # reported by an app can be recalled in QQ, web, or phone chats.
            scope="private",
            session_id=f"external:{normalized_source}:{normalized_user_id}",
            platform="",
            visibility="private_pair",
            sayability="direct",
            reality_level="external_observation",
            lifecycle="stable_memory" if bool(long_term) else "raw_event",
            confidence=confidence,
            importance=importance,
            review_status="auto" if bool(long_term) else "pending",
            tags=safe_tags,
            metadata=safe_metadata,
            source_plugin=normalized_source,
            occurred_at=normalized_occurred_at,
        )
        record.id = stable_memory_id
        self.importance.calibrate(record, source="external_memory")
        stored_id = await self.store.insert_memory(record)
        self._schedule_memory_embedding(stored_id, record)
        deduplicated = bool(existing is not None or stored_id != stable_memory_id)
        return {
            **result,
            "ok": True,
            "state": "deduplicated" if deduplicated else "stored",
            "memory_id": stored_id,
            "deduplicated": deduplicated,
        }
