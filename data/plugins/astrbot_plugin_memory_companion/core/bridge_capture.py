from __future__ import annotations

from typing import Any

from .models import EntityRef, clean_text


class MemoryCompanionBridgeCapture:
    """Capture; shared runtime resources belong to the facade."""

    async def record_event(
        self,
        *,
        content: str,
        memory_type: str = "external_event",
        scope: str = "unknown",
        session_id: str = "",
        platform: str = "",
        message_id: str = "",
        group_id: str = "",
        subject: dict[str, Any] | None = None,
        object: dict[str, Any] | None = None,
        visibility: str = "bot_self",
        sayability: str = "direct",
        reality_level: str = "bot_action",
        lifecycle: str = "stable_memory",
        confidence: float = 0.85,
        importance: float = 0.5,
        review_status: str = "auto",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        source_plugin: str = "external",
        memory_id: str = "",
        occurred_at: str = "",
    ) -> str:
        return await self._plugin.record_external_event(
            content=content,
            memory_type=memory_type,
            scope=scope,
            session_id=session_id,
            platform=platform,
            message_id=message_id,
            group_id=group_id,
            subject=self._entity(subject) if subject else EntityRef.bot_self(),
            object=self._entity(object)
            if object
            else EntityRef(kind="session", id=session_id, role="target_session"),
            visibility=visibility,
            sayability=sayability,
            reality_level=reality_level,
            lifecycle=lifecycle,
            confidence=confidence,
            importance=importance,
            review_status=review_status,
            tags=tags or [],
            metadata=metadata or {},
            source_plugin=source_plugin,
            memory_id=memory_id,
            occurred_at=occurred_at,
        )

    async def record_external_memory(
        self,
        *,
        user_id: str = "",
        content: str = "",
        summary: str = "",
        payload: dict[str, Any] | None = None,
        memory_type: str = "external_memory",
        source_plugin: str = "external",
        occurred_at: str = "",
        idempotency_key: str = "",
        memory_id: str = "",
        importance: float = 0.62,
        confidence: float = 0.82,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        long_term: bool = True,
    ) -> dict[str, Any]:
        """Write one user-scoped long-term memory from an external source.

        ``user_id`` is intentionally the only required identity field.  The
        memory service fixes the record to that user's private scope, so an
        external plugin cannot accidentally write a group/public memory by
        passing arbitrary session fields.
        """
        writer = getattr(self._plugin, "record_external_memory", None)
        if not callable(writer):
            return {
                "ok": False,
                "state": "unsupported",
                "memory_id": "",
                "deduplicated": False,
                "error_code": "record_external_memory_unavailable",
            }
        result = await writer(
            user_id=user_id,
            content=content,
            summary=summary,
            payload=payload,
            memory_type=memory_type,
            source_plugin=source_plugin,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key,
            memory_id=memory_id,
            importance=importance,
            confidence=confidence,
            tags=tags,
            metadata=metadata,
            long_term=long_term,
        )
        if isinstance(result, dict):
            return dict(result)
        return {
            "ok": bool(result),
            "state": "stored" if result else "degraded",
            "memory_id": clean_text(result, 120),
            "deduplicated": False,
            "error_code": None if result else "invalid_result",
        }

    async def record_bot_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "self_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("source_plugin", kwargs.get("source_plugin", "external"))
        return await self.record_event(content=content, **kwargs)

    async def record_persona_life(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "persona_life")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "persona_life")
        kwargs.setdefault("sayability", "indirect")
        return await self.record_event(content=content, **kwargs)

    async def record_proactive_message(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "proactive_message")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["proactive", "bot_action"])
        kwargs.setdefault("importance", 0.55)
        return await self.record_event(content=content, **kwargs)

    async def record_visible_turn(
        self, *, role: str, content: str, **kwargs: Any
    ) -> str:
        """Record a real visible chat turn into the short-term timeline only."""
        return await self._plugin.record_visible_turn(
            role=role, content=content, **kwargs
        )

    async def record_shared_experience(
        self,
        *,
        content: str,
        experience_type: str,
        bot_id: str = "",
        bot_name: str = "",
        user_id: str = "",
        user_name: str = "",
        scope: str = "private",
        session_id: str = "",
        platform: str = "",
        source_plugin: str = "external",
        memory_id: str = "",
        confidence: float = 0.9,
        importance: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record one distilled call/watch experience with explicit ownership."""
        normalized_type = clean_text(experience_type, 40).lower()
        if normalized_type in {"watch", "shared_watch", "video"}:
            memory_type = "shared_watch"
            experience_tag = "watch"
        elif normalized_type in {"call", "shared_call", "voice"}:
            memory_type = "shared_call"
            experience_tag = "call"
        else:
            memory_type = "shared_experience"
            experience_tag = normalized_type or "shared"
        subject = EntityRef.bot_self(bot_id=bot_id, bot_name=bot_name)
        target = EntityRef(
            kind="user",
            id=clean_text(user_id, 120),
            name=clean_text(user_name, 80),
            role="shared_experience_partner",
        )
        return await self.record_event(
            content=content,
            memory_type=memory_type,
            scope=scope,
            session_id=session_id,
            platform=platform,
            subject={
                "kind": subject.kind,
                "id": subject.id,
                "name": subject.name,
                "role": subject.role,
            },
            object={
                "kind": target.kind,
                "id": target.id,
                "name": target.name,
                "role": target.role,
            },
            visibility="bot_self",
            sayability="direct",
            reality_level="bot_action",
            lifecycle="stable_memory",
            confidence=confidence,
            importance=importance,
            review_status="auto",
            tags=["shared_experience", experience_tag, "bot_action"],
            metadata=metadata or {},
            source_plugin=source_plugin,
            memory_id=memory_id,
        )

    async def record_search_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "search_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["search", "bot_action"])
        kwargs.setdefault("importance", 0.62)
        return await self.record_event(content=content, **kwargs)

    async def record_creative_work(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "creative_work")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "fictional_content")
        kwargs.setdefault("sayability", "direct")
        kwargs.setdefault("tags", ["creative_work"])
        kwargs.setdefault("importance", 0.72)
        return await self.record_event(content=content, **kwargs)

    async def record_image_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "image_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["image", "bot_action"])
        kwargs.setdefault("importance", 0.6)
        return await self.record_event(content=content, **kwargs)

    async def record_qzone_action(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "qzone_action")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["qzone", "bot_action"])
        kwargs.setdefault("importance", 0.58)
        return await self.record_event(content=content, **kwargs)

    async def record_reading(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "reading_memory")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "bot_action")
        kwargs.setdefault("tags", ["reading", "bot_action"])
        kwargs.setdefault("importance", 0.55)
        return await self.record_event(content=content, **kwargs)

    async def record_schedule_fragment(self, *, content: str, **kwargs: Any) -> str:
        kwargs.setdefault("memory_type", "schedule_fragment")
        kwargs.setdefault("visibility", "bot_self")
        kwargs.setdefault("reality_level", "persona_life")
        kwargs.setdefault("sayability", "indirect")
        kwargs.setdefault("tags", ["schedule", "persona_life"])
        kwargs.setdefault("importance", 0.45)
        return await self.record_event(content=content, **kwargs)
