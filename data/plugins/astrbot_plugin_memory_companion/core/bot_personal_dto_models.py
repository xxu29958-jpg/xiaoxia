from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .bot_personal_contract import (
    BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
    BOT_PERSONAL_SUBJECT,
)
from .bot_personal_dto_common import CANONICAL_SCHEMA_VERSION
from .bot_personal_dto_validation import _fingerprint


@dataclass(frozen=True)
class BotPersonalArchiveDTO:
    record_id: str
    memory_domain: str
    memory_type: str
    subject: str
    date: str
    window: str
    occurred_at: str
    source_kind: str
    source_refs: tuple[str, ...]
    certainty: float
    evidence_level: str
    status: str
    version: int
    idempotency_key: str
    payload_schema_version: str
    payload: dict[str, Any]
    # Canonical C3 agenda axes.  Defaults keep old positional construction and
    # old consumers valid while new readers can inspect the normalized view.
    evidence_kind: str = "none"
    canonical_evidence_level: str = "L0"
    archive_evidence_level: str = "L0"
    evidence_level_mapping: dict[str, Any] | None = None
    authority_kind: str = "llm"
    commitment_level: str = "tentative"
    epistemic_status: str = "inferred"
    content_granularity: str = "intent"
    materialization_state: str = "none"
    fact_eligibility: str = "none"
    actor_type: str = "bot"
    subject_actor_id: str = BOT_PERSONAL_SUBJECT
    object_actor_id: str = ""
    source_actor_id: str = "system"
    target_user_id: str = ""
    participant_roles: list[Any] | None = None
    runtime_origin_refs: tuple[str, ...] = ()
    expires_at: str = ""
    decision_trace: tuple[dict[str, Any], ...] = ()
    owner_bot_id: str = ""
    persona_id: str = ""
    canonical_schema_version: int = CANONICAL_SCHEMA_VERSION

    def envelope(self) -> dict[str, Any]:
        result = {
            "record_id": self.record_id,
            "memory_domain": self.memory_domain,
            "memory_type": self.memory_type,
            "subject": self.subject,
            "date": self.date,
            "window": self.window,
            "occurred_at": self.occurred_at,
            "source_kind": self.source_kind,
            "source_refs": list(self.source_refs),
            "certainty": self.certainty,
            "evidence_level": self.evidence_level,
            "status": self.status,
            "version": self.version,
            "idempotency_key": self.idempotency_key,
            "payload_schema_version": self.payload_schema_version,
            "payload": deepcopy(self.payload),
            "evidence_kind": self.evidence_kind,
            "canonical_evidence_level": self.canonical_evidence_level,
            "archive_evidence_level": self.archive_evidence_level,
            "evidence_level_mapping": deepcopy(self.evidence_level_mapping or {}),
            "authority_kind": self.authority_kind,
            "commitment_level": self.commitment_level,
            "epistemic_status": self.epistemic_status,
            "content_granularity": self.content_granularity,
            "materialization_state": self.materialization_state,
            "fact_eligibility": self.fact_eligibility,
            "actor_type": self.actor_type,
            "subject_actor_id": self.subject_actor_id,
            "object_actor_id": self.object_actor_id,
            "source_actor_id": self.source_actor_id,
            "target_user_id": self.target_user_id,
            "participant_roles": deepcopy(self.participant_roles or []),
            "runtime_origin_refs": list(self.runtime_origin_refs),
            "expires_at": self.expires_at,
            "decision_trace": deepcopy(list(self.decision_trace)),
            "canonical_schema_version": self.canonical_schema_version,
        }
        if self.canonical_schema_version >= BOT_PERSONAL_CANONICAL_SCHEMA_VERSION:
            result["owner_bot_id"] = self.owner_bot_id
            result["persona_id"] = self.persona_id
        return result

    to_dict = envelope


def bot_personal_payload_fingerprint(dto: BotPersonalArchiveDTO) -> str:
    payload = dto.envelope()
    payload.pop("record_id", None)
    return _fingerprint(payload)
