from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    json_loads,
    stable_fingerprint,
    utc_now,
)
from .portrait import cross_scene_whitelisted_fact
from .sensitive_data import redact_sensitive_value


class MemoryStorePortraitFacts:
    """Persist portrait facts and their evidence. Shared resources stay with the root owner."""

    async def add_portrait_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._add_portrait_evidence_sync, deepcopy(evidence)
        )

    def _add_portrait_evidence_sync(self, evidence: dict[str, Any]) -> dict[str, Any]:
        person_id = clean_text(evidence.get("person_id"), 80)
        evidence_key = clean_text(evidence.get("evidence_hash"), 80)
        if not person_id or not re.fullmatch(r"[0-9a-f]{64}", evidence_key):
            return {"ok": False, "code": "portrait_evidence_invalid", "created": False}
        statement_key = clean_text(evidence.get("statement_fingerprint"), 80)
        if not re.fullmatch(r"[0-9a-f]{64}", statement_key):
            statement_key = evidence_key
        context_refs = (
            evidence.get("context_refs")
            if isinstance(evidence.get("context_refs"), list)
            else []
        )
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO portrait_evidence(
                    evidence_hash, person_id, origin_identity_key, scope, session_id, message_id, statement_fingerprint, context_refs, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    evidence_key,
                    person_id,
                    clean_text(evidence.get("origin_identity_key"), 96),
                    clean_text(evidence.get("scope"), 80),
                    clean_text(evidence.get("session_id"), 200),
                    clean_text(evidence.get("message_id"), 120),
                    statement_key,
                    json_dumps(
                        [
                            clean_text(item, 160)
                            for item in context_refs
                            if clean_text(item, 160)
                        ][:8]
                    ),
                    now,
                ),
            )
            self._conn.commit()
        return {
            "ok": True,
            "code": "portrait_evidence_recorded",
            "created": bool(cur.rowcount),
        }

    async def upsert_portrait_fact(self, fact: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._upsert_portrait_fact_sync, deepcopy(fact))

    def _upsert_portrait_fact_sync(self, fact: dict[str, Any]) -> dict[str, Any]:
        fact = redact_sensitive_value(fact)
        person_id = clean_text(fact.get("person_id"), 80)
        dimension = clean_text(fact.get("dimension"), 80)
        claim_hash = clean_text(fact.get("normalized_claim_hash"), 80)
        tier = clean_text(fact.get("portrait_tier"), 24)
        source_scope = clean_text(fact.get("source_scope"), 80)
        if (
            not person_id
            or not dimension
            or not re.fullmatch(r"[0-9a-f]{64}", claim_hash)
            or tier not in {"base", "intelligent"}
        ):
            return {"ok": False, "code": "portrait_fact_invalid", "created": False}
        if not source_scope:
            source_scope = "private"
        now = utc_now()
        evidence_hashes = [
            clean_text(item, 80)
            for item in (fact.get("evidence_hashes") or [])
            if re.fullmatch(r"[0-9a-f]{64}", clean_text(item, 80))
        ][:16]
        status = clean_text(fact.get("status"), 40) or "active"
        cardinality = clean_text(fact.get("profile_cardinality"), 20).lower()
        single_value = (
            cardinality == "single" or dimension in self.PROFILE_SINGLE_VALUE_DIMENSIONS
        )
        with self._lock:
            with self._transaction_sync():
                if self._portrait_suppressed_sync(
                    person_id, dimension, claim_hash, source_scope
                ):
                    return {
                        "ok": False,
                        "code": "portrait_suppressed",
                        "created": False,
                    }
                previous = self._conn.execute(
                    """
                    SELECT * FROM portrait_facts WHERE person_id=? AND dimension=?
                      AND normalized_claim_hash=? AND portrait_tier=? AND source_scope=?
                    """,
                    (person_id, dimension, claim_hash, tier, source_scope),
                ).fetchone()
                previous_hashes = (
                    json_loads(previous["evidence_hashes"], [])
                    if previous is not None
                    else []
                )
                merged_hashes = list(
                    dict.fromkeys(
                        [
                            clean_text(item, 80)
                            for item in previous_hashes + evidence_hashes
                            if clean_text(item, 80)
                        ]
                    )
                )[:16]
                fact_id = (
                    clean_text(previous["id"], 120)
                    if previous is not None
                    else f"portrait_{stable_fingerprint(person_id, dimension, claim_hash, tier, source_scope)[:24]}"
                )
                revision = (
                    int(previous["revision"] or 0) + 1 if previous is not None else 1
                )
                first_at = (
                    clean_text(previous["first_evidence_at"], 80)
                    if previous is not None
                    else now
                )
                sensitivity = clean_text(fact.get("sensitivity"), 24)
                if sensitivity not in {"low", "sensitive", "high"}:
                    sensitivity = "high"
                usable_scope = clean_text(fact.get("usable_scope"), 80)
                if usable_scope == "self_low_global" and cross_scene_whitelisted_fact(
                    dimension=dimension,
                    claim_summary=fact.get("claim_summary"),
                    sensitivity=sensitivity,
                    source_scope=source_scope,
                ):
                    usable_scope = "self_low_global"
                else:
                    usable_scope = "source_only"
                self._conn.execute(
                    """
                    INSERT INTO portrait_facts(
                        id, person_id, dimension, normalized_claim_hash, claim_summary, portrait_tier,
                        producer_kind, producer_version, derivation_kind, epistemic_status, source_scope,
                        usable_scope, confidence, sensitivity, status, evidence_hashes, context_refs,
                        first_evidence_at, last_evidence_at, expires_at, supersedes_id, revision,
                        operation_id, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(person_id, dimension, normalized_claim_hash, portrait_tier, source_scope) DO UPDATE SET
                        claim_summary=excluded.claim_summary,
                        producer_kind=excluded.producer_kind,
                        producer_version=excluded.producer_version,
                        derivation_kind=excluded.derivation_kind,
                        epistemic_status=excluded.epistemic_status,
                        usable_scope=excluded.usable_scope,
                        confidence=max(portrait_facts.confidence, excluded.confidence),
                        sensitivity=excluded.sensitivity,
                        status=excluded.status,
                        evidence_hashes=excluded.evidence_hashes,
                        context_refs=excluded.context_refs,
                        last_evidence_at=excluded.last_evidence_at,
                        expires_at=excluded.expires_at,
                        supersedes_id=excluded.supersedes_id,
                        revision=excluded.revision,
                        operation_id=excluded.operation_id,
                        updated_at=excluded.updated_at
                    """,
                    (
                        fact_id,
                        person_id,
                        dimension,
                        claim_hash,
                        clean_text(fact.get("claim_summary"), 180),
                        tier,
                        clean_text(fact.get("producer_kind"), 80),
                        clean_text(fact.get("producer_version"), 80),
                        clean_text(fact.get("derivation_kind"), 80),
                        clean_text(fact.get("epistemic_status"), 80),
                        source_scope,
                        usable_scope,
                        max(0.0, min(1.0, float(fact.get("confidence") or 0.0))),
                        sensitivity,
                        status,
                        json_dumps(merged_hashes),
                        json_dumps(
                            [
                                clean_text(item, 160)
                                for item in (fact.get("context_refs") or [])
                                if clean_text(item, 160)
                            ][:8]
                        ),
                        first_at,
                        now,
                        clean_text(fact.get("expires_at"), 80),
                        clean_text(fact.get("supersedes_id"), 120),
                        revision,
                        clean_text(fact.get("operation_id"), 120),
                        clean_text(previous["created_at"], 80)
                        if previous is not None
                        else now,
                        now,
                    ),
                )
                superseded_ids: list[str] = []
                if status == "active" and single_value:
                    superseded_rows = self._conn.execute(
                        """
                        SELECT id FROM portrait_facts
                        WHERE person_id=? AND dimension=?
                          AND source_scope=? AND status='active' AND id!=?
                        """,
                        (person_id, dimension, source_scope, fact_id),
                    ).fetchall()
                    superseded_ids = [
                        clean_text(row["id"], 120) for row in superseded_rows
                    ]
                    if superseded_ids:
                        placeholders = ",".join("?" for _ in superseded_ids)
                        self._conn.execute(
                            f"""
                            UPDATE portrait_facts
                            SET status='superseded', supersedes_id=?, revision=revision+1,
                                operation_id=?, updated_at=?
                            WHERE id IN ({placeholders})
                            """,
                            [
                                fact_id,
                                clean_text(fact.get("operation_id"), 120),
                                now,
                                *superseded_ids,
                            ],
                        )
                        self._conn.execute(
                            f"""
                            UPDATE portrait_learning_queue
                            SET state='superseded', updated_at=?
                            WHERE fact_id IN ({placeholders}) AND state='pending'
                            """,
                            [now, *superseded_ids],
                        )
                portrait_revision = self._bump_portrait_revision_sync(person_id)
        return {
            "ok": True,
            "code": "portrait_fact_upserted",
            "created": previous is None,
            "fact_id": fact_id,
            "portrait_revision": portrait_revision,
            "superseded": len(superseded_ids),
        }

    async def list_portrait_evidence(
        self, person_id: str, *, limit: int = 64
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_portrait_evidence_sync, person_id, limit
        )

    def _list_portrait_evidence_sync(
        self, person_id: str, limit: int
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM portrait_evidence WHERE person_id=? ORDER BY created_at DESC LIMIT ?",
                (clean_text(person_id, 80), max(1, min(512, int(limit)))),
            ).fetchall()
        return [
            {
                "evidence_hash": row["evidence_hash"],
                "scope": row["scope"],
                "session_id": row["session_id"],
                "message_id": row["message_id"],
                "statement_fingerprint": row["statement_fingerprint"],
                "context_refs": json_loads(row["context_refs"], []),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def portrait_summary(
        self,
        person_id: str,
        *,
        scope: str = "",
        legacy_scope: str = "",
        limit: int = 8,
        low_only: bool = True,
        usage_min_confidence: float = 0.75,
        inferred_freshness_days: int = 90,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._portrait_summary_sync,
            person_id,
            scope,
            legacy_scope,
            limit,
            low_only,
            usage_min_confidence,
            inferred_freshness_days,
        )

    def _portrait_summary_sync(
        self,
        person_id: str,
        scope: str,
        legacy_scope: str,
        limit: int,
        low_only: bool,
        usage_min_confidence: float,
        inferred_freshness_days: int,
    ) -> dict[str, Any]:
        person_id = clean_text(person_id, 80)
        scope = clean_text(scope, 80)
        legacy_scope = clean_text(legacy_scope, 80)
        confidence_floor = max(0.0, min(1.0, float(usage_min_confidence or 0.75)))
        freshness_days = max(1, min(3650, int(inferred_freshness_days or 90)))
        with self._lock:
            person = self._conn.execute(
                "SELECT * FROM portrait_people WHERE person_id=?", (person_id,)
            ).fetchone()
            if person is None:
                return {
                    "ok": False,
                    "code": "bridge_unavailable",
                    "items": [],
                    "portrait_revision": 0,
                }
            if clean_text(person["profile_status"], 40) != "active" or clean_text(
                person["identity_assurance"], 40
            ) not in {"observed", "verified", "explicit_linked"}:
                return {
                    "ok": False,
                    "code": "bridge_person_mismatch",
                    "items": [],
                    "portrait_revision": int(person["portrait_revision"] or 0),
                }
            capabilities = self._portrait_scope_capability_sync(
                person_id, scope, legacy_scope=legacy_scope
            )
            if not bool(capabilities.get("portrait_usage_enabled")):
                return {
                    "ok": False,
                    "code": "portrait_usage_disabled",
                    "items": [],
                    "portrait_revision": int(person["portrait_revision"] or 0),
                }
            query = (
                "SELECT * FROM portrait_facts "
                "WHERE person_id=? AND status='active' "
                "AND producer_version!='req036.rule.v1'"
            )
            params: list[Any] = [person_id]
            if low_only:
                query += " AND sensitivity='low'"
            # Read a bounded superset before filtering scope, suppression,
            # confidence and freshness.  The bridge never sees rejected rows.
            query += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
            params.append(max(16, min(128, int(limit) * 8)))
            rows = self._conn.execute(query, params).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                if not self._portrait_scope_allows_row(row, scope, legacy_scope):
                    continue
                if self._portrait_suppressed_sync(
                    person_id,
                    row["dimension"],
                    row["normalized_claim_hash"],
                    row["source_scope"],
                ):
                    continue
                confidence = float(row["confidence"] or 0)
                if confidence < confidence_floor:
                    continue
                if row[
                    "portrait_tier"
                ] == "intelligent" and not self._portrait_timestamp_is_fresh(
                    row["updated_at"], freshness_days
                ):
                    continue
                items.append(
                    {
                        "dimension": row["dimension"],
                        "summary": row["claim_summary"],
                        "portrait_tier": row["portrait_tier"],
                        "epistemic_status": row["epistemic_status"],
                        "confidence": confidence,
                        "sensitivity": row["sensitivity"],
                        "usable_scope": row["usable_scope"],
                        "updated_at": row["updated_at"],
                    }
                )
                if len(items) >= max(1, min(32, int(limit))):
                    break
        return {
            "ok": True,
            "code": "profile_exact",
            "items": items,
            "portrait_revision": int(person["portrait_revision"] or 0),
            "last_synced_at": clean_text(person["last_synced_at"], 80),
        }
