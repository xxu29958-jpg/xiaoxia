from __future__ import annotations

import asyncio
import math
import sqlite3
from collections.abc import Callable
from typing import Any

from .core_state_store import CoreStateAccess
from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    new_id,
    utc_now,
)
from .profile_quality import normalize_profile_value


class MemoryStoreRecordInsert(CoreStateAccess):
    """store record insert; the facade owns all shared resources."""

    PROFILE_MEMORY_TYPES: frozenset[str]
    PROFILE_RULE_EXTRACTORS: frozenset[str]
    PROFILE_SINGLE_VALUE_DIMENSIONS: frozenset[str]
    _embedding_candidate_cache: dict[
        tuple[str, bool, int], list[tuple[MemoryRecord, list[float], str]]
    ]
    _embedding_candidate_cache_revision: str
    _profile_domain_rows_sync: Callable[[MemoryRecord], list[sqlite3.Row]]
    _profile_state: Callable[[MemoryRecord], str]
    _write_memory_record_sync: Callable[[MemoryRecord], sqlite3.Row]

    @staticmethod
    def _memory_atom_record_for_row(
        row: sqlite3.Row,
        *,
        reset_semantic_keys: bool = False,
        **changes: Any,
    ) -> MemoryRecord:
        payload = dict(row)
        payload.update(changes)
        if reset_semantic_keys:
            payload["canonical_key"] = ""
            payload["content_fingerprint"] = ""
        return MemoryRecord.from_row(payload)

    async def insert_memory(self, record: MemoryRecord, review_reason: str = "") -> str:
        return await self._run_recoverable_database_operation(
            self._insert_memory_sync,
            record,
            review_reason,
        )

    async def upsert_bot_personal_archive(self, record: MemoryRecord) -> dict[str, Any]:
        """Commit the archive version check and row update in one transaction."""
        return await self._run_recoverable_database_operation(
            self._upsert_bot_personal_archive_sync, record
        )

    def _upsert_bot_personal_archive_sync(self, record: MemoryRecord) -> dict[str, Any]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        version = metadata.get("version")
        fingerprint = metadata.get("payload_fingerprint")
        if (
            metadata.get("bot_personal") is not True
            or type(version) is not int
            or version < 1
            or not fingerprint
        ):
            raise ValueError("invalid_bot_personal_archive_record")
        receipt = {
            "ok": False,
            "record_id": record.id,
            "version": version,
            "deduplicated": False,
            "error_code": None,
            "state": "sent",
        }
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT metadata FROM memories WHERE id=?", (record.id,)
            ).fetchone()
            if row is not None:
                previous = json_loads(row["metadata"], {})
                old_version = int(previous.get("version") or 0)
                if old_version > version:
                    return {
                        **receipt,
                        "version": old_version,
                        "state": "stale_version",
                        "error_code": "stale_version",
                    }
                if old_version == version:
                    if previous.get("payload_fingerprint") == fingerprint:
                        return {
                            **receipt,
                            "ok": True,
                            "state": "deduplicated",
                            "deduplicated": True,
                        }
                    return {
                        **receipt,
                        "state": "version_conflict",
                        "error_code": "version_conflict",
                    }
            stored_id = self._insert_memory_sync(record, _commit=False)
            if stored_id != record.id:
                raise RuntimeError("archive_identity_conflict")
        return {**receipt, "ok": True}

    def _insert_memory_sync(
        self,
        record: MemoryRecord,
        review_reason: str = "",
        _commit: bool = True,
    ) -> str:
        # _commit=False 用于批量写链：由外层事务统一提交，避免逐次 fsync。
        record.ensure_defaults()
        data = record.to_db()
        columns = ", ".join(data.keys())
        placeholders = ", ".join(f":{key}" for key in data.keys())
        updates = ", ".join(
            f"{key}=excluded.{key}" for key in data.keys() if key != "id"
        )
        with self._lock:
            duplicate = None
            if record.content_fingerprint:
                duplicate = self._conn.execute(
                    """
                    SELECT id, importance, confidence, merged_count, evidence, metadata
                    FROM memories
                    WHERE content_fingerprint=? AND id<>? AND lifecycle!='archived'
                    ORDER BY merged_count DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (record.content_fingerprint, record.id),
                ).fetchone()
            if duplicate:
                merged_metadata = json_loads(duplicate["metadata"], {})
                incoming_metadata = (
                    record.metadata if isinstance(record.metadata, dict) else {}
                )
                merged_metadata.setdefault("merged_from", [])
                merged_from = merged_metadata.get("merged_from")
                if isinstance(merged_from, list) and record.id not in merged_from:
                    merged_from.append(record.id)
                merged_metadata["last_merge_source"] = record.source_plugin
                for key, value in incoming_metadata.items():
                    if key in {
                        "persona_importance",
                        "relationship_weight",
                        "emotional_weight",
                        "promise_weight",
                        "open_loop_weight",
                        "creative_weight",
                        "preference_weight",
                        "self_continuity_weight",
                        "freshness_weight",
                        "scar_weight",
                        "emotional_debt_weight",
                    }:
                        try:
                            merged_metadata[key] = max(
                                float(merged_metadata.get(key) or 0.0),
                                float(value or 0.0),
                            )
                        except Exception:
                            merged_metadata.setdefault(key, value)
                    elif key in {
                        "memory_reason",
                        "relationship_phase",
                        "decay_mode",
                        "last_emotional_touch_at",
                        "importance_evaluator",
                        "importance_source",
                    }:
                        if value:
                            merged_metadata[key] = value
                    elif key == "mention_policy":
                        incoming_policy = clean_text(value, 60)
                        existing_policy = clean_text(merged_metadata.get(key), 60)
                        policy_rank = {
                            "direct": 0,
                            "soft_echo": 1,
                            "tone_only": 2,
                            "avoid_unless_asked": 3,
                        }
                        if incoming_policy and policy_rank.get(
                            incoming_policy, 1
                        ) > policy_rank.get(existing_policy, -1):
                            merged_metadata[key] = incoming_policy
                    elif key == "mentionability_score":
                        try:
                            incoming_score = float(value or 0.5)
                            existing_score = float(merged_metadata.get(key, 0.5) or 0.5)
                            merged_metadata[key] = round(
                                min(incoming_score, existing_score), 3
                            )
                        except Exception:
                            merged_metadata.setdefault(key, value)
                    elif key == "mention_policy_source":
                        merged_metadata.setdefault(key, value)
                    elif key == "persona_dimensions" and isinstance(value, list):
                        old_dimensions = merged_metadata.get("persona_dimensions")
                        if not isinstance(old_dimensions, list):
                            old_dimensions = []
                        merged_metadata[key] = list(
                            dict.fromkeys([*old_dimensions, *value])
                        )
                evidence = duplicate["evidence"] or record.evidence
                if record.evidence and record.evidence not in evidence:
                    evidence = clean_text(f"{evidence}\n---\n{record.evidence}", 4000)
                self._conn.execute(
                    """
                    UPDATE memories
                    SET importance=max(importance, ?),
                        confidence=max(confidence, ?),
                        evidence=?,
                        metadata=?,
                        merged_count=COALESCE(merged_count, 1) + 1,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        record.importance,
                        record.confidence,
                        evidence,
                        json_dumps(merged_metadata),
                        utc_now(),
                        duplicate["id"],
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (duplicate["id"],)
                ).fetchone()
                self._upsert_memory_fts_row(row)
                if _commit:
                    self._conn.commit()
                return str(duplicate["id"])
            self._conn.execute(
                f"INSERT INTO memories ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(id) DO UPDATE SET {updates}",
                data,
            )
            if record.review_status == "pending" or review_reason:
                self._upsert_review_sync(record.id, review_reason or "待人工确认")
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (record.id,)
            ).fetchone()
            self._upsert_memory_fts_row(row)
            if _commit:
                self._conn.commit()
        return record.id

    def _upsert_review_sync(self, memory_id: str, reason: str) -> None:
        now = utc_now()
        self._conn.execute(
            """
            INSERT INTO review_queue(id, memory_id, reason, status, created_at, updated_at)
            VALUES(:id, :memory_id, :reason, 'pending', :created_at, :updated_at)
            ON CONFLICT(memory_id, reason) DO UPDATE SET updated_at=excluded.updated_at
            """,
            {
                "id": new_id("review"),
                "memory_id": memory_id,
                "reason": clean_text(reason, 500),
                "created_at": now,
                "updated_at": now,
            },
        )

    async def list_core_memories(self, limit: int = 200) -> list[MemoryRecord]:
        """Read active core blocks without routing them through similarity search."""
        return await self._run_recoverable_database_operation(
            self._list_core_memories_sync,
            limit,
        )

    def _list_core_memories_sync(self, limit: int) -> list[MemoryRecord]:
        now = utc_now()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM memories
                WHERE memory_type='core_memory'
                  AND lifecycle!='archived'
                  AND review_status!='pending'
                  AND validity_status='active'
                  AND (valid_from='' OR julianday(valid_from) IS NULL OR julianday(valid_from)<=julianday(?))
                  AND (valid_to='' OR julianday(valid_to) IS NULL OR julianday(valid_to)>=julianday(?))
                ORDER BY
                    CASE
                        WHEN json_valid(metadata)
                        THEN COALESCE(CAST(json_extract(metadata, '$.core_priority') AS INTEGER), 50)
                        ELSE 50
                    END DESC,
                    updated_at DESC,
                    id ASC
                LIMIT ?
                """,
                (now, now, max(1, min(1000, int(limit or 200)))),
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    async def update_review_status(self, memory_id: str, status: str) -> bool:
        return await asyncio.to_thread(
            self._update_review_status_sync, memory_id, status
        )

    def _update_review_status_sync(self, memory_id: str, status: str) -> bool:
        memory_id = clean_text(memory_id, 120)
        now = utc_now()
        review_status = (
            "auto"
            if clean_text(status, 24).lower() in {"approve", "approved", "auto"}
            else "rejected"
        )
        lifecycle = "archived" if review_status == "rejected" else "stable_memory"
        with self._lock:  # noqa: SIM117
            with self._transaction_sync():
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                if row is None:
                    return False

                record = MemoryRecord.from_row(row)
                metadata = (
                    dict(record.metadata) if isinstance(record.metadata, dict) else {}
                )
                extractor = clean_text(metadata.get("extractor"), 40).lower()
                dimension = clean_text(metadata.get("profile_dimension"), 80).lower()
                is_rule_profile = bool(
                    record.memory_type in self.PROFILE_MEMORY_TYPES
                    and extractor in self.PROFILE_RULE_EXTRACTORS
                )
                if not is_rule_profile:
                    self._conn.execute(
                        "UPDATE memories SET review_status=?, lifecycle=?, validity_status=?, updated_at=? WHERE id=?",
                        (
                            review_status,
                            lifecycle,
                            "archived" if lifecycle == "archived" else "active",
                            now,
                            memory_id,
                        ),
                    )
                    self._conn.execute(
                        "UPDATE review_queue SET status=?, updated_at=? WHERE memory_id=?",
                        (review_status, now, memory_id),
                    )
                    return True

                if review_status == "rejected":
                    metadata["profile_state"] = "rejected"
                    metadata["profile_status"] = "rejected"
                    metadata["profile_archive_reason"] = "manual_rejected"
                    metadata["profile_status_updated_at"] = now
                    metadata["quality_gate_passed"] = False
                    record.metadata = metadata
                    record.lifecycle = "archived"
                    record.validity_status = "archived"
                    record.review_status = "rejected"
                    record.updated_at = now
                    self._write_memory_record_sync(record)
                    self._conn.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=?", (record.id,)
                    )
                    self._conn.execute(
                        "UPDATE review_queue SET status='rejected', updated_at=? WHERE memory_id=?",
                        (now, record.id),
                    )
                    self._embedding_candidate_cache.clear()
                    self._embedding_candidate_cache_revision = ""
                    return True

                profile_value = clean_text(metadata.get("profile_value"), 240)
                normalized_value = normalize_profile_value(
                    metadata.get("normalized_value")
                )
                if (
                    not dimension
                    or not profile_value
                    or not normalized_value
                    or normalize_profile_value(profile_value) != normalized_value
                ):
                    return False

                domain_rows = self._profile_domain_rows_sync(record)
                try:
                    quality_score = float(
                        metadata.get("extraction_quality_score") or 0.0
                    )
                except (TypeError, ValueError):
                    quality_score = 0.0
                if not math.isfinite(quality_score):
                    quality_score = 0.0
                metadata["profile_state"] = "active"
                metadata["profile_status"] = "active"
                metadata["extraction_quality"] = "confirmed"
                metadata["extraction_quality_score"] = round(
                    max(0.95, min(1.0, quality_score)), 4
                )
                metadata["evidence_strength"] = "user_confirmed"
                metadata["quality_gate_passed"] = True
                metadata["required_evidence_count"] = 1
                metadata["profile_confirmation_source"] = "manual_review"
                metadata["profile_status_updated_at"] = now
                metadata["profile_cardinality"] = (
                    "single"
                    if dimension in self.PROFILE_SINGLE_VALUE_DIMENSIONS
                    else clean_text(metadata.get("profile_cardinality"), 20).lower()
                    or "multi"
                )
                record.metadata = metadata
                record.confidence = max(float(record.confidence or 0.0), 0.95)
                record.lifecycle = "stable_memory"
                record.validity_status = "active"
                record.review_status = "auto"
                record.updated_at = now
                self._write_memory_record_sync(record)

                cardinality = metadata["profile_cardinality"]
                if cardinality == "single":
                    for domain_row in domain_rows:
                        other = MemoryRecord.from_row(domain_row)
                        if (
                            other.id == record.id
                            or self._profile_state(other) != "active"
                        ):
                            continue
                        other_metadata = (
                            dict(other.metadata)
                            if isinstance(other.metadata, dict)
                            else {}
                        )
                        if (
                            clean_text(
                                other_metadata.get("profile_dimension"), 80
                            ).lower()
                            != dimension
                        ):
                            continue
                        other_metadata["profile_state"] = "superseded"
                        other_metadata["profile_status"] = "superseded"
                        other_metadata["profile_superseded_by"] = record.id
                        other_metadata["profile_status_updated_at"] = now
                        other.metadata = other_metadata
                        other.lifecycle = "archived"
                        other.validity_status = "superseded"
                        other.review_status = "auto"
                        other.supersedes_id = record.id
                        other.updated_at = now
                        self._write_memory_record_sync(other)
                        self._conn.execute(
                            "DELETE FROM memory_embeddings WHERE memory_id=?",
                            (other.id,),
                        )
                        self._conn.execute(
                            "UPDATE review_queue SET status='superseded', updated_at=? WHERE memory_id=?",
                            (now, other.id),
                        )

                self._conn.execute(
                    "UPDATE review_queue SET status='auto', updated_at=? WHERE memory_id=?",
                    (now, record.id),
                )
                self._embedding_candidate_cache.clear()
                self._embedding_candidate_cache_revision = ""
                return True
