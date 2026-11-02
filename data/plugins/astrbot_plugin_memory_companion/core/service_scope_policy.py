from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    MemoryRecord,
    SessionContext,
    clean_text,
)
from .provenance import observed_from_companion_snapshot
from .provenance_store import ProvenanceLedger
from .service_support import (
    _RULE_PROFILE_MEMORY_TYPES,
)
from .store import MemoryStore


class MemoryCompanionServiceScopePolicy:
    """service scope policy; the facade owns all shared resources."""

    def _p5_gate_enabled(self, sink: str) -> bool:
        key = (
            "private_companion_bridge.enable_p5_b1_recall_gate"
            if sink == "memory_recall"
            else "private_companion_bridge.enable_p5_b1_bridge_gate"
        )
        return self.config.bool(key, False)

    def _scope_feature_enabled(
        self, scope_or_ctx: str | SessionContext, feature: str
    ) -> bool:
        window_id = ""
        scope = (
            scope_or_ctx.scope
            if isinstance(scope_or_ctx, SessionContext)
            else clean_text(scope_or_ctx, 40)
        )
        if isinstance(scope_or_ctx, SessionContext):
            window_id = clean_text(scope_or_ctx.current_target_id, 160)
        scope = clean_text(scope, 40).lower()
        feature = clean_text(feature, 40).lower()
        if (
            scope not in {"private", "group"}
            or feature not in self._SCOPE_CONTROL_FEATURES
        ):
            return True
        if window_id and feature in {"capture", "recall"}:
            getter = getattr(self.store, "get_scope_feature_override_sync", None)
            if callable(getter):
                try:
                    override = getter(scope, window_id)
                except Exception:
                    override = {}
                value = (
                    override.get(f"{feature}_enabled")
                    if isinstance(override, dict)
                    else None
                )
                if value is not None:
                    return bool(value)
        config = getattr(self, "config", None)
        config_bool = getattr(config, "bool", None)
        if not callable(config_bool):
            return True
        return bool(config_bool(f"scope_control.{scope}_{feature}_enabled", True))

    async def _p5_gate(
        self,
        *,
        event: Any = None,
        sink: str,
        attestation: Any = None,
        consumer: Any = None,
    ) -> dict[str, Any]:
        """Consume an opaque Companion attestation when a P5 gate is enabled."""

        if not self._p5_gate_enabled(sink):
            if attestation is not None and callable(consumer):
                try:
                    consumed = consumer(attestation, sink)
                    if inspect.isawaitable(consumed):
                        await consumed
                except Exception:
                    pass
            result = {"ok": True, "state": "legacy", "legacy": True, "enabled": False}
            self._last_p5_gate_status = result
            return result
        if attestation is None and event is not None:
            issuer = getattr(event, "private_companion_p5_issue_attestation", None)
            if callable(issuer):
                try:
                    issued = issuer(sink)
                except Exception:
                    issued = None
                if isinstance(issued, tuple) and len(issued) == 2:
                    attestation, consumer = issued
        if attestation is None or not callable(consumer):
            result = {
                "ok": False,
                "state": "degraded",
                "enabled": True,
                "error_code": "p5_attestation_required",
                "warnings": ["legacy_call_not_attested"],
            }
            self._last_p5_gate_status = result
            return result
        try:
            snapshot = consumer(attestation, sink)
            if inspect.isawaitable(snapshot):
                snapshot = await snapshot
        except Exception:
            snapshot = None
        snapshot_record = observed_from_companion_snapshot("p5_gate", snapshot)
        snapshot_sink = getattr(snapshot, "sink", None)
        if snapshot_sink is None and isinstance(snapshot, Mapping):
            snapshot_sink = snapshot.get("sink")
        if (
            snapshot_record.get("provenance_state") != "observed"
            or snapshot_sink != sink
        ):
            result = {
                "ok": False,
                "state": "denied",
                "enabled": True,
                "error_code": "p5_attestation_invalid",
                "warnings": ["invalid_or_replayed_attestation"],
            }
            self._last_p5_gate_status = result
            return result
        disposition = str(getattr(snapshot, "disposition", "") or "")
        trust = str(getattr(snapshot, "source_trust", "") or "")
        if isinstance(snapshot, Mapping):
            disposition = str(snapshot.get("disposition") or disposition)
            trust = str(snapshot.get("source_trust") or snapshot.get("trust") or trust)
        if disposition == "deny_high_risk":
            state, error_code, allowed = "denied", "p5_high_risk_denied", False
        elif disposition != "allow" or trust in {"T3", "T4"}:
            state, error_code, allowed = "shadow", "p5_source_shadowed", False
        else:
            state, error_code, allowed = "allowed", "", True
        result = {
            "ok": allowed,
            "state": state,
            "enabled": True,
            "snapshot": snapshot,
            "error_code": error_code or None,
            "warnings": [] if allowed else [error_code],
        }
        self._last_p5_gate_status = {
            key: value for key, value in result.items() if key != "snapshot"
        }
        return result

    def p5_capability_status(self) -> dict[str, Any]:
        ledger = getattr(self, "provenance_ledger", None)
        snapshot = (
            ledger.snapshot()
            if isinstance(ledger, ProvenanceLedger)
            else {
                "records": {},
                "operation_count": 0,
                "observation_count": 0,
                "path_present": False,
            }
        )
        return {
            "schema_version": "ops.p5.provenance.v1",
            "recall_gate": self._p5_gate_enabled("memory_recall"),
            "bridge_gate": self._p5_gate_enabled("bridge_serialization"),
            "last_gate_state": str(
                getattr(self, "_last_p5_gate_status", {}).get("state") or "disabled"
            ),
            "provenance_records": len(snapshot.get("records", {})),
            "provenance_operations": int(snapshot.get("operation_count", 0) or 0),
            "provenance_observations": int(snapshot.get("observation_count", 0) or 0),
            "provenance_file_present": bool(snapshot.get("path_present", False)),
        }

    async def _p5_record_observed(
        self, memory_ids: list[str], snapshot: Any
    ) -> dict[str, Any]:
        ledger = getattr(self, "provenance_ledger", None)
        if not isinstance(ledger, ProvenanceLedger):
            return {
                "ok": False,
                "state": "degraded",
                "error_code": "provenance_ledger_unavailable",
            }
        return await asyncio.to_thread(ledger.record_observed, memory_ids, snapshot)

    def provenance_snapshot(self) -> dict[str, Any]:
        ledger = getattr(self, "provenance_ledger", None)
        return (
            ledger.snapshot()
            if isinstance(ledger, ProvenanceLedger)
            else {"records": {}, "operation_count": 0}
        )

    def provenance_preview(
        self, candidates: list[Mapping[str, Any]], *, operation_ref_hash: str
    ) -> dict[str, Any]:
        ledger = getattr(self, "provenance_ledger", None)
        if not isinstance(ledger, ProvenanceLedger):
            return {
                "mode": "preview",
                "readonly": True,
                "write_count": 0,
                "error_codes": ["ledger_unavailable"],
            }
        return ledger.preview_legacy(candidates, operation_ref_hash=operation_ref_hash)

    async def provenance_backup(self) -> dict[str, Any]:
        ledger = getattr(self, "provenance_ledger", None)
        if not isinstance(ledger, ProvenanceLedger):
            return {
                "ok": False,
                "state": "degraded",
                "error_code": "ledger_unavailable",
            }
        return await asyncio.to_thread(ledger.backup)

    async def provenance_apply(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        ledger = getattr(self, "provenance_ledger", None)
        if not isinstance(ledger, ProvenanceLedger):
            return {
                "ok": False,
                "state": "degraded",
                "error_code": "ledger_unavailable",
            }
        return await asyncio.to_thread(ledger.apply, operation)

    async def provenance_rollback(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        ledger = getattr(self, "provenance_ledger", None)
        if not isinstance(ledger, ProvenanceLedger):
            return {
                "ok": False,
                "state": "degraded",
                "error_code": "ledger_unavailable",
            }
        return await asyncio.to_thread(ledger.rollback, operation)

    @staticmethod
    def _prepare_rule_profile_write(record: MemoryRecord) -> tuple[str, str]:
        """Return (write mode, reason) and normalize rule profile state in place."""
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        extractor = clean_text(metadata.get("extractor"), 40).lower()
        if (
            record.memory_type not in _RULE_PROFILE_MEMORY_TYPES
            or not extractor.startswith("rule_")
        ):
            return "generic", "not_rule_profile"

        rejection_reason = clean_text(
            metadata.get("rejection_reason")
            or metadata.get("profile_rejection_reason")
            or metadata.get("quality_rejection_reason"),
            120,
        )
        if rejection_reason or metadata.get("quality_gate_passed") is False:
            return "reject", rejection_reason or "profile_quality_rejected"

        dimension = clean_text(metadata.get("profile_dimension"), 80).lower()
        value = clean_text(metadata.get("profile_value"), 240)
        normalized = clean_text(metadata.get("normalized_value"), 240)
        try:
            quality_score = float(metadata.get("extraction_quality_score"))
        except (TypeError, ValueError):
            quality_score = -1.0
        if (
            not dimension
            or not value
            or not normalized
            or not 0.0 <= quality_score <= 1.0
        ):
            return "reject", "profile_candidate_metadata_missing"
        if extractor not in MemoryStore.PROFILE_RULE_EXTRACTORS:
            return "reject", "profile_extractor_unsupported"

        extraction_quality = clean_text(metadata.get("extraction_quality"), 40).lower()
        evidence_strength = clean_text(metadata.get("evidence_strength"), 40).lower()
        requested_state = clean_text(
            metadata.get("profile_state") or metadata.get("profile_status"),
            32,
        ).lower()
        explicit_active = bool(
            requested_state == "active"
            and extraction_quality == "explicit"
            and evidence_strength == "direct_statement"
            and quality_score >= 0.8
        )
        next_state = "active" if explicit_active else "candidate"
        try:
            configured_evidence_count = int(
                metadata.get("required_evidence_count") or 2
            )
        except (TypeError, ValueError):
            configured_evidence_count = 2
        metadata["extractor"] = extractor
        metadata["profile_dimension"] = dimension
        metadata["profile_value"] = value
        metadata["normalized_value"] = normalized
        metadata["profile_state"] = next_state
        metadata["profile_status"] = next_state
        metadata["quality_gate_passed"] = True
        metadata["required_evidence_count"] = (
            1 if explicit_active else max(2, min(10, configured_evidence_count))
        )
        record.metadata = metadata
        record.confidence = quality_score
        record.lifecycle = "stable_memory" if explicit_active else "raw_event"
        record.review_status = "auto" if explicit_active else "pending"
        if not explicit_active:
            record.importance = min(float(record.importance or 0.3), 0.55)
        return "profile", next_state

    @staticmethod
    def _log_profile_write_gate(
        record: MemoryRecord, *, decision: str, reason: str
    ) -> None:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        logger.info(
            "[MemoryCompanion] 画像写入门禁: decision=%s reason=%s type=%s dimension=%s "
            "subject=%s source=%s",
            clean_text(decision, 40),
            clean_text(reason, 120),
            clean_text(record.memory_type, 80),
            clean_text(metadata.get("profile_dimension"), 80),
            clean_text(record.subject.id, 120),
            clean_text(metadata.get("source_memory_id"), 160),
        )
