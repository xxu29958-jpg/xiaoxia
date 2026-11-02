from __future__ import annotations

from typing import Any

from . import bot_personal_contract
from .bot_personal_dto import BotPersonalArchiveDTO, build_bot_personal_archive
from .models import clean_text


class MemoryCompanionBridgeProfiles:
    """Profiles; shared runtime resources belong to the facade."""

    async def record_bot_personal_archive(
        self,
        envelope: BotPersonalArchiveDTO | dict[str, Any],
        *,
        producer_capability: Any = None,
        producer_context: Any = None,
    ) -> dict[str, Any]:
        """Send one validated Bot Personal archive envelope without leaking failures."""
        return await self._dispatch_bot_personal_archive(
            "record_bot_personal_archive",
            envelope,
            producer_capability=producer_capability,
            producer_context=producer_context,
        )

    async def lookup_bot_personal_archive(
        self,
        envelope: BotPersonalArchiveDTO | dict[str, Any],
        *,
        producer_capability: Any = None,
        producer_context: Any = None,
    ) -> dict[str, Any]:
        """Read an archive receipt through the same producer/namespace boundary."""
        return await self._dispatch_bot_personal_archive(
            "lookup_bot_personal_archive",
            envelope,
            producer_capability=producer_capability,
            producer_context=producer_context,
        )

    async def _dispatch_bot_personal_archive(
        self,
        method: str,
        envelope: BotPersonalArchiveDTO | dict[str, Any],
        *,
        producer_capability: Any,
        producer_context: Any,
    ) -> dict[str, Any]:
        base = {
            "ok": False,
            "record_id": "",
            "deduplicated": False,
            "version": 0,
            "error_code": None,
            "state": "degraded",
        }
        authority = (
            producer_capability if producer_capability is not None else producer_context
        )
        if not self._is_valid_private_companion_capability(authority):
            return {
                **base,
                "state": "forbidden",
                "error_code": "producer_capability_required",
            }
        try:
            dto = build_bot_personal_archive(envelope)
        except Exception as exc:
            return {
                **base,
                "state": "invalid",
                "error_code": getattr(exc, "error_code", "invalid"),
            }
        if (
            dto.canonical_schema_version
            >= bot_personal_contract.BOT_PERSONAL_CANONICAL_SCHEMA_VERSION
        ):
            capability = self._producer_capability_from(authority)
            producer = getattr(capability, "_producer", None)
            bot_getter = getattr(producer, "_memory_companion_bridge_bot_id", None)
            persona_getter = getattr(
                producer, "_memory_companion_archive_persona_id", None
            )
            try:
                expected_bot_id = (
                    clean_text(bot_getter(), 120) if callable(bot_getter) else ""
                )
                expected_persona_id = (
                    clean_text(persona_getter(), 96) if callable(persona_getter) else ""
                )
            except Exception:
                expected_bot_id = expected_persona_id = ""
            if (
                not expected_bot_id
                or not expected_persona_id
                or dto.owner_bot_id != expected_bot_id
                or dto.persona_id != expected_persona_id
            ):
                return {
                    **base,
                    "state": "forbidden",
                    "error_code": "producer_namespace_mismatch",
                }
        try:
            recorder = getattr(self._plugin, method, None)
        except Exception:
            recorder = None
        if not callable(recorder):
            return {
                **base,
                "error_code": "bridge_method_unavailable",
                "state": "degraded",
            }
        try:
            result = await recorder(dto)
        except Exception:
            return {**base, "error_code": "bridge_exception", "state": "degraded"}
        if not isinstance(result, dict):
            return {
                **base,
                "error_code": "invalid_bridge_response",
                "state": "degraded",
            }
        normalized = dict(base)
        for key in base:
            if key in result:
                normalized[key] = result[key]
        normalized["ok"] = bool(result.get("ok"))
        normalized["deduplicated"] = bool(result.get("deduplicated"))
        normalized["version"] = int(result.get("version") or 0)
        if normalized["ok"]:
            normalized["state"] = (
                "deduplicated" if normalized["deduplicated"] else "sent"
            )
        elif normalized["state"] == "ready":
            normalized["state"] = "degraded"
        return normalized

    async def record_bot_personal_memory(
        self,
        *,
        memory_type: str,
        payload: dict[str, Any] | None = None,
        producer_capability: Any = None,
        producer_context: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Compatibility alias that still crosses the structured archive boundary."""
        try:
            envelope = build_bot_personal_archive(
                memory_type=memory_type, payload=payload or {}, **kwargs
            )
        except Exception as exc:
            return {
                "ok": False,
                "record_id": "",
                "deduplicated": False,
                "version": 0,
                "error_code": getattr(exc, "error_code", "invalid"),
                "state": "invalid",
            }
        return await self.record_bot_personal_archive(
            envelope,
            producer_capability=producer_capability,
            producer_context=producer_context,
        )

    async def read_bot_personal_profile(
        self,
        query: str = "",
        *,
        limit: int = 10,
        producer_capability: Any = None,
    ) -> dict[str, Any]:
        """Read only safe Bot Personal summaries; never return archive payloads."""
        base = {
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "items": [],
        }
        owner_bot_id = ""
        persona_id = ""
        if producer_capability is not None:
            if not self._is_valid_private_companion_capability(producer_capability):
                return {
                    **base,
                    "state": "forbidden",
                    "degraded": False,
                    "pending": False,
                    "error_code": "producer_capability_required",
                }
            capability = self._producer_capability_from(producer_capability)
            producer = getattr(capability, "_producer", None)
            bot_getter = getattr(producer, "_memory_companion_bridge_bot_id", None)
            persona_getter = getattr(
                producer, "_memory_companion_archive_persona_id", None
            )
            try:
                owner_bot_id = (
                    clean_text(bot_getter(), 120) if callable(bot_getter) else ""
                )
                persona_id = (
                    clean_text(persona_getter(), 96) if callable(persona_getter) else ""
                )
            except Exception:
                owner_bot_id = persona_id = ""
            if not owner_bot_id or not persona_id:
                return {
                    **base,
                    "state": "forbidden",
                    "degraded": False,
                    "pending": False,
                    "error_code": "producer_namespace_unavailable",
                }
        try:
            getter = getattr(self._plugin, "read_bot_personal_profile", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {**base, "error_code": "bridge_method_unavailable"}
        try:
            result = await getter(
                query=query,
                limit=limit,
                owner_bot_id=owner_bot_id,
                persona_id=persona_id,
            )
        except Exception:
            return {**base, "error_code": "bridge_exception"}
        if not isinstance(result, dict):
            return {**base, "error_code": "invalid_bridge_response"}
        safe_keys = {
            "record_id",
            "memory_type",
            "memory_domain",
            "subject",
            "date",
            "window",
            "occurred_at",
            "source_kind",
            "source_refs",
            "evidence_level",
            "status",
            "version",
            "summary",
            "reference",
        }
        items = result.get("items", result.get("memories", []))
        safe_items: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            safe_items.append(
                {
                    key: item[key]
                    for key in safe_keys
                    if key in item and key not in {"payload", "content"}
                }
            )
        return {
            "ok": bool(result.get("ok", True)),
            "read_only": True,
            "state": "ready"
            if result.get("state") in (None, "ready")
            else result.get("state"),
            "degraded": bool(result.get("degraded", False)),
            "pending": bool(result.get("pending", False)),
            "items": safe_items,
        }

    async def read_user_memory_summary(
        self,
        user_id: str,
        *,
        session_id: str = "",
        limit: int = 6,
        requester_context: Any = None,
    ) -> dict[str, Any]:
        """Read a strict, exact-user Memory summary without exposing memory text."""

        identity = clean_text(user_id, 120)
        safe_session = clean_text(session_id, 200)
        base = {
            "contract": "memory.user_memory_summary.v1",
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "user_id": identity,
            "session_id": safe_session,
            "counts": {
                "profile": 0,
                "preference": 0,
                "relationship": 0,
                "private_conversation": 0,
                "other": 0,
                "total": 0,
            },
            "summaries": [],
            "workspace": {
                "kind": "memory_user_workspace",
                "route_hint": "user_memory",
                "user_id": identity,
            },
        }
        if not identity:
            return {**base, "error_code": "missing_user_id"}
        if not self._is_valid_emotion_producer_context(requester_context):
            return {
                **base,
                "state": "forbidden",
                "degraded": False,
                "pending": False,
                "error_code": "requester_context_required",
            }
        if requester_context.user_id != identity:
            return {
                **base,
                "state": "forbidden",
                "degraded": False,
                "pending": False,
                "error_code": "requester_identity_mismatch",
            }
        if safe_session and requester_context.session_id != safe_session:
            return {
                **base,
                "state": "forbidden",
                "degraded": False,
                "pending": False,
                "error_code": "requester_session_mismatch",
            }
        safe_session = requester_context.session_id
        base["session_id"] = safe_session
        try:
            getter = getattr(self._plugin, "read_user_memory_summary", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {**base, "error_code": "bridge_method_unavailable"}
        try:
            result = await getter(identity, session_id=safe_session, limit=limit)
        except Exception:
            return {**base, "error_code": "bridge_exception"}
        if not isinstance(result, dict) or result.get("contract") != base["contract"]:
            return {**base, "error_code": "invalid_bridge_response"}

        counts = dict(base["counts"])
        raw_counts = (
            result.get("counts") if isinstance(result.get("counts"), dict) else {}
        )
        for key in counts:
            try:
                counts[key] = max(0, int(raw_counts.get(key, 0)))
            except (TypeError, ValueError, OverflowError):
                counts[key] = 0
        summaries: list[dict[str, Any]] = []
        for item in (
            result.get("summaries", [])
            if isinstance(result.get("summaries"), list)
            else []
        ):
            if not isinstance(item, dict):
                continue
            category = clean_text(item.get("category"), 40)
            if category not in {
                "profile",
                "preference",
                "relationship",
                "private_conversation",
                "other",
            }:
                continue
            summaries.append(
                {
                    "category": category,
                    "memory_type": clean_text(item.get("memory_type"), 80),
                    "occurred_at": clean_text(item.get("occurred_at"), 80),
                    "summary": clean_text(item.get("summary"), 100),
                    "content_redacted": True,
                    "truncated": True,
                }
            )
            if len(summaries) >= 8:
                break
        state = clean_text(result.get("state"), 40)
        return {
            **base,
            "ok": bool(result.get("ok")) and state == "ready",
            "state": "ready" if state == "ready" else "degraded",
            "degraded": state != "ready" or bool(result.get("degraded", False)),
            "pending": state != "ready" or bool(result.get("pending", False)),
            "user_id": identity,
            "session_id": safe_session,
            "counts": counts,
            "summaries": summaries,
            "workspace": base["workspace"],
            "memory_revision": (
                result["memory_revision"]
                if type(result.get("memory_revision")) is int
                and result["memory_revision"] >= 0
                else None
            ),
            **(
                {"error_code": clean_text(result.get("error_code"), 80)}
                if state != "ready" and clean_text(result.get("error_code"), 80)
                else {}
            ),
        }

    async def read_unified_profile_portrait(
        self, request: dict[str, Any], *, limit: int = 8
    ) -> dict[str, Any]:
        """Return only a pre-authorized low-sensitivity portrait summary."""
        base = {
            "ok": False,
            "read_only": True,
            "code": "bridge_unavailable",
            "items": [],
        }
        getter = getattr(self._plugin, "read_unified_profile_portrait", None)
        if not callable(getter):
            return base
        try:
            result = await getter(
                request if isinstance(request, dict) else {},
                limit=max(1, min(16, int(limit))),
            )
        except Exception:
            return {**base, "code": "bridge_degraded"}
        if not isinstance(result, dict):
            return {**base, "code": "bridge_degraded"}
        items: list[dict[str, Any]] = []
        for item in (
            result.get("items", []) if isinstance(result.get("items"), list) else []
        ):
            if not isinstance(item, dict):
                continue
            if clean_text(item.get("sensitivity"), 24) != "low":
                continue
            items.append(
                {
                    "dimension": clean_text(item.get("dimension"), 80),
                    "summary": clean_text(item.get("summary"), 180),
                    "portrait_tier": clean_text(item.get("portrait_tier"), 24),
                    "epistemic_status": clean_text(item.get("epistemic_status"), 40),
                    "confidence": float(item.get("confidence") or 0),
                    "updated_at": clean_text(item.get("updated_at"), 80),
                }
            )
        return {
            "ok": bool(result.get("ok")),
            "read_only": True,
            "code": clean_text(result.get("code"), 80) or "bridge_degraded",
            "items": items,
            "portrait_revision": int(result.get("portrait_revision") or 0),
        }

    async def unified_profile_portrait_status(self, person_id: str) -> dict[str, Any]:
        """Return only bridge synchronization metadata, never portrait text."""
        getter = getattr(self._plugin, "unified_profile_portrait_status", None)
        if not callable(getter):
            return {
                "ok": False,
                "read_only": True,
                "code": "bridge_unavailable",
                "last_synced_at": "",
                "portrait_revision": 0,
            }
        try:
            result = await getter(clean_text(person_id, 80))
        except Exception:
            return {
                "ok": False,
                "read_only": True,
                "code": "bridge_degraded",
                "last_synced_at": "",
                "portrait_revision": 0,
            }
        if not isinstance(result, dict):
            return {
                "ok": False,
                "read_only": True,
                "code": "bridge_degraded",
                "last_synced_at": "",
                "portrait_revision": 0,
            }
        return {
            "ok": bool(result.get("ok")),
            "read_only": True,
            "code": clean_text(result.get("code"), 80) or "bridge_degraded",
            "last_synced_at": clean_text(result.get("last_synced_at"), 80),
            "portrait_revision": int(result.get("portrait_revision") or 0),
        }

    async def run_unified_profile_portrait_batch(
        self, person_id: str, *, run_day: str = ""
    ) -> dict[str, Any]:
        getter = getattr(self._plugin, "run_unified_profile_portrait_batch", None)
        if not callable(getter):
            return {"ok": False, "code": "bridge_unavailable"}
        try:
            result = await getter(
                clean_text(person_id, 80), run_day=clean_text(run_day, 16)
            )
        except Exception:
            return {"ok": False, "code": "bridge_degraded"}
        return (
            dict(result)
            if isinstance(result, dict)
            else {"ok": False, "code": "bridge_degraded"}
        )

    async def search_bot_personal_profile(
        self,
        query: str = "",
        *,
        limit: int = 10,
        producer_capability: Any = None,
    ) -> dict[str, Any]:
        return await self.read_bot_personal_profile(
            query=query,
            limit=limit,
            producer_capability=producer_capability,
        )

    async def read_bot_profile(
        self,
        profile: str,
        query: str = "",
        *,
        limit: int = 10,
        current_date: str = "",
        current_window: str = "",
        authorized: bool = False,
        producer_capability: Any = None,
        producer_context: Any = None,
    ) -> dict[str, Any]:
        """Read a C4 Bot Profile through a privacy-limited bridge boundary."""

        base = {
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "profile": clean_text(profile, 80),
            "items": [],
            "warnings": [],
        }
        authority = (
            producer_capability if producer_capability is not None else producer_context
        )
        locked_authorized = self._is_valid_private_companion_capability(authority)
        if base["profile"] == "locked_frame_personal" and not locked_authorized:
            return {
                **base,
                "state": "forbidden",
                "degraded": False,
                "pending": False,
                "warnings": ["authorization_required"],
                "error_code": "producer_capability_required",
            }
        _ = authorized
        try:
            getter = getattr(self._plugin, "read_bot_profile", None)
        except Exception:
            getter = None
        if not callable(getter):
            return {**base, "error_code": "bridge_method_unavailable"}
        try:
            result = await getter(
                profile,
                query=query,
                limit=limit,
                current_date=current_date,
                current_window=current_window,
                authorized=locked_authorized,
            )
        except Exception:
            return {**base, "error_code": "bridge_exception"}
        if not isinstance(result, dict):
            return {**base, "error_code": "invalid_bridge_response"}
        safe_item_keys = {
            "record_id",
            "memory_domain",
            "memory_type",
            "subject",
            "date",
            "window",
            "occurred_at",
            "source_kind",
            "source_refs",
            "evidence_level",
            "status",
            "version",
            "summary",
            "reference",
        }
        safe_items: list[dict[str, Any]] = []
        items = result.get("items", [])
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                safe_items.append(
                    {key: item[key] for key in safe_item_keys if key in item}
                )
        return {
            "ok": bool(result.get("ok", True)),
            "read_only": True,
            "state": clean_text(result.get("state"), 40) or "ready",
            "degraded": bool(result.get("degraded", False)),
            "pending": bool(result.get("pending", False)),
            "profile": clean_text(result.get("profile") or profile, 80),
            "items": safe_items,
            "warnings": [
                clean_text(item, 160)
                for item in result.get("warnings", [])
                if clean_text(item, 160)
            ][:8]
            if isinstance(result.get("warnings"), list)
            else [],
        }

    async def read_profile(
        self, profile: str, query: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        """Short alias for callers that use the generic Profile API name."""

        return await self.read_bot_profile(profile, query=query, **kwargs)
