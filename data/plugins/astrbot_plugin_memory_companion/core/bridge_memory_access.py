from __future__ import annotations

import json
from typing import Any

from .context_consumer import consume_context_projection
from .models import SessionContext
from .person_projection import consume_person_projection


class MemoryCompanionBridgeMemoryAccess:
    """Memory access; shared runtime resources belong to the facade."""

    async def search(
        self,
        query: str,
        *,
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> list[dict[str, Any]]:
        return await self._plugin.bridge_search(
            query,
            session_context=session_context,
            top_k=top_k,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    async def compose_injection(
        self,
        query: str,
        *,
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> str:
        return await self._plugin.bridge_compose_injection(
            query,
            session_context=session_context,
            top_k=top_k,
            max_chars=max_chars,
            companion_bot_mood=companion_bot_mood,
            companion_bot_energy=companion_bot_energy,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    async def compose_context(
        self,
        *,
        query: str = "",
        session_context: SessionContext | dict[str, Any] | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        retrieval_profile: str = "",
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> str:
        return await self._plugin.bridge_compose_context(
            query=query,
            session_context=session_context,
            top_k=top_k,
            max_chars=max_chars,
            companion_bot_mood=companion_bot_mood,
            companion_bot_energy=companion_bot_energy,
            retrieval_profile=retrieval_profile,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    async def correct_user_memory(self, *, event: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._plugin.correct_user_memory(event=event, **kwargs)

    async def check_memory_dependencies(
        self, *, event: Any, refs: list[dict[str, str]]
    ) -> dict[str, Any]:
        return await self._plugin.check_memory_dependencies(event=event, refs=refs)

    async def open_role_input_sources(
        self, *, event, producer_capability, context, identity
    ):
        """Explicit original-role assembly; does not enable a production writer."""
        if not self._is_valid_private_companion_capability(producer_capability):
            raise ValueError("memory_role_producer_unavailable")
        from .role_input_sources import RoleInputSources

        owner = getattr(self._plugin, "_role_input_sources", None)
        if owner is None:
            owner = RoleInputSources(self._plugin)
            self._plugin._role_input_sources = owner
        return await owner.open(self, event, producer_capability, context, identity)

    async def open_role_task_sources(self, *, task_ticket, producer_capability):
        """Pull the live task grant from the registered producer, not caller JSON."""
        if not self._is_valid_private_companion_capability(producer_capability):
            raise ValueError("memory_role_producer_unavailable")
        producer = self._producer_capability_from(producer_capability)._producer
        runtime = getattr(producer, "_s4_work_runtime", None)
        manager = getattr(runtime, "role_tasks", None)
        if manager is None or runtime.plugin is not producer:
            raise ValueError("memory_role_task_producer_unavailable")
        from .role_input_sources import RoleInputSources

        owner = getattr(self._plugin, "_role_input_sources", None)
        if owner is None:
            owner = RoleInputSources(self._plugin)
            self._plugin._role_input_sources = owner
        return await owner.open_task(self, producer_capability, manager, task_ticket)

    async def bind_life_sources(
        self, *, event, refs, binding, producer_capability=None
    ):
        if not self._is_valid_private_companion_capability(producer_capability):
            return {"status": "unavailable", "items": []}
        from .source_watches import bind

        result = await bind(self._plugin, event, refs, binding)
        return (
            result
            if self._is_valid_private_companion_capability(producer_capability)
            else {"status": "unavailable", "items": []}
        )

    def check_life_sources(self, *, binding, tickets, producer_capability=None):
        if not self._is_valid_private_companion_capability(producer_capability):
            return {"status": "unavailable", "items": []}
        from .source_watches import check

        return check(self._plugin, binding, tickets)

    async def source_capture_permission(self, *, event, producer_capability=None):
        if not self._is_valid_private_companion_capability(producer_capability):
            return False
        capture = getattr(self._plugin, "capture", None)
        if capture is None:
            return False
        ctx = self._plugin._normalized_session_context(
            await self._plugin.identity.resolve_event_context(event)
        )
        identity = capture.identity(ctx, direction="incoming", message="permission")
        setattr(
            event,
            "_s4_memory_source_context",
            {
                key: identity[key]
                for key in (
                    "platform",
                    "bot",
                    "persona",
                    "scope",
                    "session",
                    "audience",
                )
            },
        )
        return capture.allowed(
            identity
        ) and self._is_valid_private_companion_capability(producer_capability)

    async def accept_source_withdrawal(self, *, ticket, producer_capability=None):
        if not self._is_valid_private_companion_capability(producer_capability):
            return {"status": "unavailable"}
        producer = self._producer_capability_from(producer_capability)._producer
        work = getattr(producer, "_s4_work_runtime", None)
        delivery = getattr(work, "delivery", None)
        capture = getattr(self._plugin, "capture", None)
        if delivery is None or capture is None:
            return {"status": "unavailable"}
        notice = delivery.export_notice(ticket)
        if not notice:
            return {"status": "unavailable"}
        if delivery.row(ticket) and not delivery.bind_memory(
            ticket, capture.store.installation
        ):
            return {"status": "held", "reason": "memory_owner_changed"}

        from .source_capture import digest

        context = json.loads(notice["context"])
        if set(context) != {
            "platform",
            "bot",
            "persona",
            "scope",
            "session",
            "audience",
        }:
            return {"status": "held", "reason": "capture_scope_unbound"}
        identity = dict(
            context,
            installation=capture.store.installation,
            speaker=context["audience"],
            direction="incoming",
            message=notice["native_id"],
        )
        # This contains no text, and remains a cleanup request after capture is disabled.
        return capture.store.withdraw(digest(identity), reason="native_friend_recall")

    async def accept_source_delivery(self, *, ticket, producer_capability=None):
        """Pull a part from the registered Delivery owner, never from caller text."""
        if not self._is_valid_private_companion_capability(producer_capability):
            return {"status": "unavailable"}
        producer = self._producer_capability_from(producer_capability)._producer
        work = getattr(producer, "_s4_work_runtime", None)
        delivery = getattr(work, "delivery", None)
        capture = getattr(self._plugin, "capture", None)
        if delivery is None or capture is None:
            return {"status": "unavailable"}
        exported = delivery.export(ticket)
        if not exported:
            return {"status": "excluded"}
        row, scope, payload = exported
        if not delivery.bind_memory(ticket, capture.store.installation):
            return {"status": "held", "reason": "memory_owner_changed"}
        context = payload.get("memory_context", {})
        if set(context) != {
            "platform",
            "bot",
            "persona",
            "scope",
            "session",
            "audience",
        }:
            return {"status": "held", "reason": "capture_scope_unbound"}
        if context["persona"] != scope.persona_id or context["scope"] != "private":
            return {"status": "held", "reason": "capture_scope_mismatch"}
        identity = dict(
            context,
            installation=capture.store.installation,
            speaker=context["audience"]
            if row["direction"] == "incoming"
            else context["bot"],
            direction=row["direction"],
            message=row["native_id"]
            if row["direction"] == "incoming"
            else "delivery:" + ticket,
        )

        def authorize():
            return (
                self._is_valid_private_companion_capability(producer_capability)
                and work.allowed(scope)
                and delivery.export(ticket) is not None
            )

        return capture.submit(
            identity,
            {
                "body": payload["body"],
                "media": payload["media"],
                "source_message_at": payload.get("source_message_at", ""),
                "observed_at": payload["observed_at"],
                "evidence": row["evidence"],
                "raw_body_digest": payload.get("raw_body_digest", ""),
            },
            authorize=authorize,
        )

    async def bind_life_message_sources(
        self, *, event, binding, producer_capability=None
    ):
        if not self._is_valid_private_companion_capability(producer_capability):
            return {"status": "unavailable", "items": []}
        from .message_sources import bind

        result = await bind(self._plugin, event, binding)
        return (
            result
            if self._is_valid_private_companion_capability(producer_capability)
            else {"status": "unavailable", "items": []}
        )

    def check_life_message_sources(self, *, binding, tickets, producer_capability=None):
        if not self._is_valid_private_companion_capability(producer_capability):
            return {"status": "unavailable", "items": []}
        from .message_sources import check

        return check(self._plugin, binding, tickets)

    async def manage_life_message_source(
        self, *, event, producer_capability=None, **request
    ):
        if not self._is_valid_private_companion_capability(producer_capability):
            return {"status": "unavailable", "ok": False}
        from .message_sources import manage

        return await manage(
            self._plugin,
            event,
            authorize=lambda: self._is_valid_private_companion_capability(
                producer_capability
            ),
            **request,
        )

    async def remember(
        self, *, event: Any, content: str, note_type: str = "memory"
    ) -> dict[str, Any]:
        return await self._plugin.tool_remember(event, content, note_type=note_type)

    async def recall(
        self,
        *,
        event: Any,
        query: str,
        top_k: int = 5,
        p5_attestation: Any = None,
        p5_attestation_consumer: Any = None,
    ) -> dict[str, Any]:
        return await self._plugin.tool_recall(
            event,
            query,
            top_k=top_k,
            p5_attestation=p5_attestation,
            p5_attestation_consumer=p5_attestation_consumer,
        )

    def p5_capability_status(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "p5_capability_status", None)
        if not callable(getter):
            return {"state": "degraded", "error_code": "p5_status_unavailable"}
        try:
            result = getter()
        except Exception:
            return {"state": "degraded", "error_code": "p5_status_exception"}
        return (
            dict(result)
            if isinstance(result, dict)
            else {"state": "degraded", "error_code": "p5_status_invalid"}
        )

    def provenance_snapshot(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_snapshot", None)
        if not callable(getter):
            return {"records": {}, "operation_count": 0, "state": "degraded"}
        result = getter()
        return (
            dict(result)
            if isinstance(result, dict)
            else {"records": {}, "operation_count": 0, "state": "degraded"}
        )

    def provenance_preview(
        self, candidates: list[dict[str, Any]], *, operation_ref_hash: str
    ) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_preview", None)
        if not callable(getter):
            return {
                "mode": "preview",
                "readonly": True,
                "write_count": 0,
                "error_codes": ["unavailable"],
            }
        result = getter(candidates, operation_ref_hash=operation_ref_hash)
        return (
            dict(result)
            if isinstance(result, dict)
            else {
                "mode": "preview",
                "readonly": True,
                "write_count": 0,
                "error_codes": ["invalid_result"],
            }
        )

    async def provenance_apply(self, operation: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_apply", None)
        if not callable(getter):
            return {"ok": False, "state": "degraded", "error_code": "unavailable"}
        result = await getter(operation)
        return (
            dict(result)
            if isinstance(result, dict)
            else {"ok": False, "state": "degraded", "error_code": "invalid_result"}
        )

    async def provenance_backup(self) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_backup", None)
        if not callable(getter):
            return {"ok": False, "state": "degraded", "error_code": "unavailable"}
        result = await getter()
        return (
            dict(result)
            if isinstance(result, dict)
            else {"ok": False, "state": "degraded", "error_code": "invalid_result"}
        )

    async def provenance_rollback(self, operation: dict[str, Any]) -> dict[str, Any]:
        getter = getattr(self._plugin, "provenance_rollback", None)
        if not callable(getter):
            return {"ok": False, "state": "degraded", "error_code": "unavailable"}
        result = await getter(operation)
        return (
            dict(result)
            if isinstance(result, dict)
            else {"ok": False, "state": "degraded", "error_code": "invalid_result"}
        )

    async def create_note(
        self, *, event: Any, title: str, content: str = ""
    ) -> dict[str, Any]:
        return await self._plugin.tool_note_create(event, title, content)

    async def read_notes(
        self, *, event: Any, query: str = "", limit: int = 5
    ) -> dict[str, Any]:
        return await self._plugin.tool_note_read(event, query, limit=limit)

    async def delete_note(
        self, *, event: Any, memory_id: str = "", title: str = ""
    ) -> dict[str, Any]:
        return await self._plugin.tool_note_delete(event, memory_id, title=title)

    def coordination_status(self) -> dict[str, Any]:
        try:
            getter = getattr(self._plugin, "companion_coordination_status", None)
        except Exception:
            return {
                "available": False,
                "state": "degraded",
                "degraded": True,
                "reason": "bridge_exception",
            }
        if not callable(getter):
            return {
                "available": False,
                "state": "degraded",
                "degraded": True,
                "reason": "method_missing",
            }
        try:
            result = getter()
        except Exception as exc:
            return {
                "available": False,
                "state": "degraded",
                "degraded": True,
                "reason": "bridge_exception",
                "error": str(exc)[:160],
            }
        if not isinstance(result, dict):
            return {
                "available": False,
                "state": "degraded",
                "degraded": True,
                "reason": "invalid_status",
            }
        result = dict(result)
        result.setdefault("available", True)
        result.setdefault("state", "ready")
        result.setdefault("degraded", False)
        return result

    def consume_person_projection(
        self,
        projection: Any,
        expected_identity_key: str = "",
        expected_person_id: str = "",
        *,
        companion_available: bool = True,
    ) -> dict[str, Any]:
        """Validate a companion-owned person projection without writing memory state."""
        return consume_person_projection(
            projection,
            expected_identity_key=expected_identity_key,
            expected_person_id=expected_person_id,
            companion_available=companion_available,
        )

    def consume_context_projection(
        self,
        context: Any,
        expected_person_id: str = "",
        expected_scope: str = "",
        *,
        companion_available: bool = True,
    ) -> dict[str, Any]:
        """Validate a P3 projection without creating people or storing raw text."""
        return consume_context_projection(
            context,
            expected_person_id=expected_person_id,
            expected_scope=expected_scope,
            companion_available=companion_available,
        )
