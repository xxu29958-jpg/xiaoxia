from __future__ import annotations

import asyncio
import re
from typing import Any

from .models import (
    clean_text,
    json_loads,
    stable_fingerprint,
    utc_now,
)
from .portrait_namespace import portrait_scope_kind


class MemoryStorePortraitLearning:
    """Portrait learning; state and transaction ownership remain in the facade."""

    async def enqueue_portrait_learning(
        self, *, person_id: str, fact_id: str, evidence_hash: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._enqueue_portrait_learning_sync, person_id, fact_id, evidence_hash
        )

    async def list_pending_portrait_people(self, *, limit: int = 500) -> list[str]:
        return await asyncio.to_thread(self._list_pending_portrait_people_sync, limit)

    def _list_pending_portrait_people_sync(self, limit: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT DISTINCT q.person_id
                FROM portrait_learning_queue q
                JOIN portrait_people p ON p.person_id=q.person_id
                WHERE q.state='pending' AND p.profile_status='active'
                ORDER BY q.updated_at ASC
                LIMIT ?
                """,
                (max(1, min(2000, int(limit))),),
            ).fetchall()
        return [
            clean_text(row["person_id"], 80)
            for row in rows
            if clean_text(row["person_id"], 80)
        ]

    def _enqueue_portrait_learning_sync(
        self, person_id: str, fact_id: str, evidence_hash: str
    ) -> dict[str, Any]:
        person_id = clean_text(person_id, 80)
        fact_id = clean_text(fact_id, 120)
        evidence_hash = clean_text(evidence_hash, 80)
        if (
            not person_id
            or not fact_id
            or not re.fullmatch(r"[0-9a-f]{64}", evidence_hash)
        ):
            return {"ok": False, "code": "portrait_queue_invalid"}
        queue_id = f"portrait_queue_{stable_fingerprint(person_id, fact_id, evidence_hash)[:24]}"
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO portrait_learning_queue(queue_id, person_id, fact_id, evidence_hash, state, created_at, updated_at)
                VALUES(?,?,?,?, 'pending', ?, ?)
                """,
                (queue_id, person_id, fact_id, evidence_hash, now, now),
            )
            self._conn.commit()
        return {
            "ok": True,
            "code": "portrait_queued",
            "created": bool(cur.rowcount),
            "queue_id": queue_id,
        }

    async def run_portrait_daily_batch(
        self,
        *,
        person_id: str,
        run_day: str,
        min_independent_evidence: int = 3,
        success_limit: int = 1,
        attempt_limit: int = 2,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._run_portrait_daily_batch_sync,
            person_id,
            run_day,
            min_independent_evidence,
            success_limit,
            attempt_limit,
        )

    def _portrait_distinct_statement_count_sync(
        self, evidence_hashes: list[Any]
    ) -> int:
        hashes = list(
            dict.fromkeys(
                clean_text(item, 80)
                for item in evidence_hashes
                if re.fullmatch(r"[0-9a-f]{64}", clean_text(item, 80))
            )
        )[:16]
        if not hashes:
            return 0
        placeholders = ",".join("?" for _ in hashes)
        rows = self._conn.execute(
            f"SELECT evidence_hash, statement_fingerprint FROM portrait_evidence WHERE evidence_hash IN ({placeholders})",
            hashes,
        ).fetchall()
        fingerprints = {
            clean_text(row["statement_fingerprint"], 80)
            or clean_text(row["evidence_hash"], 80)
            for row in rows
            if clean_text(row["statement_fingerprint"], 80)
            or clean_text(row["evidence_hash"], 80)
        }
        return len(fingerprints)

    def _run_portrait_daily_batch_sync(
        self,
        person_id: str,
        run_day: str,
        min_independent_evidence: int,
        success_limit: int,
        attempt_limit: int,
    ) -> dict[str, Any]:
        person_id = clean_text(person_id, 80)
        run_day = clean_text(run_day, 16)
        if not person_id or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_day):
            return {"ok": False, "code": "invalid_request"}
        min_independent_evidence = max(1, min(16, int(min_independent_evidence)))
        success_limit = max(1, min(4, int(success_limit)))
        attempt_limit = max(success_limit, min(8, int(attempt_limit)))
        now = utc_now()
        with self._lock:
            person = self._conn.execute(
                "SELECT * FROM portrait_people WHERE person_id=?", (person_id,)
            ).fetchone()
            if person is None:
                return {"ok": False, "code": "bridge_unavailable"}
            run = self._conn.execute(
                "SELECT * FROM portrait_daily_runs WHERE person_id=? AND run_day=?",
                (person_id, run_day),
            ).fetchone()
            attempts = int(run["attempts"] or 0) if run is not None else 0
            successes = int(run["successes"] or 0) if run is not None else 0
            if successes >= success_limit:
                return {
                    "ok": False,
                    "code": "portrait_daily_limit",
                    "attempts": attempts,
                    "successes": successes,
                }
            if attempts >= attempt_limit:
                return {
                    "ok": False,
                    "code": "portrait_daily_limit",
                    "attempts": attempts,
                    "successes": successes,
                }
            pending_scopes = self._conn.execute(
                """
                SELECT DISTINCT f.source_scope
                FROM portrait_learning_queue q
                JOIN portrait_facts f ON f.id=q.fact_id
                WHERE q.person_id=? AND q.state='pending' AND f.status='active'
                """,
                (person_id,),
            ).fetchall()
            if not any(
                bool(
                    self._portrait_scope_capability_sync(
                        person_id,
                        clean_text(row["source_scope"], 80),
                        legacy_scope=(
                            "private"
                            if clean_text(row["source_scope"], 80) == "private"
                            else ""
                        ),
                    ).get("portrait_learning_enabled")
                )
                for row in pending_scopes
            ):
                return {"ok": False, "code": "portrait_learning_disabled"}
            attempts += 1
            self._conn.execute(
                """
                INSERT INTO portrait_daily_runs(person_id, run_day, attempts, successes, last_code, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(person_id, run_day) DO UPDATE SET attempts=excluded.attempts, updated_at=excluded.updated_at
                """,
                (
                    person_id,
                    run_day,
                    attempts,
                    successes,
                    "portrait_insufficient_evidence",
                    now,
                ),
            )
            rows = self._conn.execute(
                """
                SELECT q.queue_id, q.fact_id, f.* FROM portrait_learning_queue q
                JOIN portrait_facts f ON f.id=q.fact_id
                WHERE q.person_id=? AND q.state='pending' AND f.status='active' AND f.portrait_tier='base'
                ORDER BY q.created_at ASC
                """,
                (person_id,),
            ).fetchall()
            created = 0
            for row in rows:
                row_scope = clean_text(row["source_scope"], 80)
                row_capabilities = self._portrait_scope_capability_sync(
                    person_id,
                    row_scope,
                    legacy_scope="private" if row_scope == "private" else "",
                )
                if not bool(row_capabilities.get("portrait_learning_enabled")):
                    continue
                evidence_hashes = json_loads(row["evidence_hashes"], [])
                if (
                    self._portrait_distinct_statement_count_sync(evidence_hashes)
                    < min_independent_evidence
                ):
                    continue
                if self._portrait_suppressed_sync(
                    person_id,
                    row["dimension"],
                    row["normalized_claim_hash"],
                    row["source_scope"],
                ):
                    self._conn.execute(
                        "UPDATE portrait_learning_queue SET state='suppressed', updated_at=? "
                        "WHERE person_id=? AND fact_id=? AND state='pending'",
                        (now, person_id, row["fact_id"]),
                    )
                    continue
                inferred = {
                    "person_id": person_id,
                    "dimension": row["dimension"],
                    "normalized_claim_hash": row["normalized_claim_hash"],
                    "claim_summary": clean_text(f"可能{row['claim_summary']}", 180),
                    "portrait_tier": "intelligent",
                    "producer_kind": "daily_evidence_batch",
                    "producer_version": "req036.batch.v1",
                    "derivation_kind": "independent_evidence_aggregate",
                    "epistemic_status": "inferred",
                    "source_scope": row["source_scope"],
                    "usable_scope": (
                        "self_low_global"
                        if portrait_scope_kind(row["source_scope"]) == "private"
                        else "source_only"
                    ),
                    "confidence": min(0.95, max(0.75, float(row["confidence"] or 0.0))),
                    "sensitivity": row["sensitivity"],
                    "evidence_hashes": evidence_hashes,
                    "context_refs": json_loads(row["context_refs"], []),
                    "operation_id": f"portrait.daily:{person_id[-12:]}:{run_day}",
                }
                result = self._upsert_portrait_fact_sync(inferred)
                if result.get("ok"):
                    self._conn.execute(
                        "UPDATE portrait_learning_queue SET state='processed', updated_at=? "
                        "WHERE person_id=? AND fact_id=? AND state='pending'",
                        (now, person_id, row["fact_id"]),
                    )
                    created += 1
                    break
            successes += 1 if created else 0
            code = (
                "portrait_fact_upserted"
                if created
                else "portrait_insufficient_evidence"
            )
            self._conn.execute(
                "UPDATE portrait_daily_runs SET successes=?, last_code=?, updated_at=? WHERE person_id=? AND run_day=?",
                (successes, code, now, person_id, run_day),
            )
            self._conn.commit()
        return {
            "ok": bool(created),
            "code": code,
            "attempts": attempts,
            "successes": successes,
            "created": created,
        }
