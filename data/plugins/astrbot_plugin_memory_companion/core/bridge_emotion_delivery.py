from __future__ import annotations

from typing import Any

from .bridge_support import (
    _EMOTION_INGRESS_ORIGINS,
    _EmotionProducerContext,
)
from .models import EntityRef, SessionContext, clean_text


class MemoryCompanionBridgeEmotionDelivery:
    """Emotion delivery; shared runtime resources belong to the facade."""

    def get_token_usage_summary(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "token_usage_summary", None)
        if callable(getter):
            result = getter()
            return result if isinstance(result, dict) else {}
        return {}

    def should_defer_private_companion_section(self, section: str) -> bool:
        checker = getattr(self._plugin, "should_private_companion_defer_section", None)
        if callable(checker):
            return bool(checker(section))
        return False

    async def create_cross_window_thread(
        self,
        *,
        from_session: str,
        to_session: str,
        topic: str,
        content: str,
        visibility: str = "shareable",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await self._plugin.store.create_cross_window_thread(
            from_session=from_session,
            to_session=to_session,
            topic=topic,
            content=content,
            visibility=visibility,
            metadata=metadata or {},
        )

    async def mark_visibility(self, memory_id: str, visibility: str) -> bool:
        return await self._plugin.store.update_memory_visibility(memory_id, visibility)

    def get_emotional_events(
        self, *, session_id: str = "", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Temporary exact-window compatibility path for pre-capability callers."""

        safe_session = clean_text(session_id, 220)
        if not safe_session or ":" not in safe_session:
            return []
        getter = getattr(self._plugin, "bridge_get_emotional_events", None)
        if not callable(getter):
            return []
        try:
            events = getter(session_id=safe_session, limit=limit)
        except Exception:
            return []
        return events if isinstance(events, list) else []

    async def list_emotion_events(
        self,
        *,
        delivery_context: Any = None,
        cursor: str = "",
        limit: int = 10,
        **_legacy: Any,
    ) -> dict[str, Any]:
        """List afterglow events only for one opaque Companion delivery context."""

        if not self._is_valid_emotion_delivery_context(delivery_context):
            return self._emotion_delivery_forbidden_result("delivery_context_required")
        return await self._plugin.store.list_emotion_event_deliveries(
            consumer_id=delivery_context.consumer_id,
            bot_id=delivery_context.bot_id,
            scope=delivery_context.scope,
            platform=delivery_context.platform,
            user_id=delivery_context.user_id,
            session_id=delivery_context.session_id,
            allow_cross_window=delivery_context.allow_cross_window,
            cursor=cursor,
            limit=limit,
        )

    async def ack_emotion_events(
        self,
        event_refs: list[dict[str, Any]],
        *,
        delivery_context: Any = None,
        **_legacy: Any,
    ) -> dict[str, Any]:
        """Acknowledge only events delivered inside one opaque identity domain."""

        if not self._is_valid_emotion_delivery_context(delivery_context):
            return self._emotion_ack_forbidden_result("delivery_context_required")
        return await self._plugin.store.ack_emotion_event_deliveries(
            consumer_id=delivery_context.consumer_id,
            event_refs=event_refs,
            bot_id=delivery_context.bot_id,
            scope=delivery_context.scope,
            platform=delivery_context.platform,
            user_id=delivery_context.user_id,
            session_id=delivery_context.session_id,
            allow_cross_window=delivery_context.allow_cross_window,
        )

    async def record_emotion_event(
        self,
        event: dict[str, Any],
        *,
        producer_context: Any = None,
    ) -> dict[str, Any]:
        """Persist a Companion event only inside an attested private user/Bot domain."""

        if not self._is_valid_emotion_producer_context(producer_context):
            return self._emotion_forbidden_result("producer_context_required")
        return await self._plugin.store.upsert_emotion_event(
            self._attested_emotion_event(event, producer_context)
        )

    async def revise_emotion_event(
        self,
        event: dict[str, Any],
        *,
        producer_context: Any = None,
    ) -> dict[str, Any]:
        """Persist a later Companion revision only inside its attested domain."""

        if not self._is_valid_emotion_producer_context(producer_context):
            return self._emotion_forbidden_result("producer_context_required")
        return await self._plugin.store.upsert_emotion_event(
            self._attested_emotion_event(event, producer_context)
        )

    async def get_emotion_trace(
        self,
        trace_id: str,
        *,
        requester_context: Any = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return only the scoped, redacted diagnostic projection for a trusted admin."""

        return await self.get_emotion_trace_diagnostic(
            trace_id,
            requester_context,
            limit=limit,
        )

    async def get_emotion_trace_diagnostic(
        self,
        trace_id: str,
        requester_context: Any,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        context = requester_context
        if not self._is_valid_emotion_admin_context(context):
            return {
                "state": "forbidden",
                "read_only": True,
                "items": [],
                "error_code": "admin_required",
            }
        items = await self._plugin.store.get_emotion_trace_diagnostic(
            trace_id,
            bot_id=context.bot_id,
            scope=context.scope,
            session_id=context.session_id,
            limit=max(1, min(100, int(limit or 100))),
        )
        return {"state": "ready", "read_only": True, "items": items}

    async def get_emotion_trace_summary(
        self,
        requester_context: Any,
        *,
        cursor: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        context = requester_context
        if not self._is_valid_emotion_admin_context(context):
            return {
                "state": "forbidden",
                "read_only": True,
                "items": [],
                "next_cursor": "",
                "error_code": "admin_required",
            }
        result = await self._plugin.store.get_emotion_trace_summary(
            bot_id=context.bot_id,
            scope=context.scope,
            session_id=context.session_id,
            cursor=clean_text(cursor, 20),
            limit=max(1, min(100, int(limit or 20))),
        )
        return {"state": "ready", "read_only": True, **result}

    @staticmethod
    def _emotion_forbidden_result(error_code: str) -> dict[str, Any]:
        return {
            "ok": False,
            "state": "forbidden",
            "read_only": False,
            "event_id": "",
            "error_code": error_code,
        }

    @staticmethod
    def _emotion_delivery_forbidden_result(error_code: str) -> dict[str, Any]:
        return {
            "schema_version": "emotion_afterglow_delivery.v1",
            "state": "forbidden",
            "read_only": True,
            "events": [],
            "next_cursor": "",
            "has_more": False,
            "error_code": error_code,
        }

    @staticmethod
    def _emotion_ack_forbidden_result(error_code: str) -> dict[str, Any]:
        return {
            "state": "forbidden",
            "acked": 0,
            "error_code": error_code,
        }

    @staticmethod
    def _attested_emotion_event(
        event: Any, context: _EmotionProducerContext
    ) -> dict[str, Any]:
        source = dict(event) if isinstance(event, dict) else {}
        origin = clean_text(source.get("origin_kind"), 40).lower()
        if origin not in _EMOTION_INGRESS_ORIGINS:
            origin = "interaction"
        source.update(
            {
                "producer_plugin": "private_companion",
                "origin_kind": origin,
                "bot_id": context.bot_id,
                "scope": context.scope,
                "platform": context.platform,
                "session_id": context.session_id,
                "actor_ref": {"kind": "user", "id": context.user_id, "role": "speaker"},
                "target_ref": {"kind": "bot", "id": context.bot_id, "role": "bot_self"},
                "quoted_target_ref": {},
            }
        )
        return source

    async def search_open_loops(
        self, *, session_id: str = "", limit: int = 3
    ) -> list[dict[str, Any]]:
        """Search for unresolved open-loop / promise memories for proactive companionship."""
        return await self._plugin.bridge_search_open_loops(
            session_id=session_id, limit=limit
        )

    def get_relationship_phase(
        self,
        *,
        session_id: str = "",
        scope: str = "private",
        platform: str = "",
        user_id: str = "",
        group_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        """Return current relationship phase state for a session."""
        getter = getattr(self._plugin, "_get_relationship_phase", None)
        if not callable(getter):
            return {"phase": "unknown", "momentum": 0.0}
        normalizer = getattr(self._plugin, "session_context_from_bridge", None)
        payload = {
            "session_id": session_id,
            "scope": scope,
            "platform": platform,
            "user_id": user_id,
            "group_id": group_id,
            "bot_id": bot_id,
        }
        ctx = normalizer(payload) if callable(normalizer) else SessionContext(**payload)
        return getter(ctx)

    def peek_relationship_phase(
        self,
        *,
        session_id: str = "",
        scope: str = "private",
        platform: str = "",
        user_id: str = "",
        group_id: str = "",
        bot_id: str = "",
    ) -> dict[str, Any]:
        """Read an existing phase projection without creating default state."""
        fallback = {"observed": False, "phase": "unknown", "momentum_band": "unknown"}
        payload = {
            "session_id": session_id,
            "scope": scope,
            "platform": platform,
            "user_id": user_id,
            "group_id": group_id,
            "bot_id": bot_id,
        }
        if any(type(value) is not str for value in payload.values()):
            return fallback
        try:
            getter = getattr(self._plugin, "_peek_relationship_phase", None)
            if not callable(getter):
                return fallback
            normalizer = getattr(self._plugin, "session_context_from_bridge", None)
            ctx = (
                normalizer(payload)
                if callable(normalizer)
                else SessionContext(**payload)
            )
            result = getter(ctx)
        except Exception:
            return fallback
        if type(result) is not dict:
            return fallback
        for key in result:
            if type(key) is not str:
                return fallback

        observed = result.get("observed")
        phase = result.get("phase")
        momentum_band = result.get("momentum_band")
        if (
            type(observed) is not bool
            or type(phase) is not str
            or type(momentum_band) is not str
        ):
            return fallback
        if phase not in {
            "acquaintance",
            "familiar",
            "close",
            "intimate",
            "deeply_bonded",
        }:
            return fallback
        if momentum_band not in {"rising", "cooling", "steady"}:
            return fallback
        if not observed:
            return fallback
        projection: dict[str, Any] = {
            "observed": True,
            "phase": phase,
            "momentum_band": momentum_band,
        }
        touch_count = result.get("touch_count")
        if touch_count is not None:
            if type(touch_count) is not int or not 0 <= touch_count <= 256:
                return fallback
            projection["touch_count"] = touch_count
        return projection

    def get_recent_emotional_state(
        self,
        *,
        exclude_session_id: str = "",
        window_seconds: float = 1800.0,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Explain why the identity-free cross-window aggregate is unavailable."""
        _ = (exclude_session_id, window_seconds, limit)
        return {
            "enabled": False,
            "state": "migration_required",
            "error_code": "delivery_context_required",
            "total": 0,
            "scar_count": 0,
            "warm_count": 0,
            "vulnerable_count": 0,
        }

    def _entity(self, payload: dict[str, Any]) -> EntityRef:
        return EntityRef(
            kind=str(payload.get("kind") or "user"),
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            role=str(payload.get("role") or "unknown"),
        )
