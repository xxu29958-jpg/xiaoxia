from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    json_loads,
    stable_fingerprint,
    utc_now,
)


class MemoryStorePortraitGovernance:
    """Own portrait suppression, review and reversible migration. Shared resources stay with the root owner."""

    async def upsert_portrait_suppression(
        self, marker: dict[str, Any]
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._upsert_portrait_suppression_sync, deepcopy(marker)
        )

    def _upsert_portrait_suppression_sync(
        self, marker: dict[str, Any]
    ) -> dict[str, Any]:
        key = clean_text(marker.get("suppression_key"), 80)
        person_id = clean_text(marker.get("person_id"), 80)
        status = clean_text(marker.get("status"), 40) or "active"
        operation_id = clean_text(marker.get("operation_id"), 120)
        if (
            not key
            or not person_id
            or not operation_id
            or status
            not in {
                "active",
                "reconfirmation_pending",
                "revoked",
                "superseded",
                "expired",
            }
        ):
            return {"ok": False, "code": "suppression_invalid"}
        now = utc_now()
        with self._lock:
            previous = self._conn.execute(
                "SELECT * FROM portrait_suppressions WHERE suppression_key=?", (key,)
            ).fetchone()
            if (
                previous is not None
                and clean_text(previous["operation_id"], 120) == operation_id
            ):
                return {
                    "ok": True,
                    "code": "suppression_idempotent_replay",
                    "revision": int(previous["revision"] or 1),
                }
            revision = int(previous["revision"] or 0) + 1 if previous is not None else 1
            self._conn.execute(
                """
                INSERT INTO portrait_suppressions(
                    suppression_key, person_id, dimension, normalized_claim_hash, scope, reason, actor, status,
                    origin_identity_key, operation_id, revision, created_at, updated_at, expires_at, revoked_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(suppression_key) DO UPDATE SET
                    reason=excluded.reason, actor=excluded.actor, status=excluded.status,
                    operation_id=excluded.operation_id, revision=excluded.revision, updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at, revoked_at=excluded.revoked_at
                """,
                (
                    key,
                    person_id,
                    clean_text(marker.get("dimension"), 80),
                    clean_text(marker.get("normalized_claim_hash"), 80),
                    clean_text(marker.get("scope"), 80),
                    clean_text(marker.get("reason"), 80),
                    clean_text(marker.get("actor"), 80),
                    status,
                    clean_text(marker.get("origin_identity_key"), 96),
                    operation_id,
                    revision,
                    now
                    if previous is None
                    else clean_text(marker.get("created_at"), 80) or now,
                    now,
                    clean_text(marker.get("expires_at"), 80),
                    clean_text(marker.get("revoked_at"), 80),
                ),
            )
            self._bump_portrait_revision_sync(person_id)
            self._conn.commit()
        return {"ok": True, "code": "suppression_upserted", "revision": revision}

    async def list_portrait_people(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_portrait_people_sync, limit)

    async def portrait_status(self, person_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._portrait_status_sync, person_id)

    def _portrait_status_sync(self, person_id: str) -> dict[str, Any]:
        person_id = clean_text(person_id, 80)
        with self._lock:
            row = self._conn.execute(
                "SELECT portrait_revision, last_synced_at, profile_status FROM portrait_people WHERE person_id=?",
                (person_id,),
            ).fetchone()
        if row is None:
            return {
                "ok": False,
                "code": "bridge_unavailable",
                "person_id": person_id,
                "last_synced_at": "",
                "portrait_revision": 0,
            }
        return {
            "ok": clean_text(row["profile_status"], 40) == "active",
            "code": "profile_exact"
            if clean_text(row["profile_status"], 40) == "active"
            else "bridge_person_mismatch",
            "person_id": person_id,
            "portrait_revision": int(row["portrait_revision"] or 0),
            "last_synced_at": clean_text(row["last_synced_at"], 80),
        }

    def _list_portrait_people_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT p.*, COUNT(DISTINCT f.id) AS fact_count, COUNT(DISTINCT e.evidence_hash) AS evidence_count
                FROM portrait_people p
                LEFT JOIN portrait_facts f ON f.person_id=p.person_id
                LEFT JOIN portrait_evidence e ON e.person_id=p.person_id
                GROUP BY p.person_id
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [
            {
                "person_id": clean_text(row["person_id"], 80),
                "identity_assurance": clean_text(row["identity_assurance"], 40),
                "profile_status": clean_text(row["profile_status"], 40),
                "projection_revision": int(row["projection_revision"] or 0),
                "portrait_revision": int(row["portrait_revision"] or 0),
                "last_synced_at": clean_text(row["last_synced_at"], 80),
                "updated_at": clean_text(row["updated_at"], 80),
                "capability_summary": json_loads(row["capability_summary"], {}),
                "fact_count": int(row["fact_count"] or 0),
                "evidence_count": int(row["evidence_count"] or 0),
            }
            for row in rows
        ]

    async def portrait_governance_detail(self, person_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._portrait_governance_detail_sync, person_id)

    def _portrait_governance_detail_sync(self, person_id: str) -> dict[str, Any]:
        person_id = clean_text(person_id, 80)
        with self._lock:
            person = self._conn.execute(
                "SELECT * FROM portrait_people WHERE person_id=?", (person_id,)
            ).fetchone()
            if person is None:
                return {
                    "ok": False,
                    "code": "bridge_unavailable",
                    "person": {},
                    "facts": [],
                    "suppressions": [],
                }
            facts = self._conn.execute(
                """
                SELECT id, dimension, normalized_claim_hash, claim_summary, portrait_tier, producer_kind,
                       derivation_kind, epistemic_status, source_scope, usable_scope, confidence,
                       sensitivity, status, first_evidence_at, last_evidence_at, expires_at, revision
                FROM portrait_facts WHERE person_id=? ORDER BY updated_at DESC LIMIT 200
                """,
                (person_id,),
            ).fetchall()
            suppressions = self._conn.execute(
                """
                SELECT suppression_key, dimension, normalized_claim_hash, scope, reason, actor, status,
                       operation_id, revision, created_at, updated_at, expires_at
                FROM portrait_suppressions WHERE person_id=? ORDER BY updated_at DESC LIMIT 200
                """,
                (person_id,),
            ).fetchall()
        return {
            "ok": True,
            "code": "profile_exact",
            "person": {
                "person_id": person_id,
                "identity_assurance": clean_text(person["identity_assurance"], 40),
                "profile_status": clean_text(person["profile_status"], 40),
                "portrait_revision": int(person["portrait_revision"] or 0),
                "last_synced_at": clean_text(person["last_synced_at"], 80),
                "capability_summary": json_loads(person["capability_summary"], {}),
            },
            "facts": [dict(row) for row in facts],
            "suppressions": [dict(row) for row in suppressions],
        }

    async def govern_portrait_fact(
        self,
        *,
        person_id: str,
        fact_id: str,
        action: str,
        actor: str,
        operation_id: str,
        expires_at: str = "",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._govern_portrait_fact_sync,
            person_id,
            fact_id,
            action,
            actor,
            operation_id,
            expires_at,
        )

    def _govern_portrait_fact_sync(
        self,
        person_id: str,
        fact_id: str,
        action: str,
        actor: str,
        operation_id: str,
        expires_at: str,
    ) -> dict[str, Any]:
        person_id = clean_text(person_id, 80)
        fact_id = clean_text(fact_id, 120)
        action = clean_text(action, 40)
        actor = clean_text(actor, 80) or "administrator"
        operation_id = clean_text(operation_id, 120)
        expires_at = clean_text(expires_at, 80)
        if (
            not person_id
            or not fact_id
            or not operation_id
            or action not in {"suppress", "freeze", "reconfirmation_pending"}
        ):
            return {"ok": False, "code": "invalid_request"}
        if action == "freeze" and not expires_at:
            return {"ok": False, "code": "suppression_expiry_required"}
        with self._lock:
            fact = self._conn.execute(
                "SELECT * FROM portrait_facts WHERE id=? AND person_id=?",
                (fact_id, person_id),
            ).fetchone()
            if fact is None:
                return {"ok": False, "code": "portrait_fact_not_found"}
        reason = {
            "suppress": "administrator_delete_or_forget",
            "freeze": "administrator_temporary_freeze",
            "reconfirmation_pending": "reconfirmation_requested",
        }[action]
        status = (
            "reconfirmation_pending" if action == "reconfirmation_pending" else "active"
        )
        marker = {
            "suppression_key": stable_fingerprint(
                "portrait_suppression",
                person_id,
                fact["dimension"],
                fact["normalized_claim_hash"],
                fact["source_scope"],
            ),
            "person_id": person_id,
            "dimension": clean_text(fact["dimension"], 80),
            "normalized_claim_hash": clean_text(fact["normalized_claim_hash"], 80),
            "scope": clean_text(fact["source_scope"], 80),
            "reason": reason,
            "actor": actor,
            "status": status,
            "operation_id": operation_id,
            "expires_at": expires_at if action == "freeze" else "",
        }
        result = self._upsert_portrait_suppression_sync(marker)
        return {**result, "fact_id": fact_id, "action": action}

    async def portrait_migration(
        self, *, operation_id: str, dry_run: bool = True
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._portrait_migration_sync, operation_id, dry_run
        )

    def _portrait_migration_sync(
        self, operation_id: str, dry_run: bool
    ) -> dict[str, Any]:
        operation_id = clean_text(operation_id, 120)
        if not operation_id:
            return {"ok": False, "code": "invalid_request"}
        with self._lock:
            count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE memory_type IN ('user_profile', 'user_preference', 'user_habit')"
                ).fetchone()[0]
                or 0
            )
            if dry_run:
                return {
                    "ok": True,
                    "code": "migration_dry_run",
                    "write_count": 0,
                    "legacy_candidate_count": count,
                }
            prior = self._conn.execute(
                "SELECT * FROM portrait_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if prior is not None:
                return {
                    "ok": True,
                    "code": "migration_idempotent_replay",
                    "operation_id": operation_id,
                }
            now = utc_now()
            self._conn.execute(
                "INSERT INTO portrait_operations(operation_id, operation_kind, payload_hash, snapshot, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    operation_id,
                    "legacy_portrait_projection",
                    stable_fingerprint("portrait", operation_id, count),
                    json_dumps({"fact_ids": []}),
                    "applied",
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return {
            "ok": True,
            "code": "migration_applied",
            "operation_id": operation_id,
            "legacy_candidate_count": count,
        }

    async def rollback_portrait_migration(self, *, operation_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._rollback_portrait_migration_sync, operation_id
        )

    def _rollback_portrait_migration_sync(self, operation_id: str) -> dict[str, Any]:
        operation_id = clean_text(operation_id, 120)
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot FROM portrait_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "code": "migration_not_found"}
            snapshot = json_loads(row["snapshot"], {})
            fact_ids = (
                snapshot.get("fact_ids")
                if isinstance(snapshot, dict)
                and isinstance(snapshot.get("fact_ids"), list)
                else []
            )
            if fact_ids:
                self._conn.executemany(
                    "DELETE FROM portrait_facts WHERE id=?",
                    [
                        (clean_text(item, 120),)
                        for item in fact_ids
                        if clean_text(item, 120)
                    ],
                )
            self._conn.execute(
                "UPDATE portrait_operations SET state='rolled_back', updated_at=? WHERE operation_id=?",
                (utc_now(), operation_id),
            )
            self._conn.commit()
        return {
            "ok": True,
            "code": "migration_rolled_back",
            "operation_id": operation_id,
        }
