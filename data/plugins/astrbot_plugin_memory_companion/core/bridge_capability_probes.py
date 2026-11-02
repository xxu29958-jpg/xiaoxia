from __future__ import annotations

from typing import Any

from . import bot_personal_contract
from .capability_probe import (
    PROFILE_NAMES as C4_PROFILE_NAMES,
)
from .capability_probe import (
    build_capability_snapshot,
    detect_companion_plugin,
)
from .models import clean_text
from .namespace import build_namespace_context, validate_namespace_context
from .namespace_capability import namespace_capability_descriptor
from .scoped_store import ScopedStoreError


class MemoryCompanionBridgeCapabilityProbes:
    """Capability probes; shared runtime resources belong to the facade."""

    def probe_capability_snapshot(self, context: Any = None) -> dict[str, Any]:
        """Return the C4 capability snapshot without touching plugin state or storage.

        The probe is intentionally based only on the shared contract module. It
        must remain safe to call from ordinary chat paths even when the contract
        is stale or the local module is otherwise malformed.

        ``context`` is passed in rather than read from ``self._plugin`` on
        purpose: the capability probe must never touch plugin state -- callers
        use it before the service is safe to query, and C1 guarantees no storage
        access.  Reading ``self._plugin.context`` here broke that invariant
        (tests/test_c1_bridge_probe.py asserts it).  Callers that know the context
        hand it in; everyone else falls back to a module-alias probe, which is
        the right conservative answer when there is no host registry to ask.
        """
        if not self._active:
            return self._negative_personal_capability_probe("bridge_inactive")
        if self._capability_cache.snapshot().get("state") == "negative":
            return self.capability_status()
        try:
            descriptor = bot_personal_contract.capability_descriptor(
                available=True,
                read_only=False,
            )
        except Exception:
            return self._negative_personal_capability_probe(
                "contract_descriptor_exception"
            )

        if not isinstance(descriptor, dict):
            return self._negative_personal_capability_probe(
                "contract_descriptor_invalid"
            )

        result = dict(descriptor)
        try:
            problems = bot_personal_contract.contract_self_check()
        except Exception:
            return self._negative_personal_capability_probe(
                "contract_self_check_exception",
                base=result,
            )

        if not isinstance(problems, list):
            return self._negative_personal_capability_probe(
                "contract_self_check_invalid",
                base=result,
            )
        if problems:
            warnings = ["contract_self_check_failed"]
            known_codes = {
                "contract_fingerprint_stale",
                "duplicate_window_slug",
                "type_contracts_out_of_sync",
                "window_coverage_gap",
                "alias_points_to_unknown_window",
            }
            for problem in problems:
                code = str(problem).split(":", 1)[0]
                if code in known_codes and code not in warnings:
                    warnings.append(code)
            return self._negative_personal_capability_probe(
                "contract_self_check_failed",
                base=result,
                warnings=warnings,
            )

        result["available"] = True
        result["state"] = "available"
        result["degraded"] = False
        self._add_personal_capability_contract_aliases(result)
        # ``available`` 到此为止只代表**记忆侧这份 contract 自检通过**，也就是
        # 「我准备好了、可以跟陪伴侧对话」。它不代表陪伴插件装了——过去面板正是
        # 把这两件事当成一件，才会在没装陪伴插件时显示「已连接」。
        # 「装没装」必须真去问运行时，所以单独探测、单独两个字段。
        companion = detect_companion_plugin(context)
        result["companion_installed"] = bool(companion["companion_installed"])
        result["companion_plugin_name"] = str(companion["companion_plugin_name"])
        c4_snapshot = build_capability_snapshot(
            available=True,
            state="available",
            contract_module=bot_personal_contract,
            methods=result.get("methods", []),
            profiles=C4_PROFILE_NAMES,
            warnings=result.get("warnings", []),
            companion_installed=result["companion_installed"],
            companion_plugin_name=result["companion_plugin_name"],
        )
        result.update(c4_snapshot)
        result["memory_domain"] = bot_personal_contract.BOT_PERSONAL_MEMORY_DOMAIN
        result["domain"] = bot_personal_contract.BOT_PERSONAL_MEMORY_DOMAIN
        result["contract_revision"] = bot_personal_contract.CONTRACT_REVISION
        result["capability_schema_version"] = (
            bot_personal_contract.BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION
        )
        result["payload_schema_version"] = (
            bot_personal_contract.BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
        )
        result["capability_state"] = "available"
        # Capability discovery must remain static: callers use it before the
        # plugin service is safe to query, and C1 guarantees no storage access.
        result["p5"] = {"state": "unprobed", "error_code": "p5_status_not_probed"}
        self._capability_cache.mark_available(c4_snapshot)
        result.setdefault("warnings", [])
        return result

    def probe_bot_personal_memory_capabilities(
        self, context: Any = None
    ) -> dict[str, Any]:
        """Backward-compatible C1 probe; C4 state is exposed as capability_state."""

        result = dict(self.probe_capability_snapshot(context))
        if result.get("capability_state") == "available":
            result["state"] = "ready"
        result["legacy_state"] = result.get("state", "degraded")
        return result

    def probe_namespace_context_capabilities(self) -> dict[str, Any]:
        """Advertise ready only after the store and active epoch are bound."""
        status = (
            self._scoped_store.epoch_status()
            if self._scoped_store is not None
            else {"bound": False}
        )
        ready = self._active and status.get("bound") is True
        return namespace_capability_descriptor(
            available=ready,
            methods=(
                "list_scoped_records",
                "read_scoped_record",
                "erase_scoped_group_scopes",
                "erase_scoped_persona_scopes",
                "tombstone_scoped_identity_scopes",
                "tombstone_scoped_namespace",
                "tombstone_scoped_record",
                "upsert_scoped_record",
            )
            if ready
            else (),
            error_code=(
                ""
                if ready
                else "bridge_inactive"
                if not self._active
                else "namespace_scoped_api_not_bound"
            ),
        )

    def bind_namespace_migration_epoch(
        self,
        capability: Any,
        *,
        operation_id: str,
        expected_previous_epoch: str,
        migration_epoch: str,
        policy_version: str,
    ) -> dict[str, Any]:
        if not self._active:
            return {"ok": False, "state": "degraded", "code": "bridge_inactive"}
        if not self._is_valid_private_companion_capability(capability):
            return {
                "ok": False,
                "state": "forbidden",
                "code": "producer_capability_required",
            }
        if self._scoped_store is None:
            return {
                "ok": False,
                "state": "degraded",
                "code": "namespace_scoped_store_unavailable",
            }
        try:
            result = self._scoped_store.bind_epoch(
                operation_id=operation_id,
                expected_previous_epoch=expected_previous_epoch,
                migration_epoch=migration_epoch,
                policy_version=policy_version,
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {
            "ok": True,
            "state": "ready",
            "code": result,
            "epoch": self._scoped_store.epoch_status(),
        }

    def _authorized_scoped_context(
        self, capability: Any, namespace: Any
    ) -> tuple[Any | None, dict[str, Any] | None]:
        if not self._active:
            return None, {"ok": False, "state": "degraded", "code": "bridge_inactive"}
        if not self._is_valid_private_companion_capability(capability):
            return None, {
                "ok": False,
                "state": "forbidden",
                "code": "producer_capability_required",
            }
        if self._scoped_store is None:
            return None, {
                "ok": False,
                "state": "degraded",
                "code": "namespace_scoped_store_unavailable",
            }
        if self._scoped_store.epoch_status().get("bound") is not True:
            return None, {
                "ok": False,
                "state": "degraded",
                "code": "namespace_scoped_api_not_bound",
            }
        errors = validate_namespace_context(namespace)
        if errors:
            return None, {"ok": False, "state": "rejected", "code": errors[0]}
        context = build_namespace_context(namespace)
        if context is None:
            return None, {
                "ok": False,
                "state": "rejected",
                "code": "namespace_context_invalid",
            }
        return context, None

    def upsert_scoped_record(
        self,
        capability: Any,
        namespace: Any,
        *,
        record_kind: str,
        record_id: str,
        revision: int,
        payload: dict[str, Any],
        event_id: str,
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            result = self._scoped_store.upsert(
                context,
                record_kind=record_kind,
                record_id=record_id,
                revision=revision,
                payload=payload,
                event_id=event_id,
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {"ok": True, "state": "ready", "code": result}

    def read_scoped_record(
        self, capability: Any, namespace: Any, *, record_kind: str, record_id: str
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            record = self._scoped_store.read(
                context, record_kind=record_kind, record_id=record_id
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {
            "ok": True,
            "state": "ready",
            "code": "found" if record is not None else "not_found",
            "record": record,
        }

    def list_scoped_records(
        self, capability: Any, namespace: Any, *, record_kind: str, limit: int = 100
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            records = self._scoped_store.list_records(
                context, record_kind=record_kind, limit=limit
            )
        except (ScopedStoreError, TypeError, ValueError, OverflowError) as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {"ok": True, "state": "ready", "code": "listed", "records": records}

    def tombstone_scoped_record(
        self,
        capability: Any,
        namespace: Any,
        *,
        record_kind: str,
        record_id: str,
        revision: int,
        event_id: str,
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            result = self._scoped_store.tombstone(
                context,
                record_kind=record_kind,
                record_id=record_id,
                revision=revision,
                event_id=event_id,
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {"ok": True, "state": "ready", "code": result}

    def tombstone_scoped_namespace(
        self,
        capability: Any,
        namespace: Any,
        *,
        operation_id: str,
        reason_code: str,
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            result = self._scoped_store.tombstone_namespace(
                context,
                operation_id=operation_id,
                reason_code=reason_code,
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {"ok": True, "state": "ready", **result}

    def tombstone_scoped_identity_scopes(
        self,
        capability: Any,
        namespace: Any,
        *,
        operation_id: str,
        reason_code: str,
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            result = self._scoped_store.tombstone_identity_scopes(
                context,
                operation_id=operation_id,
                reason_code=reason_code,
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {"ok": True, "state": "ready", **result}

    def erase_scoped_group_scopes(
        self,
        capability: Any,
        namespace: Any,
        *,
        operation_id: str,
        reason_code: str = "group_reset",
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            result = self._scoped_store.erase_group_scopes(
                context,
                operation_id=operation_id,
                reason_code=reason_code,
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {"ok": True, "state": "ready", **result}

    def erase_scoped_persona_scopes(
        self,
        capability: Any,
        namespace: Any,
        *,
        operation_id: str,
        reason_code: str = "persona_reset",
    ) -> dict[str, Any]:
        context, denied = self._authorized_scoped_context(capability, namespace)
        if denied is not None:
            return denied
        try:
            result = self._scoped_store.erase_persona_scopes(
                context,
                operation_id=operation_id,
                reason_code=reason_code,
            )
        except ScopedStoreError as exc:
            return {"ok": False, "state": "rejected", "code": str(exc)[:120]}
        return {"ok": True, "state": "ready", **result}

    def capability_status(self) -> dict[str, Any]:
        """Return the bounded C4 cache state without probing storage."""

        snapshot = self._capability_cache.snapshot()
        snapshot["read_only"] = False
        snapshot["contract_name"] = bot_personal_contract.CONTRACT_NAME
        snapshot["max_payload_bytes"] = (
            bot_personal_contract.BOT_PERSONAL_MAX_PAYLOAD_BYTES
        )
        snapshot["memory_domain"] = bot_personal_contract.BOT_PERSONAL_MEMORY_DOMAIN
        snapshot["domain"] = snapshot["memory_domain"]
        snapshot["contract_revision"] = bot_personal_contract.CONTRACT_REVISION
        snapshot["capability_schema_version"] = (
            bot_personal_contract.BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION
        )
        snapshot["payload_schema_version"] = (
            bot_personal_contract.BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
        )
        snapshot["capability_state"] = snapshot.get("state", "unprobed")
        return snapshot

    def mark_capability_negative(self, reason: str) -> dict[str, Any]:
        """Temporarily suppress repeated capability failures at the bridge edge."""

        self._capability_cache.mark_negative(
            clean_text(reason, 120) or "capability_negative"
        )
        return self.capability_status()

    @staticmethod
    def _add_personal_capability_contract_aliases(
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Expose stable C1 aliases without changing the shared contract copy."""

        result.setdefault("domain", result.get("memory_domain", ""))
        result.setdefault("domains", [result.get("memory_domain", "")])
        result.setdefault("profiles", list(C4_PROFILE_NAMES))
        result.setdefault("legacy_profiles", ["bot_personal_archive"])
        result.setdefault(
            "methods",
            [
                "record_event",
                "record_external_memory",
                "record_visible_turn",
                "register_private_companion",
                "register_bot_personal_producer",
                "create_user_memory_context",
                "record_bot_personal_archive",
                "record_bot_personal_memory",
                "read_bot_personal_profile",
                "search_bot_personal_profile",
                "search",
                "compose_injection",
                "compose_context",
                "remember",
                "recall",
                "correct_user_memory",
                "check_memory_dependencies",
                "consume_person_projection",
                "consume_context_projection",
                "consume_relationship_projection",
                "consume_expression_decision",
                "read_bot_profile",
                "read_profile",
                "read_unified_profile_portrait",
                "unified_profile_portrait_status",
                "run_unified_profile_portrait_batch",
                "p5_capability_status",
                "provenance_snapshot",
                "provenance_preview",
                "provenance_apply",
                "provenance_backup",
                "provenance_rollback",
                "probe_capability_snapshot",
                "probe_bot_personal_memory_capabilities",
            ],
        )
        result.setdefault("contract_version", str(result.get("contract_revision", "")))
        result.setdefault(
            "schema_version", str(result.get("capability_schema_version", ""))
        )
        return result

    def _negative_personal_capability_probe(
        self,
        reason: str,
        *,
        base: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a bounded failure and suppress repeated probes for the TTL."""

        safe_reason = clean_text(reason, 120) or "capability_negative"
        result = self._degraded_personal_capability_probe(
            safe_reason,
            base=base,
            warnings=warnings,
        )
        cached = self._capability_cache.mark_negative(safe_reason)
        for key in ("available", "state", "degraded", "pending", "error_code"):
            if key in cached:
                result[key] = cached[key]
        result["available"] = False
        result["state"] = "negative"
        result["capability_state"] = "negative"
        result["degraded"] = True
        result["pending"] = False
        result["error_code"] = safe_reason
        result["p5"] = {"state": "degraded", "error_code": safe_reason}
        return result

    @staticmethod
    def _degraded_personal_capability_probe(
        reason: str,
        *,
        base: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        result = {
            "available": False,
            "read_only": False,
            "memory_domain": getattr(
                bot_personal_contract, "BOT_PERSONAL_MEMORY_DOMAIN", ""
            ),
            "contract_name": getattr(bot_personal_contract, "CONTRACT_NAME", ""),
            "contract_revision": getattr(bot_personal_contract, "CONTRACT_REVISION", 0),
            "contract_fingerprint": getattr(
                bot_personal_contract, "CONTRACT_FINGERPRINT", ""
            ),
            "capability_schema_version": getattr(
                bot_personal_contract, "BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION", ""
            ),
            "payload_schema_version": getattr(
                bot_personal_contract, "BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION", ""
            ),
            "windows": list(getattr(bot_personal_contract, "WINDOW_SLUGS", ())),
            "memory_types": list(
                getattr(bot_personal_contract, "BOT_PERSONAL_MEMORY_TYPES", ())
            ),
            "max_payload_bytes": getattr(
                bot_personal_contract, "BOT_PERSONAL_MAX_PAYLOAD_BYTES", 0
            ),
            "warnings": [],
        }
        result.update(base or {})
        result.update(
            {
                "available": False,
                "state": "degraded",
                "degraded": True,
                "warnings": list(warnings or [reason]),
            }
        )
        MemoryCompanionBridgeCapabilityProbes._add_personal_capability_contract_aliases(
            result
        )
        c4_snapshot = build_capability_snapshot(
            available=False,
            state="degraded",
            contract_module=bot_personal_contract,
            methods=result.get("methods", []),
            profiles=C4_PROFILE_NAMES,
            warnings=result.get("warnings", []),
            error_code=reason,
        )
        result.update(c4_snapshot)
        result["memory_domain"] = getattr(
            bot_personal_contract, "BOT_PERSONAL_MEMORY_DOMAIN", ""
        )
        result["domain"] = result["memory_domain"]
        result["contract_revision"] = getattr(
            bot_personal_contract, "CONTRACT_REVISION", 0
        )
        result["capability_schema_version"] = getattr(
            bot_personal_contract, "BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION", ""
        )
        result["payload_schema_version"] = getattr(
            bot_personal_contract, "BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION", ""
        )
        result["capability_state"] = "degraded"
        result["p5"] = {"state": "degraded", "error_code": reason}
        return result
