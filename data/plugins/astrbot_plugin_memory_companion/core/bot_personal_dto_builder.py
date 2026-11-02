from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .bot_personal_contract import (
    BOT_PERSONAL_MAX_PAYLOAD_BYTES,
    BOT_PERSONAL_MEMORY_DOMAIN,
    BOT_PERSONAL_MEMORY_TYPES,
    BOT_PERSONAL_SUBJECT,
    TYPE_CONTRACTS,
)
from .bot_personal_dto_common import (
    _FORBIDDEN_KEY_PARTS,
    _FORBIDDEN_TOP_LEVEL_KEYS,
    ARCHIVE_EVIDENCE_LEVELS,
    AUTHORITY_KINDS,
    CANONICAL_EVIDENCE_LEVELS,
    COMMITMENT_LEVELS,
    CONTENT_GRANULARITIES,
    EVIDENCE_KINDS,
    EXTERNAL_AUTHORITIES,
    FACT_ELIGIBILITIES,
    MATERIALIZATION_STATES,
    BotPersonalValidationError,
)
from .bot_personal_dto_models import BotPersonalArchiveDTO
from .bot_personal_dto_schedule_authority import _has_trusted_schedule_ref
from .bot_personal_dto_validation import (
    _archive_namespace,
    _archive_time_and_references,
    _canonical_mapping,
    _canonical_text,
    _json_size,
    _key_name,
    _raw_field,
    _string_refs,
    _text,
    sanitize_bot_personal_value,
)


@dataclass(frozen=True)
class ArchiveEvidence:
    """Resolved evidence axes; model assertions do not grant execution authority."""

    requested_canonical: str
    archive_evidence: str
    requested_authority: str
    commitment_value: str
    source_kind_value: str
    status_value: str
    evidence_kind_value: str
    epistemic_value: str
    granularity_value: str
    materialization_value: str
    fact_value: str
    refs: tuple[str, ...]
    expires_at: str


def build_bot_personal_archive(
    envelope: BotPersonalArchiveDTO | Mapping[str, Any] | None = None,
    *,
    memory_type: str | None = None,
    payload: Mapping[str, Any] | None = None,
    record_id: str = "",
    memory_domain: str | None = None,
    subject: str | None = None,
    date: str = "",
    window: str = "",
    occurred_at: str = "",
    source_kind: str = "",
    source_refs: Any = None,
    certainty: Any = None,
    evidence_level: str = "",
    status: str = "",
    version: Any = None,
    idempotency_key: str = "",
    payload_schema_version: str | None = None,
    now: datetime | None = None,
    evidence_kind: str = "",
    canonical_evidence_level: str = "",
    archive_evidence_level: str = "",
    evidence_level_mapping: Any = None,
    authority_kind: str = "",
    commitment_level: str = "",
    epistemic_status: str = "",
    content_granularity: str = "",
    materialization_state: str = "",
    fact_eligibility: str = "",
    actor_type: str = "",
    subject_actor_id: str = "",
    object_actor_id: str = "",
    source_actor_id: str = "",
    target_user_id: str = "",
    participant_roles: Any = None,
    runtime_origin_refs: Any = None,
    expires_at: str = "",
    decision_trace: Any = None,
    owner_bot_id: str = "",
    persona_id: str = "",
    canonical_schema_version: Any = None,
) -> BotPersonalArchiveDTO:
    if isinstance(envelope, BotPersonalArchiveDTO):
        return envelope
    source: dict[str, Any] = (
        dict(envelope or {}) if isinstance(envelope, Mapping) else {}
    )
    if envelope is not None and not isinstance(
        envelope, (BotPersonalArchiveDTO, Mapping)
    ):
        raise BotPersonalValidationError(
            "invalid", "envelope", "envelope must be an object"
        )
    supplied = {
        key: value
        for key, value in {
            "memory_type": memory_type,
            "payload": payload,
            "record_id": record_id,
            "memory_domain": memory_domain,
            "subject": subject,
            "date": date,
            "window": window,
            "occurred_at": occurred_at,
            "source_kind": source_kind,
            "source_refs": source_refs,
            "certainty": certainty,
            "evidence_level": evidence_level,
            "status": status,
            "version": version,
            "idempotency_key": idempotency_key,
            "payload_schema_version": payload_schema_version,
            "evidence_kind": evidence_kind,
            "canonical_evidence_level": canonical_evidence_level,
            "archive_evidence_level": archive_evidence_level,
            "evidence_level_mapping": evidence_level_mapping,
            "authority_kind": authority_kind,
            "commitment_level": commitment_level,
            "epistemic_status": epistemic_status,
            "content_granularity": content_granularity,
            "materialization_state": materialization_state,
            "fact_eligibility": fact_eligibility,
            "actor_type": actor_type,
            "subject_actor_id": subject_actor_id,
            "object_actor_id": object_actor_id,
            "source_actor_id": source_actor_id,
            "target_user_id": target_user_id,
            "participant_roles": participant_roles,
            "runtime_origin_refs": runtime_origin_refs,
            "expires_at": expires_at,
            "decision_trace": decision_trace,
            "owner_bot_id": owner_bot_id,
            "persona_id": persona_id,
            "canonical_schema_version": canonical_schema_version,
        }.items()
        if value not in (None, "")
    }
    source.update(supplied)
    for key in source:
        if _key_name(key) in _FORBIDDEN_TOP_LEVEL_KEYS or any(
            part in _key_name(key) for part in _FORBIDDEN_KEY_PARTS
        ):
            raise BotPersonalValidationError(
                "privacy_rejected", str(key), "sensitive or raw field is not accepted"
            )

    kind = _text(source.get("memory_type"), 80).lower()
    if kind not in BOT_PERSONAL_MEMORY_TYPES or kind not in TYPE_CONTRACTS:
        raise BotPersonalValidationError(
            "invalid", "memory_type", "unknown Bot Personal memory type"
        )
    if _text(source.get("memory_domain"), 80) not in ("", BOT_PERSONAL_MEMORY_DOMAIN):
        raise BotPersonalValidationError(
            "invalid", "memory_domain", "Bot Personal domain is fixed"
        )
    if _text(source.get("subject"), 80) not in ("", BOT_PERSONAL_SUBJECT):
        raise BotPersonalValidationError(
            "invalid", "subject", "Bot Personal subject is fixed"
        )

    key, requested_canonical_schema, owner_bot_id, persona_id, derived_record_id = (
        _archive_namespace(kind, source)
    )
    raw_payload = source.get("payload")
    if not isinstance(raw_payload, Mapping):
        raise BotPersonalValidationError(
            "invalid", "payload", "payload must be an object"
        )
    safe_payload = sanitize_bot_personal_value(raw_payload, path="payload")
    if _json_size(safe_payload) > BOT_PERSONAL_MAX_PAYLOAD_BYTES:
        raise BotPersonalValidationError(
            "payload_too_large", "payload", "payload exceeds the contract limit"
        )
    safe: dict[str, Any] = dict(safe_payload)
    # New producers may put canonical axes beside the legacy envelope while
    # older producers keep them in ``payload``.  Normalize both forms through
    # the same gate; top-level values take precedence.
    canonical_input_fields = (
        "evidence_kind",
        "canonical_evidence_level",
        "archive_evidence_level",
        "evidence_level_mapping",
        "authority_kind",
        "commitment_level",
        "epistemic_status",
        "content_granularity",
        "materialization_state",
        "fact_eligibility",
        "actor_type",
        "subject_actor_id",
        "object_actor_id",
        "source_actor_id",
        "target_user_id",
        "participant_roles",
        "runtime_origin_refs",
        "expires_at",
        "decision_trace",
        "schedule_ref",
        "source_refs_trusted",
        "trusted_source_refs",
        "authority_verified",
        "legacy_status",
        "legacy_source_kind",
    )
    for field in canonical_input_fields:
        if field not in source:
            continue
        safe[field] = sanitize_bot_personal_value(source[field], path=field)
    for field in ("source_kind", "status", "lifecycle_status", "evidence_level"):
        if field in source:
            safe[field] = _text(source[field], 96)

    (
        current,
        occurred,
        record_date,
        record_window,
        confidence,
        record_version,
        schema,
        refs,
    ) = _archive_time_and_references(key, now, safe, source)

    contract_source, contract_evidence, contract_status = TYPE_CONTRACTS[kind]
    # Actor binding is deliberately strict at the Bot Personal boundary.  A
    # user assertion may be archived by another domain, but it must not be
    # silently rewritten as Bot history.
    explicit_subject = _raw_field(source, safe, "subject_actor_id", "")
    subject_actor_id = _text(explicit_subject, 120) or BOT_PERSONAL_SUBJECT
    if subject_actor_id != BOT_PERSONAL_SUBJECT:
        raise BotPersonalValidationError(
            "invalid", "subject_actor_id", "Bot Personal subject actor is fixed"
        )
    safe["subject_actor_id"] = BOT_PERSONAL_SUBJECT
    actor_type = _canonical_text(
        _raw_field(source, safe, "actor_type", "bot"), frozenset({"bot"}), "bot"
    )
    source_actor_id = (
        _text(_raw_field(source, safe, "source_actor_id", "system"), 120) or "system"
    )
    object_actor_id = _text(_raw_field(source, safe, "object_actor_id", ""), 120)
    target_user_id = _text(_raw_field(source, safe, "target_user_id", ""), 120)
    participant_roles = _raw_field(source, safe, "participant_roles", [])
    if not isinstance(participant_roles, list):
        participant_roles = (
            list(participant_roles)
            if isinstance(participant_roles, (tuple, set))
            else []
        )
    participant_roles = participant_roles[:30]
    runtime_origin_refs = tuple(
        _string_refs(_raw_field(source, safe, "runtime_origin_refs", []))
    )
    expires_at = _text(_raw_field(source, safe, "expires_at", ""), 96)

    # Preserve legacy input for diagnostics, then overwrite executable axes by
    # memory type.  A model-supplied status/source/evidence level is never an
    # execution proof.
    trace: list[dict[str, Any]] = []
    raw_trace = _raw_field(source, safe, "decision_trace", [])
    if isinstance(raw_trace, (list, tuple)):
        for item in raw_trace[:50]:
            if isinstance(item, Mapping):
                trace.append(deepcopy(dict(item)))
            elif _text(item, 240):
                trace.append({"code": "legacy_trace", "message": _text(item, 240)})

    axes = _resolve_archive_evidence(
        contract_evidence=contract_evidence,
        contract_source=contract_source,
        contract_status=contract_status,
        current=current,
        expires_at=expires_at,
        key=key,
        kind=kind,
        refs=refs,
        safe=safe,
        safe_payload=safe_payload,
        source=source,
        trace=trace,
    )

    mapping = _canonical_mapping(axes.requested_canonical, axes.archive_evidence)
    safe.update(
        {
            "source_kind": axes.source_kind_value,
            "status": axes.status_value,
            "evidence_kind": axes.evidence_kind_value,
            "evidence_level": axes.archive_evidence,
            "canonical_evidence_level": axes.requested_canonical,
            "archive_evidence_level": axes.archive_evidence,
            "evidence_level_mapping": mapping,
            "authority_kind": axes.requested_authority,
            "commitment_level": axes.commitment_value,
            "epistemic_status": axes.epistemic_value,
            "content_granularity": axes.granularity_value,
            "materialization_state": axes.materialization_value,
            "fact_eligibility": axes.fact_value,
            "actor_type": actor_type,
            "subject_actor_id": subject_actor_id,
            "object_actor_id": object_actor_id,
            "source_actor_id": source_actor_id,
            "target_user_id": target_user_id,
            "participant_roles": deepcopy(participant_roles),
            "runtime_origin_refs": list(runtime_origin_refs),
            "expires_at": axes.expires_at,
            "decision_trace": deepcopy(trace),
            "canonical_schema_version": requested_canonical_schema,
            "source_refs": list(axes.refs),
        }
    )
    if _json_size(safe) > BOT_PERSONAL_MAX_PAYLOAD_BYTES:
        raise BotPersonalValidationError(
            "payload_too_large", "payload", "payload exceeds the contract limit"
        )
    return BotPersonalArchiveDTO(
        record_id=derived_record_id,
        memory_domain=BOT_PERSONAL_MEMORY_DOMAIN,
        memory_type=kind,
        subject=BOT_PERSONAL_SUBJECT,
        date=record_date,
        window=record_window,
        occurred_at=occurred,
        source_kind=axes.source_kind_value,
        source_refs=axes.refs,
        certainty=confidence,
        evidence_level=axes.archive_evidence,
        status=axes.status_value,
        version=record_version,
        idempotency_key=key,
        payload_schema_version=schema,
        payload=deepcopy(safe),
        evidence_kind=axes.evidence_kind_value,
        canonical_evidence_level=axes.requested_canonical,
        archive_evidence_level=axes.archive_evidence,
        evidence_level_mapping=deepcopy(mapping),
        authority_kind=axes.requested_authority,
        commitment_level=axes.commitment_value,
        epistemic_status=axes.epistemic_value,
        content_granularity=axes.granularity_value,
        materialization_state=axes.materialization_value,
        fact_eligibility=axes.fact_value,
        actor_type=actor_type,
        subject_actor_id=subject_actor_id,
        object_actor_id=object_actor_id,
        source_actor_id=source_actor_id,
        target_user_id=target_user_id,
        participant_roles=deepcopy(participant_roles),
        runtime_origin_refs=runtime_origin_refs,
        expires_at=axes.expires_at,
        decision_trace=tuple(deepcopy(trace)),
        owner_bot_id=owner_bot_id,
        persona_id=persona_id,
        canonical_schema_version=requested_canonical_schema,
    )


def _resolve_archive_evidence(
    *,
    contract_evidence,
    contract_source,
    contract_status,
    current,
    expires_at,
    key,
    kind,
    refs,
    safe,
    safe_payload,
    source,
    trace,
):
    """Resolve archive evidence."""
    requested_source_kind = _text(
        _raw_field(source, safe, "source_kind", ""), 40
    ).lower()
    requested_status = _text(_raw_field(source, safe, "status", ""), 40).lower()
    requested_evidence_kind = _canonical_text(
        _raw_field(source, safe, "evidence_kind", ""), EVIDENCE_KINDS, "none"
    )
    requested_authority = _canonical_text(
        _raw_field(source, safe, "authority_kind", ""),
        AUTHORITY_KINDS,
        "calendar" if kind == "bot_calendar_event" else "llm",
    )
    requested_commitment = _canonical_text(
        _raw_field(source, safe, "commitment_level", ""), COMMITMENT_LEVELS, "tentative"
    )
    requested_granularity = _canonical_text(
        _raw_field(source, safe, "content_granularity", ""),
        CONTENT_GRANULARITIES,
        "intent",
    )
    requested_materialization = _canonical_text(
        _raw_field(source, safe, "materialization_state", ""),
        MATERIALIZATION_STATES,
        "none",
    )
    requested_fact = _canonical_text(
        _raw_field(source, safe, "fact_eligibility", ""), FACT_ELIGIBILITIES, "none"
    )
    requested_canonical = _text(
        _raw_field(source, safe, "canonical_evidence_level", ""), 8
    ).upper()
    legacy_evidence_level = _text(
        _raw_field(source, safe, "evidence_level", ""), 8
    ).upper()
    # ``evidence_level`` is a legacy field.  Keep its historical default for
    # old archive producers unless they explicitly provide the canonical axis.
    if requested_canonical not in CANONICAL_EVIDENCE_LEVELS:
        # L4/L5 never fit the legacy archive column, so accepting them from a
        # newer producer is useful even when it has not yet sent the additive
        # canonical key.  Keep the historical L0-L3 default behavior intact.
        requested_canonical = (
            legacy_evidence_level
            if legacy_evidence_level in {"L4", "L5"}
            else contract_evidence
        )
    explicit_canonical = bool(
        source.get("canonical_evidence_level") not in (None, "")
        or safe_payload.get("canonical_evidence_level") not in (None, "")
        or legacy_evidence_level in {"L4", "L5"}
    )
    if kind == "bot_schedule_plan":
        requested_canonical = "L0"
    archive_evidence = (
        requested_canonical if requested_canonical in ARCHIVE_EVIDENCE_LEVELS else "L3"
    )
    if not explicit_canonical and kind != "bot_schedule_plan":
        archive_evidence = contract_evidence

    trusted_schedule = _has_trusted_schedule_ref(safe, requested_authority, current)
    commitment_value = requested_commitment
    if kind == "bot_schedule_plan":
        if requested_status and requested_status != "planned":
            safe["legacy_status"] = requested_status
        if requested_source_kind and requested_source_kind != "planned":
            safe["legacy_source_kind"] = requested_source_kind
        archive_ref = f"archive:{key}"
        if refs and not trusted_schedule and refs != (archive_ref,):
            safe["legacy_source_refs"] = list(refs)[:30]
            refs = (archive_ref,)
        source_kind_value = "planned"
        status_value = "planned"
        evidence_kind_value = "none"
        requested_canonical = archive_evidence = "L0"
        requested_authority = (
            requested_authority
            if trusted_schedule
            else ("routine" if requested_authority == "routine" else "llm")
        )
        commitment_value = (
            "confirmed"
            if trusted_schedule and requested_authority in EXTERNAL_AUTHORITIES
            else "routine"
            if requested_authority == "routine"
            else "tentative"
        )
        epistemic_value = "inferred"
        granularity_value = "intent"
        materialization_value = "none"
        fact_value = "schedule_commitment" if trusted_schedule else "none"
        if (
            requested_status
            and requested_status != "planned"
            and not any(
                item.get("code") == "archive.plan_status_ignored" for item in trace
            )
        ):
            trace.append(
                {"code": "archive.plan_status_ignored", "requested": requested_status}
            )
        if (
            requested_source_kind
            and requested_source_kind != "planned"
            and not any(
                item.get("code") == "archive.plan_source_kind_ignored" for item in trace
            )
        ):
            trace.append(
                {
                    "code": "archive.plan_source_kind_ignored",
                    "requested": requested_source_kind,
                }
            )
        if requested_evidence_kind != "none" and not any(
            item.get("code") == "archive.plan_evidence_ignored" for item in trace
        ):
            trace.append(
                {
                    "code": "archive.plan_evidence_ignored",
                    "requested": requested_evidence_kind,
                }
            )
        if (
            requested_commitment == "confirmed"
            and commitment_value != "confirmed"
            and not any(
                item.get("code") == "archive.plan_commitment_downgraded"
                for item in trace
            )
        ):
            trace.append(
                {"code": "archive.plan_commitment_downgraded", "requested": "confirmed"}
            )
        if not trusted_schedule and _text(
            _raw_field(source, safe, "authority_kind", ""), 48
        ):
            if not any(
                item.get("code") == "archive.untrusted_schedule_authority"
                for item in trace
            ):
                trace.append(
                    {
                        "code": "archive.untrusted_schedule_authority",
                        "requested": requested_authority,
                    }
                )
    elif kind == "bot_window_snapshot":
        source_kind_value, status_value = "projection", "reconciled"
        evidence_kind_value = "none"
        requested_canonical = archive_evidence = "L0"
        requested_authority, commitment_value = "state", "tentative"
        epistemic_value, granularity_value = "inferred", "intent"
        materialization_value, fact_value = "none", "none"
    elif kind == "bot_schedule_reconciliation":
        source_kind_value, status_value = "reconciled", "reconciled"
        compatible = (
            requested_evidence_kind in {"interaction", "tool_action", "external_record"}
            and requested_fact in {"current_observed", "history_observed"}
            and bool(
                _raw_field(source, safe, "source_refs_trusted", False)
                or _raw_field(source, safe, "authority_verified", False)
            )
            and bool(refs)
        )
        if not compatible:
            if requested_status and requested_status != "reconciled":
                safe["legacy_status"] = requested_status
            evidence_kind_value = "none"
            requested_canonical = archive_evidence = "L0"
            epistemic_value, fact_value = "inferred", "none"
            materialization_value = "none"
        else:
            evidence_kind_value = requested_evidence_kind
            epistemic_value, fact_value = "observed", requested_fact
            materialization_value = "active"
        granularity_value = requested_granularity
    elif kind == "bot_detail_fragment":
        source_kind_value, status_value = "detail", "planned"
        evidence_kind_value = "none"
        requested_canonical = archive_evidence = "L0"
        requested_authority, commitment_value = "llm", "tentative"
        epistemic_value, granularity_value = "inferred", "scene"
        materialization_value, fact_value = "candidate", "none"
        if not expires_at:
            expires_at = (current + timedelta(hours=2)).isoformat(timespec="seconds")
        flags = (
            safe.get("legacy_flags")
            if isinstance(safe.get("legacy_flags"), list)
            else []
        )
        flags = list(flags)
        for flag in ("short_ttl_candidate", "unverified_plan"):
            if flag not in flags:
                flags.append(flag)
        safe["legacy_flags"] = flags[:30]
    elif kind == "bot_calendar_event":
        source_kind_value, status_value = "calendar", "planned"
        evidence_kind_value = "external_commitment"
        requested_authority = "calendar"
        commitment_value = "confirmed" if trusted_schedule else "tentative"
        epistemic_value, granularity_value = "asserted", "commitment"
        materialization_value = "none"
        fact_value = "schedule_commitment" if trusted_schedule else "none"
    else:
        source_kind_value = contract_source
        status_value = (
            requested_status
            if kind == "bot_observed_activity"
            and requested_status
            in {"active", "completed", "partially_completed", "unknown", "cancelled"}
            else contract_status
        )
        evidence_kind_value = requested_evidence_kind
        if kind == "bot_observed_activity" and evidence_kind_value == "none":
            source_hint = _text(_raw_field(source, safe, "source", ""), 64).lower()
            evidence_kind_value = (
                "interaction"
                if source_hint in {"chat", "conversation", "interaction", "message"}
                else "external_record"
            )
        if evidence_kind_value in {"none", "external_commitment"}:
            fact_value = "none"
        elif evidence_kind_value == "self_state_commit":
            fact_value = (
                "current_internal" if requested_fact == "current_internal" else "none"
            )
        elif requested_fact in {"current_observed", "history_observed"} and refs:
            fact_value = requested_fact
        else:
            fact_value = "none"
        epistemic_value = "observed" if evidence_kind_value != "none" else "inferred"
        granularity_value = requested_granularity
        materialization_value = requested_materialization

    return ArchiveEvidence(
        requested_canonical=requested_canonical,
        archive_evidence=archive_evidence,
        requested_authority=requested_authority,
        commitment_value=commitment_value,
        source_kind_value=source_kind_value,
        status_value=status_value,
        evidence_kind_value=evidence_kind_value,
        epistemic_value=epistemic_value,
        granularity_value=granularity_value,
        materialization_value=materialization_value,
        fact_value=fact_value,
        refs=refs,
        expires_at=expires_at,
    )
