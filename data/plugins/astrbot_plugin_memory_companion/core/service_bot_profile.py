from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .bot_personal_dto import (
    BotPersonalArchiveDTO,
    BotPersonalValidationError,
    bot_personal_payload_fingerprint,
    build_bot_personal_archive,
    sanitize_bot_personal_value,
)
from .models import (
    EntityRef,
    MemoryRecord,
    clean_text,
)
from .profile_api import build_profile_result


class MemoryCompanionServiceBotProfile:
    """Bot profile; state and transaction ownership remain in the facade."""

    async def lookup_bot_personal_archive(
        self, envelope: BotPersonalArchiveDTO | dict[str, Any]
    ) -> dict[str, Any]:
        """Reconcile an expected archive version without writing or returning its payload."""
        base = {
            "ok": False,
            "record_id": "",
            "deduplicated": False,
            "version": 0,
            "error_code": None,
            "state": "degraded",
        }
        try:
            dto = build_bot_personal_archive(envelope)
        except Exception as exc:
            return {
                **base,
                "state": "invalid",
                "error_code": getattr(exc, "error_code", "invalid"),
            }
        try:
            existing = await self.store.get_memory(dto.record_id)
        except Exception:
            return {**base, "error_code": "store_unavailable"}
        base["record_id"] = dto.record_id
        if existing is None:
            return {**base, "state": "missing", "error_code": "archive_not_found"}
        metadata = existing.metadata if isinstance(existing.metadata, dict) else {}
        old_version = int(metadata.get("version") or 0)
        base["version"] = old_version
        if old_version > dto.version:
            return {**base, "state": "stale_version", "error_code": "stale_version"}
        if old_version < dto.version:
            return {
                **base,
                "state": "missing",
                "error_code": "archive_version_not_found",
            }
        if metadata.get("payload_fingerprint") != bot_personal_payload_fingerprint(dto):
            return {
                **base,
                "state": "version_conflict",
                "error_code": "version_conflict",
            }
        return {**base, "ok": True, "deduplicated": True, "state": "deduplicated"}

    async def record_bot_personal_archive(
        self, envelope: BotPersonalArchiveDTO | dict[str, Any]
    ) -> dict[str, Any]:
        """Persist one Bot Personal envelope in its isolated memory domain."""
        result = {
            "ok": False,
            "record_id": "",
            "deduplicated": False,
            "version": 0,
            "error_code": None,
            "state": "degraded",
        }
        try:
            dto = build_bot_personal_archive(envelope)
        except BotPersonalValidationError as exc:
            return {
                **result,
                "state": "invalid",
                "error_code": exc.error_code,
                "field": exc.field,
            }
        except Exception:
            return {**result, "state": "invalid", "error_code": "invalid"}

        store = getattr(self, "store", None)
        getter = getattr(store, "get_memory", None)
        inserter = getattr(store, "insert_memory", None)
        if not callable(inserter):
            return {**result, "error_code": "store_unavailable", "state": "degraded"}
        fingerprint = bot_personal_payload_fingerprint(dto)
        existing = None
        if callable(getter):
            try:
                existing = await getter(dto.record_id)
            except Exception:
                return {
                    **result,
                    "record_id": dto.record_id,
                    "error_code": "store_unavailable",
                    "state": "degraded",
                }
        if existing is not None:
            metadata = existing.metadata if isinstance(existing.metadata, dict) else {}
            old_version = int(metadata.get("version") or 0)
            old_fingerprint = str(metadata.get("payload_fingerprint") or "")
            if old_version > dto.version:
                return {
                    **result,
                    "record_id": dto.record_id,
                    "version": old_version,
                    "error_code": "stale_version",
                    "state": "stale_version",
                }
            if old_version == dto.version:
                if old_fingerprint == fingerprint:
                    return {
                        **result,
                        "ok": True,
                        "record_id": dto.record_id,
                        "deduplicated": True,
                        "version": old_version,
                        "state": "deduplicated",
                    }
                return {
                    **result,
                    "record_id": dto.record_id,
                    "version": old_version,
                    "error_code": "version_conflict",
                    "state": "version_conflict",
                }

        payload = dict(dto.payload)
        # The raw payload stays in the isolated Bot domain for future internal use;
        # Profile readers below deliberately expose only a safe reference.
        metadata = {
            "bot_personal": True,
            "memory_domain": dto.memory_domain,
            "subject": dto.subject,
            "date": dto.date,
            "window": dto.window,
            "occurred_at": dto.occurred_at,
            "source_kind": dto.source_kind,
            "source_refs": list(dto.source_refs),
            "certainty": dto.certainty,
            "evidence_level": dto.evidence_level,
            "status": dto.status,
            "version": dto.version,
            "idempotency_key": dto.idempotency_key,
            "payload_schema_version": dto.payload_schema_version,
            "payload_fingerprint": fingerprint,
            "payload": payload,
            "canonical_schema_version": dto.canonical_schema_version,
            "window_date": getattr(dto, "window_date", "") or dto.date,
            "created_at": getattr(dto, "created_at", ""),
            "updated_at": getattr(dto, "updated_at", ""),
            "temporal_phase": getattr(dto, "temporal_phase", ""),
            "evidence_kind": dto.evidence_kind,
            "canonical_evidence_level": dto.canonical_evidence_level,
            "archive_evidence_level": dto.archive_evidence_level,
            "evidence_level_mapping": deepcopy(dto.evidence_level_mapping or {}),
            "authority_kind": dto.authority_kind,
            "commitment_level": dto.commitment_level,
            "epistemic_status": dto.epistemic_status,
            "content_granularity": dto.content_granularity,
            "materialization_state": dto.materialization_state,
            "fact_eligibility": dto.fact_eligibility,
            "actor_type": dto.actor_type,
            "subject_actor_id": dto.subject_actor_id,
            "object_actor_id": dto.object_actor_id,
            "source_actor_id": dto.source_actor_id,
            "target_user_id": dto.target_user_id,
            "participant_roles": deepcopy(dto.participant_roles or []),
            "runtime_origin_refs": list(dto.runtime_origin_refs or []),
            "expires_at": dto.expires_at,
            "decision_trace": deepcopy(dto.decision_trace or []),
            "owner_bot_id": dto.owner_bot_id,
            "persona_id": dto.persona_id,
            "legacy_namespace": dto.canonical_schema_version < 3,
        }
        reality_level = {
            "planned": "planned",
            "observed": "observed_activity",
            "reconciled": "reconciled",
            "projection": "window_projection",
            "subjective": "subjective",
            "creative": "creative_work",
            "media": "media_index",
            "shared": "shared_activity",
            "detail": "detail_fragment",
            "calendar": "calendar_event",
            "proactive": "proactive_action",
        }.get(dto.source_kind, dto.source_kind)
        namespace_digest = hashlib.sha256(
            f"{dto.owner_bot_id or 'legacy'}|{dto.persona_id}".encode()
        ).hexdigest()[:20]
        record = MemoryRecord(
            id=dto.record_id,
            memory_type=dto.memory_type,
            subject=EntityRef(kind="bot", id=dto.subject, role="subject"),
            object=EntityRef(kind="bot", id=dto.subject, role="object"),
            scope="private",
            session_id=f"bot_personal:{namespace_digest}",
            visibility="bot_self",
            sayability="indirect",
            reality_level=reality_level,
            lifecycle="planned_projection"
            if dto.status == "planned"
            else "stable_memory",
            content=f"Bot Personal archive reference [{dto.memory_type}]",
            evidence="; ".join(dto.source_refs),
            confidence=dto.certainty,
            importance=0.55,
            review_status="auto",
            tags=["bot_personal", dto.memory_type, dto.source_kind],
            metadata=metadata,
            source_plugin="bot_personal_bridge",
            occurred_at=dto.occurred_at,
            owner_bot_id=dto.owner_bot_id,
            valid_to=dto.expires_at,
            durability="ephemeral" if dto.expires_at else "normal",
            sensitivity="private",
            canonical_key=(
                f"bot_personal|{dto.owner_bot_id or 'legacy'}|{dto.persona_id}|"
                f"{dto.memory_type}|{dto.idempotency_key}"
            ),
            # MemoryStore's generic content dedupe intentionally ignores the
            # metadata payload.  Bot Personal records therefore need a stable
            # key-derived fingerprint or different agenda keys would merge
            # into one row merely because their display reference is similar.
            content_fingerprint=hashlib.sha256(
                (
                    f"bot_personal|{dto.owner_bot_id or 'legacy'}|{dto.persona_id}|"
                    f"{dto.memory_type}|{dto.idempotency_key}"
                ).encode()
            ).hexdigest(),
        )
        try:
            atomic_writer = getattr(store, "upsert_bot_personal_archive", None)
            if callable(atomic_writer):
                receipt = await atomic_writer(record)
                if not receipt.get("ok") or receipt.get("deduplicated"):
                    return receipt
                memory_id = receipt["record_id"]
            else:
                memory_id = await inserter(record)
            scheduler = getattr(self, "_schedule_memory_embedding", None)
            if callable(scheduler):
                try:
                    scheduler(memory_id, record)
                except Exception:
                    pass
        except Exception:
            return {
                **result,
                "record_id": dto.record_id,
                "version": dto.version,
                "error_code": "store_write_failed",
                "state": "degraded",
            }
        return {
            **result,
            "ok": True,
            "record_id": dto.record_id,
            "version": dto.version,
            "state": "sent",
        }

    async def record_bot_personal_memory(
        self, *, memory_type: str, payload: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            dto = build_bot_personal_archive(
                memory_type=memory_type, payload=payload or {}, **kwargs
            )
        except BotPersonalValidationError as exc:
            return {
                "ok": False,
                "record_id": "",
                "deduplicated": False,
                "version": 0,
                "error_code": exc.error_code,
                "state": "invalid",
            }
        return await self.record_bot_personal_archive(dto)

    async def read_bot_personal_profile(
        self,
        query: str = "",
        *,
        limit: int = 10,
        owner_bot_id: str = "",
        persona_id: str = "",
    ) -> dict[str, Any]:
        """Read safe references from exactly one Bot/persona namespace.

        Calls without namespace parameters are compatibility reads and can see
        legacy unowned rows only; current v3 rows never cross that boundary.
        """
        store = getattr(self, "store", None)
        lister = getattr(store, "list_memories", None)
        if not callable(lister):
            return {
                "ok": False,
                "read_only": True,
                "state": "degraded",
                "degraded": True,
                "pending": True,
                "items": [],
                "error_code": "store_unavailable",
            }
        try:
            records = await lister(
                limit=max(1, min(100, int(limit or 10) * 3)),
                include_pending=False,
                query="",
                scope="private",
                visibility="bot_self",
            )
        except Exception:
            return {
                "ok": False,
                "read_only": True,
                "state": "degraded",
                "degraded": True,
                "pending": True,
                "items": [],
                "error_code": "store_unavailable",
            }
        query_text = clean_text(query, 240).lower()
        items: list[dict[str, Any]] = []
        for record in records:
            metadata = record.metadata if isinstance(record.metadata, dict) else {}
            if clean_text(metadata.get("memory_domain"), 80) != "bot_self_schedule":
                continue
            record_bot_id = clean_text(
                metadata.get("owner_bot_id") or record.owner_bot_id, 120
            )
            record_persona_id = clean_text(metadata.get("persona_id"), 96)
            if owner_bot_id or persona_id:
                if record_bot_id != clean_text(
                    owner_bot_id, 120
                ) or record_persona_id != clean_text(persona_id, 96):
                    continue
            elif record_bot_id or record_persona_id not in {"", "legacy"}:
                continue
            source_refs: list[str] = []
            raw_source_refs = metadata.get("source_refs")
            if isinstance(raw_source_refs, (list, tuple)):
                for raw_reference in raw_source_refs:
                    if not isinstance(raw_reference, str):
                        continue
                    try:
                        safe_reference = sanitize_bot_personal_value(
                            raw_reference,
                            path="source_refs",
                        )
                    except BotPersonalValidationError:
                        continue
                    source_refs.append(clean_text(safe_reference, 240))
            searchable = " ".join(
                [
                    clean_text(record.memory_type, 80),
                    clean_text(metadata.get("date"), 20),
                    clean_text(metadata.get("window"), 40),
                    clean_text(metadata.get("source_kind"), 40),
                    clean_text((metadata.get("payload") or {}).get("summary"), 240)
                    if isinstance(metadata.get("payload"), dict)
                    else "",
                    clean_text((metadata.get("payload") or {}).get("title"), 240)
                    if isinstance(metadata.get("payload"), dict)
                    else "",
                    clean_text((metadata.get("payload") or {}).get("activity"), 240)
                    if isinstance(metadata.get("payload"), dict)
                    else "",
                ]
            ).lower()
            if query_text and query_text not in searchable:
                continue
            items.append(
                {
                    "record_id": record.id,
                    "memory_domain": "bot_self_schedule",
                    "memory_type": record.memory_type,
                    "subject": "bot_self",
                    "date": clean_text(metadata.get("date"), 20),
                    "window": clean_text(metadata.get("window"), 40),
                    "occurred_at": clean_text(record.occurred_at, 80),
                    "source_kind": clean_text(metadata.get("source_kind"), 40),
                    "source_refs": source_refs[:8],
                    "evidence_level": clean_text(metadata.get("evidence_level"), 8),
                    "status": clean_text(metadata.get("status"), 40),
                    "version": int(metadata.get("version") or 0),
                    "summary": f"Bot Personal archive reference [{record.memory_type}]",
                }
            )
        return {
            "ok": True,
            "read_only": True,
            "state": "ready",
            "degraded": False,
            "pending": False,
            "items": items[: max(1, min(100, int(limit or 10)))],
        }

    async def read_bot_profile(
        self,
        profile: str,
        query: str = "",
        *,
        limit: int = 10,
        current_date: str = "",
        current_window: str = "",
        authorized: bool = False,
    ) -> dict[str, Any]:
        """Read a C4 Bot Profile after hard domain and storage filtering."""

        store = getattr(self, "store", None)
        lister = getattr(store, "list_memories", None)
        if not callable(lister):
            return {
                "ok": False,
                "read_only": True,
                "state": "degraded",
                "degraded": True,
                "pending": True,
                "profile": clean_text(profile, 80),
                "items": [],
                "warnings": ["store_unavailable"],
            }
        try:
            safe_limit = max(1, min(100, int(limit or 10)))
        except (TypeError, ValueError):
            safe_limit = 10
        try:
            records = await lister(
                # The profile filter is applied again in profile_api; this
                # bounded overscan leaves room for malformed or other-domain
                # rows without scanning the whole database.
                limit=min(300, max(24, safe_limit * 3)),
                include_pending=False,
                query="",
                scope="private",
                visibility="bot_self",
            )
        except Exception:
            return {
                "ok": False,
                "read_only": True,
                "state": "degraded",
                "degraded": True,
                "pending": True,
                "profile": clean_text(profile, 80),
                "items": [],
                "warnings": ["store_unavailable"],
            }
        if not clean_text(current_date, 20) or not clean_text(current_window, 40):
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            current_date = current_date or now.strftime("%Y-%m-%d")
            current_window = current_window or self._bot_personal_window_for_datetime(
                now
            )
        return build_profile_result(
            records,
            profile,
            query=query,
            limit=safe_limit,
            current_date=current_date,
            current_window=current_window,
            authorized=authorized,
        )

    @staticmethod
    def _bot_personal_window_for_datetime(value: datetime) -> str:
        minutes = value.hour * 60 + value.minute
        try:
            from .bot_personal_contract import window_for_minutes

            return window_for_minutes(minutes)
        except Exception:
            return ""
