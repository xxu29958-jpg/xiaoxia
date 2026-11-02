from __future__ import annotations

from typing import Any

from .bridge_support import (
    _EMOTION_DELIVERY_CONSUMER,
    _PRIVATE_COMPANION_NAMES,
    _PRIVATE_COMPANION_ROOT,
    _AuthenticatedCompanionProjection,
    _companion_projection_signature,
    _EmotionAdminContext,
    _EmotionDeliveryContext,
    _EmotionPageAdminCapability,
    _EmotionProducerCapability,
    _EmotionProducerContext,
    sanitize_companion_expression_decision,
    sanitize_companion_relationship_projection,
)
from .models import clean_text


class MemoryCompanionBridgeCapabilityAuthority:
    """Capability authority; shared runtime resources belong to the facade."""

    def register_emotion_producer(self, producer: Any) -> Any | None:
        """Issue a private Companion capability only to a live registered plugin."""

        if not self._active or not self._is_registered_private_companion(producer):
            return None
        return _EmotionProducerCapability(self, producer, self._emotion_producer_token)

    def register_private_companion(self, producer: Any) -> Any | None:
        """Issue the shared producer capability used by protected Companion APIs."""

        return self.register_emotion_producer(producer)

    def register_bot_personal_producer(self, producer: Any) -> Any | None:
        """Compatibility name for the same scoped Private Companion capability."""

        return self.register_emotion_producer(producer)

    def create_emotion_producer_context(
        self,
        capability: Any,
        *,
        bot_id: str,
        scope: str,
        platform: str,
        user_id: str,
        session_id: str,
    ) -> Any | None:
        """Bind a Companion capability to one private user/Bot delivery domain."""

        if not self._is_valid_emotion_producer_capability(capability):
            return None
        normalized = self._normalize_emotion_domain(
            bot_id=bot_id,
            scope=scope,
            platform=platform,
            user_id=user_id,
            session_id=session_id,
        )
        if normalized is None:
            return None
        return _EmotionProducerContext(self, capability, **normalized)

    def create_emotion_delivery_context(
        self,
        capability: Any,
        *,
        bot_id: str,
        scope: str,
        platform: str,
        user_id: str,
        session_id: str,
        consumer_id: str = _EMOTION_DELIVERY_CONSUMER,
        allow_cross_window: bool = False,
    ) -> Any | None:
        """Bind Companion afterglow delivery to one trusted private identity domain."""

        if not self._is_valid_emotion_producer_capability(capability):
            return None
        if clean_text(consumer_id, 80) != _EMOTION_DELIVERY_CONSUMER:
            return None
        if type(allow_cross_window) is not bool:
            return None
        if allow_cross_window and not self._cross_window_emotion_enabled():
            return None
        normalized = self._normalize_emotion_domain(
            bot_id=bot_id,
            scope=scope,
            platform=platform,
            user_id=user_id,
            session_id=session_id,
        )
        if normalized is None:
            return None
        return _EmotionDeliveryContext(
            self,
            capability,
            consumer_id=_EMOTION_DELIVERY_CONSUMER,
            allow_cross_window=allow_cross_window,
            **normalized,
        )

    def bind_emotion_page_api(self, page_api: Any) -> Any | None:
        """Issue a private diagnostic capability to this Memory plugin's Page API."""

        if not self._active:
            return None
        page_plugin = getattr(page_api, "plugin", None)
        if (
            page_plugin is not self._plugin
            and getattr(page_plugin, "service", None) is not self._plugin
        ):
            return None
        return _EmotionPageAdminCapability(
            self, page_api, self._emotion_page_admin_token
        )

    def create_emotion_admin_context(
        self,
        capability: Any,
        *,
        bot_id: str,
        scope: str,
        session_id: str,
    ) -> Any | None:
        """Create a scoped redacted-diagnostic context after page auth succeeds."""

        if not self._is_valid_emotion_page_admin_capability(capability):
            return None
        normalized_bot_id = clean_text(bot_id, 160)
        normalized_scope = clean_text(scope, 24).lower()
        normalized_session_id = clean_text(session_id, 220)
        if (
            not normalized_bot_id
            or normalized_scope not in {"private", "group"}
            or not normalized_session_id
        ):
            return None
        return _EmotionAdminContext(
            self,
            capability,
            bot_id=normalized_bot_id,
            scope=normalized_scope,
            session_id=normalized_session_id,
        )

    def _is_registered_private_companion(self, producer: Any) -> bool:
        context = getattr(self._plugin, "context", None)
        getter = getattr(context, "get_all_stars", None)
        if not callable(getter):
            return False
        try:
            stars = getter()
        except Exception:
            return False
        if not isinstance(stars, (list, tuple)):
            return False
        for metadata in stars:
            registered_instance = getattr(metadata, "star_cls", None)
            registered_type = getattr(metadata, "star_cls_type", None)
            # AstrBot's StarMetadata stores the live plugin object in
            # ``star_cls`` and its class in ``star_cls_type``.  Companion's
            # adapter registers ``type(self)``; accept that exact class only
            # when it is paired with the exact live instance.  Keep the
            # instance form for older adapters, but never fall back to names
            # or ``isinstance`` checks.
            producer_matches = producer is registered_instance
            if producer is registered_type:
                producer_matches = (
                    registered_instance is not None
                    and type(registered_instance) is registered_type
                )
            elif producer_matches and registered_type is not None:
                producer_matches = type(registered_instance) is registered_type
            if not producer_matches:
                continue
            if getattr(metadata, "activated", True) is not True:
                continue
            root = clean_text(getattr(metadata, "root_dir_name", ""), 120)
            name = clean_text(getattr(metadata, "name", ""), 120)
            if root == _PRIVATE_COMPANION_ROOT or name in _PRIVATE_COMPANION_NAMES:
                return True
        return False

    def _is_valid_emotion_producer_capability(self, capability: Any) -> bool:
        return (
            self._active
            and type(capability) is _EmotionProducerCapability
            and capability._bridge is self
            and capability._token is self._emotion_producer_token
            and self._is_registered_private_companion(capability._producer)
        )

    def _producer_capability_from(self, value: Any) -> Any:
        if type(value) in {_EmotionProducerContext, _EmotionDeliveryContext}:
            return value._capability
        return value

    def _is_valid_private_companion_capability(self, value: Any) -> bool:
        return self._is_valid_emotion_producer_capability(
            self._producer_capability_from(value)
        )

    def _cross_window_emotion_enabled(self) -> bool:
        config = getattr(self._plugin, "config", None)
        getter = getattr(config, "bool", None)
        if not callable(getter):
            return False
        try:
            return (
                getter(
                    "private_companion_bridge.cross_window_emotional_continuity_enabled",
                    False,
                )
                is True
            )
        except Exception:
            return False

    def _is_valid_emotion_page_admin_capability(self, capability: Any) -> bool:
        return (
            self._active
            and type(capability) is _EmotionPageAdminCapability
            and capability._bridge is self
            and capability._token is self._emotion_page_admin_token
            and (
                getattr(capability._page_api, "plugin", None) is self._plugin
                or getattr(
                    getattr(capability._page_api, "plugin", None), "service", None
                )
                is self._plugin
            )
        )

    @staticmethod
    def _normalize_emotion_domain(
        *,
        bot_id: Any,
        scope: Any,
        platform: Any,
        user_id: Any,
        session_id: Any,
    ) -> dict[str, str] | None:
        normalized = {
            "bot_id": clean_text(bot_id, 160),
            "scope": clean_text(scope, 24).lower(),
            "platform": clean_text(platform, 80),
            "user_id": clean_text(user_id, 160),
            "session_id": clean_text(session_id, 220),
        }
        if not all(normalized.values()) or normalized["scope"] != "private":
            return None
        if not normalized["session_id"].startswith(f"{normalized['platform']}:"):
            return None
        return normalized

    def _is_valid_emotion_producer_context(self, context: Any) -> bool:
        return (
            type(context) is _EmotionProducerContext
            and context._bridge is self
            and self._is_valid_emotion_producer_capability(context._capability)
            and self._normalize_emotion_domain(
                bot_id=context.bot_id,
                scope=context.scope,
                platform=context.platform,
                user_id=context.user_id,
                session_id=context.session_id,
            )
            is not None
        )

    def _is_valid_emotion_delivery_context(self, context: Any) -> bool:
        return (
            type(context) is _EmotionDeliveryContext
            and context._bridge is self
            and self._is_valid_emotion_producer_capability(context._capability)
            and context.consumer_id == _EMOTION_DELIVERY_CONSUMER
            and type(context.allow_cross_window) is bool
            and (not context.allow_cross_window or self._cross_window_emotion_enabled())
            and self._normalize_emotion_domain(
                bot_id=context.bot_id,
                scope=context.scope,
                platform=context.platform,
                user_id=context.user_id,
                session_id=context.session_id,
            )
            is not None
        )

    def _is_valid_emotion_admin_context(self, context: Any) -> bool:
        return (
            type(context) is _EmotionAdminContext
            and context._bridge is self
            and self._is_valid_emotion_page_admin_capability(context._capability)
            and bool(clean_text(context.bot_id, 160))
            and clean_text(context.scope, 24).lower() in {"private", "group"}
            and bool(clean_text(context.session_id, 220))
        )

    def _seal_companion_projection(
        self,
        projection: Any,
        *,
        kind: str,
        capability: Any,
        bot_id: Any,
        platform: Any,
        user_id: Any,
        scope: Any,
        session_id: Any,
    ) -> dict[str, Any]:
        if not self._is_valid_private_companion_capability(capability):
            return {
                "status": "invalid",
                "read_only": True,
                "error_code": "producer_capability_required",
            }
        if type(capability) is _EmotionProducerContext:
            bound = {
                "bot_id": capability.bot_id,
                "platform": capability.platform,
                "user_id": capability.user_id,
                "scope": capability.scope,
                "session_id": capability.session_id,
            }
            supplied = {
                "bot_id": clean_text(bot_id, 160),
                "platform": clean_text(platform, 80),
                "user_id": clean_text(user_id, 160),
                "scope": clean_text(scope, 24).lower(),
                "session_id": clean_text(session_id, 220),
            }
            if any(supplied[key] and supplied[key] != bound[key] for key in supplied):
                return {
                    "status": "invalid",
                    "read_only": True,
                    "error_code": "projection_context_mismatch",
                }
            bot_id = bound["bot_id"]
            platform = bound["platform"]
            user_id = bound["user_id"]
            scope = bound["scope"]
            session_id = bound["session_id"]
        domain = self._normalize_emotion_domain(
            bot_id=bot_id,
            scope=scope,
            platform=platform,
            user_id=user_id,
            session_id=session_id,
        )
        if domain is None:
            return {
                "status": "invalid",
                "read_only": True,
                "error_code": "projection_domain_invalid",
            }
        sanitizer = (
            sanitize_companion_relationship_projection
            if kind == "relationship"
            else sanitize_companion_expression_decision
        )
        consumed = sanitizer(projection)
        if consumed.get("status") != "accepted":
            return consumed
        key = "projection" if kind == "relationship" else "decision"
        payload = consumed.get(key)
        if not isinstance(payload, dict):
            return {
                "status": "invalid",
                "read_only": True,
                "error_code": "projection_payload_invalid",
            }
        signature = _companion_projection_signature(
            payload,
            kind=kind,
            bot_id=domain["bot_id"],
            platform=domain["platform"],
            person_id=domain["user_id"],
            scope=domain["scope"],
            session_id=domain["session_id"],
        )
        sealed = _AuthenticatedCompanionProjection(
            payload,
            kind=kind,
            bot_id=domain["bot_id"],
            platform=domain["platform"],
            person_id=domain["user_id"],
            scope=domain["scope"],
            session_id=domain["session_id"],
            signature=signature,
        )
        return {"status": "accepted", "read_only": True, key: sealed}

    def consume_relationship_projection(
        self,
        projection: Any,
        *,
        producer_capability: Any = None,
        producer_context: Any = None,
        bot_id: str = "",
        platform: str = "",
        user_id: str = "",
        scope: str = "private",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Validate and seal a Companion relationship projection for one request."""

        authority = (
            producer_capability if producer_capability is not None else producer_context
        )
        return self._seal_companion_projection(
            projection,
            kind="relationship",
            capability=authority,
            bot_id=bot_id,
            platform=platform,
            user_id=user_id,
            scope=scope,
            session_id=session_id,
        )

    def consume_expression_decision(
        self,
        decision: Any,
        *,
        producer_capability: Any = None,
        producer_context: Any = None,
        bot_id: str = "",
        platform: str = "",
        user_id: str = "",
        scope: str = "private",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Validate and seal a Companion expression decision for one request."""

        authority = (
            producer_capability if producer_capability is not None else producer_context
        )
        return self._seal_companion_projection(
            decision,
            kind="expression",
            capability=authority,
            bot_id=bot_id,
            platform=platform,
            user_id=user_id,
            scope=scope,
            session_id=session_id,
        )

    def create_user_memory_context(self, capability: Any, **kwargs: Any) -> Any | None:
        """Name the scoped context explicitly for user-summary consumers."""

        return self.create_emotion_producer_context(capability, **kwargs)
