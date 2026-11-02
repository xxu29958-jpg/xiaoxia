from __future__ import annotations

import re
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
)
from .store_support import (
    _unpack_embedding_vector,
)


class MemoryStoreProfileRepairSnapshots:
    """Profile repair snapshots; state and transaction ownership remain in the facade."""

    @staticmethod
    def _valid_profile_operation_id(operation_id: Any) -> str:
        value = clean_text(operation_id, 120)
        return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", value) else ""

    def _profile_repair_record_snapshot_sync(
        self,
        record: MemoryRecord,
        *,
        group_id: str = "",
        guard_only: bool = False,
    ) -> dict[str, Any]:
        review_rows = self._conn.execute(
            "SELECT * FROM review_queue WHERE memory_id=? ORDER BY created_at, id",
            (record.id,),
        ).fetchall()
        embedding_rows = self._conn.execute(
            "SELECT * FROM memory_embeddings WHERE memory_id=? ORDER BY provider_id",
            (record.id,),
        ).fetchall()
        embedding_snapshots: list[dict[str, Any]] = []
        for row in embedding_rows:
            snapshot = dict(row)
            raw_vector = snapshot.get("vector")
            # 二进制向量转回 JSON 文本，保证快照可 json 序列化且恢复路径兼容
            if isinstance(raw_vector, (bytes, bytearray, memoryview)):
                snapshot["vector"] = json_dumps(_unpack_embedding_vector(raw_vector))
            embedding_snapshots.append(snapshot)
        return {
            "record_kind": "memory",
            "record_id": record.id,
            "memory_id": record.id,
            "group_id": clean_text(group_id, 120),
            "guard_only": bool(guard_only),
            "before": self._memory_db_snapshot(record),
            "review_queue": [dict(row) for row in review_rows],
            "embeddings": embedding_snapshots,
            "after_fingerprint": "",
        }

    def _profile_repair_portrait_snapshot_sync(
        self,
        fact: Any,
        *,
        group_id: str = "",
        guard_only: bool = False,
    ) -> dict[str, Any]:
        fact_data = dict(fact)
        fact_id = clean_text(fact_data.get("id"), 120)
        queue_rows = self._conn.execute(
            """
            SELECT * FROM portrait_learning_queue
            WHERE fact_id=? ORDER BY queue_id
            """,
            (fact_id,),
        ).fetchall()
        return {
            "record_kind": "portrait_fact",
            "record_id": fact_id,
            "group_id": clean_text(group_id, 120),
            "guard_only": bool(guard_only),
            "person_id": clean_text(fact_data.get("person_id"), 80),
            "before": fact_data,
            "learning_queue": [dict(row) for row in queue_rows],
            "after_fingerprint": "",
            "after_queue_fingerprint": "",
        }

    def _write_portrait_fact_snapshot_sync(self, snapshot: dict[str, Any]) -> bool:
        columns = (
            "id",
            "person_id",
            "dimension",
            "normalized_claim_hash",
            "claim_summary",
            "portrait_tier",
            "producer_kind",
            "producer_version",
            "derivation_kind",
            "epistemic_status",
            "source_scope",
            "usable_scope",
            "confidence",
            "sensitivity",
            "status",
            "evidence_hashes",
            "context_refs",
            "first_evidence_at",
            "last_evidence_at",
            "expires_at",
            "supersedes_id",
            "revision",
            "operation_id",
            "created_at",
            "updated_at",
        )
        if any(column not in snapshot for column in columns):
            return False
        names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column}=excluded.{column}" for column in columns if column != "id"
        )
        self._conn.execute(
            f"""
            INSERT INTO portrait_facts ({names}) VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {updates}
            """,
            [snapshot[column] for column in columns],
        )
        return True

    def _restore_portrait_queue_snapshot_sync(
        self, fact_id: str, rows: list[Any]
    ) -> bool:
        columns = (
            "queue_id",
            "person_id",
            "fact_id",
            "evidence_hash",
            "state",
            "created_at",
            "updated_at",
        )
        normalized: list[dict[str, Any]] = []
        for raw in rows:
            if not isinstance(raw, dict):
                return False
            row = dict(raw)
            if (
                any(column not in row for column in columns)
                or clean_text(row.get("fact_id"), 120) != fact_id
            ):
                return False
            normalized.append(row)
        self._conn.execute(
            "DELETE FROM portrait_learning_queue WHERE fact_id=?", (fact_id,)
        )
        for row in normalized:
            self._conn.execute(
                """
                INSERT INTO portrait_learning_queue(
                    queue_id, person_id, fact_id, evidence_hash, state,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                [row[column] for column in columns],
            )
        return True

    @staticmethod
    def _profile_repair_history(
        metadata: dict[str, Any],
        *,
        operation_id: str,
        action: str,
        reason: str,
        now: str,
    ) -> dict[str, Any]:
        updated = dict(metadata)
        history = updated.get("profile_repair_history")
        history = list(history) if isinstance(history, list) else []
        history.append(
            {
                "operation_id": operation_id,
                "action": action,
                "reason": clean_text(reason, 240),
                "applied_at": now,
            }
        )
        updated["profile_repair_history"] = history[-20:]
        return updated

    def _profile_repair_merge_sync(
        self,
        source: MemoryRecord,
        canonical: MemoryRecord,
        *,
        operation_id: str,
        reason: str,
        now: str,
    ) -> tuple[MemoryRecord, MemoryRecord] | None:
        source_metadata = source.metadata if isinstance(source.metadata, dict) else {}
        canonical_metadata = (
            canonical.metadata if isinstance(canonical.metadata, dict) else {}
        )
        if self._profile_domain(source) != self._profile_domain(canonical):
            return None
        source_dimension = clean_text(
            source_metadata.get("profile_dimension"), 80
        ).lower()
        canonical_dimension = clean_text(
            canonical_metadata.get("profile_dimension"), 80
        ).lower()
        source_value = clean_text(
            source_metadata.get("normalized_value"), 240
        ).casefold()
        canonical_value = clean_text(
            canonical_metadata.get("normalized_value"), 240
        ).casefold()
        source_polarity = clean_text(
            source_metadata.get("profile_polarity"), 40
        ).lower()
        canonical_polarity = clean_text(
            canonical_metadata.get("profile_polarity"), 40
        ).lower()
        if (
            not source_dimension
            or source_dimension != canonical_dimension
            or source_value != canonical_value
            or source_polarity != canonical_polarity
        ):
            return None

        refs = list(
            dict.fromkeys(
                [
                    *self._profile_evidence_refs(canonical),
                    *self._profile_evidence_refs(source),
                ]
            )
        )[:256]
        statement_fingerprints = list(
            dict.fromkeys(
                [
                    *self._profile_statement_fingerprints(canonical),
                    *self._profile_statement_fingerprints(source),
                ]
            )
        )[:256]
        next_state = (
            "active"
            if "active" in {self._profile_state(source), self._profile_state(canonical)}
            else "candidate"
        )
        merged_metadata = self._profile_repair_history(
            canonical_metadata,
            operation_id=operation_id,
            action="merge_target",
            reason=reason,
            now=now,
        )
        merged_metadata["profile_evidence_refs"] = refs
        merged_metadata["profile_statement_fingerprints"] = statement_fingerprints
        merged_metadata["evidence_count"] = len(refs)
        merged_metadata["independent_evidence_count"] = min(
            len(refs),
            len(statement_fingerprints),
        )
        merged_metadata["profile_state"] = next_state
        merged_metadata["profile_status"] = next_state
        canonical.metadata = merged_metadata
        canonical.evidence = self._profile_evidence_text([canonical, source])
        canonical.confidence = max(canonical.confidence, source.confidence)
        canonical.importance = max(canonical.importance, source.importance)
        canonical.merged_count = max(1, len(refs))
        canonical.lifecycle = "stable_memory" if next_state == "active" else "raw_event"
        canonical.review_status = "auto" if next_state == "active" else "pending"
        canonical.updated_at = now

        superseded_metadata = self._profile_repair_history(
            source_metadata,
            operation_id=operation_id,
            action="merge_source",
            reason=reason,
            now=now,
        )
        superseded_metadata["profile_state"] = "superseded"
        superseded_metadata["profile_status"] = "superseded"
        superseded_metadata["profile_superseded_by"] = canonical.id
        source.metadata = superseded_metadata
        source.lifecycle = "archived"
        source.review_status = "auto"
        source.supersedes_id = canonical.id
        source.updated_at = now
        return source, canonical
