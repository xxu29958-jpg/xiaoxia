from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    stable_fingerprint,
)
from .profile_quality import normalize_profile_value, profile_quality_decision
from .store_support import (
    _memory_time_sql,
)


class MemoryStoreProfileEvidence:
    """store profile evidence; the facade owns all shared resources."""

    @staticmethod
    def profile_memory_fingerprint(record: MemoryRecord) -> str:
        """Fingerprint fields governed by profile repair, excluding access counters."""
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        payload = {
            "id": record.id,
            "memory_type": record.memory_type,
            "subject": {
                "kind": record.subject.kind,
                "id": record.subject.id,
                "name": record.subject.name,
                "role": record.subject.role,
            },
            "object": {
                "kind": record.object.kind,
                "id": record.object.id,
                "name": record.object.name,
                "role": record.object.role,
            },
            "scope": record.scope,
            "session_id": record.session_id,
            "platform": record.platform,
            "message_id": record.message_id,
            "group_id": record.group_id,
            "visibility": record.visibility,
            "sayability": record.sayability,
            "reality_level": record.reality_level,
            "lifecycle": record.lifecycle,
            "content": record.content,
            "evidence": record.evidence,
            "confidence": record.confidence,
            "importance": record.importance,
            "review_status": record.review_status,
            "tags": list(record.tags or []),
            "metadata": metadata,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "occurred_at": record.occurred_at,
            "source_plugin": record.source_plugin,
            "import_batch_id": record.import_batch_id,
            "content_fingerprint": record.content_fingerprint,
            "merged_count": record.merged_count,
            "supersedes_id": record.supersedes_id,
        }
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def profile_repair_plan_fingerprint(actions: list[dict[str, Any]]) -> str:
        """Return a deterministic fingerprint for an ordered repair action plan."""
        raw = json.dumps(
            actions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def profile_repair_group_id(record_id: Any) -> str:
        record_id = clean_text(record_id, 120)
        return (
            "profile_group_"
            + stable_fingerprint("profile_repair_group", record_id)[:24]
            if record_id
            else ""
        )

    @staticmethod
    def profile_portrait_fact_fingerprint(fact: Any) -> str:
        data = dict(fact) if fact is not None else {}
        fields = (
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
        payload = {field: data.get(field) for field in fields}
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def profile_portrait_queue_fingerprint(rows: Any) -> str:
        normalized = sorted(
            (dict(row) for row in (rows or [])),
            key=lambda item: (
                clean_text(item.get("queue_id"), 120),
                clean_text(item.get("evidence_hash"), 80),
            ),
        )
        raw = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _memory_db_snapshot(record: MemoryRecord) -> dict[str, Any]:
        return dict(record.to_db())

    @staticmethod
    def _memory_from_db_snapshot(snapshot: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord.from_row(snapshot)

    def _write_memory_record_sync(self, record: MemoryRecord) -> sqlite3.Row:
        record.ensure_defaults()
        data = record.to_db()
        columns = ", ".join(data.keys())
        placeholders = ", ".join(f":{key}" for key in data)
        updates = ", ".join(f"{key}=excluded.{key}" for key in data if key != "id")
        self._conn.execute(
            f"INSERT INTO memories ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            data,
        )
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (record.id,)
        ).fetchone()
        self._upsert_memory_fts_row(row)
        return row

    @staticmethod
    def _profile_evidence_refs(record: MemoryRecord) -> list[str]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        raw_refs: list[Any] = []
        for key in ("profile_evidence_refs", "source_memory_ids", "evidence_refs"):
            value = metadata.get(key)
            if isinstance(value, list):
                raw_refs.extend(value)
        source_memory_id = clean_text(metadata.get("source_memory_id"), 160)
        if source_memory_id:
            raw_refs.append(source_memory_id)
        elif not raw_refs and record.message_id:
            raw_refs.append(record.message_id)
        refs = [clean_text(value, 160) for value in raw_refs if clean_text(value, 160)]
        if not refs:
            refs = [
                "fallback:"
                + stable_fingerprint(
                    record.scope,
                    record.session_id,
                    record.subject.id,
                    record.evidence,
                )
            ]
        return list(dict.fromkeys(refs))[:256]

    @staticmethod
    def _profile_incoming_evidence_refs(record: MemoryRecord) -> list[str]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        source_memory_id = clean_text(metadata.get("source_memory_id"), 160)
        if source_memory_id:
            return [source_memory_id]
        message_id = clean_text(record.message_id, 160)
        if message_id:
            return [message_id]
        return [
            "fallback:"
            + stable_fingerprint(
                record.scope,
                record.session_id,
                record.subject.id,
                record.evidence,
            )
        ]

    @staticmethod
    def _profile_evidence_text(records: list[MemoryRecord]) -> str:
        parts: list[str] = []
        for record in records:
            for part in clean_text(record.evidence, 4000).split("\n---\n"):
                cleaned = clean_text(part, 1200)
                if cleaned and cleaned not in parts:
                    parts.append(cleaned)
        return clean_text("\n---\n".join(parts), 4000)

    @staticmethod
    def _profile_statement_fingerprints(record: MemoryRecord) -> list[str]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        raw = metadata.get("profile_statement_fingerprints")
        values = (
            [clean_text(item, 80) for item in raw if clean_text(item, 80)]
            if isinstance(raw, list)
            else []
        )
        if not values and clean_text(record.evidence, 4000):
            values.append(
                stable_fingerprint("profile_statement", record.evidence.casefold())
            )
        return list(dict.fromkeys(values))[:256]

    @staticmethod
    def _profile_incoming_statement_fingerprints(
        record: MemoryRecord,
    ) -> list[str]:
        evidence = clean_text(record.evidence, 4000)
        if not evidence:
            return []
        return [stable_fingerprint("profile_statement", evidence.casefold())]

    @staticmethod
    def _profile_evidence_passes_quality_gate(record: MemoryRecord) -> bool:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        metadata["profile_state"] = "active"
        metadata["profile_status"] = "active"
        metadata["independent_evidence_count"] = 2
        allowed, _reason = profile_quality_decision(
            {"memory_type": record.memory_type, "metadata": metadata},
            require_active=True,
        )
        return allowed

    @staticmethod
    def _profile_domain(record: MemoryRecord) -> tuple[str, ...]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        return (
            clean_text(record.platform, 80).lower(),
            clean_text(record.subject.kind, 40).lower(),
            clean_text(record.subject.id, 120),
            clean_text(record.object.kind, 40).lower(),
            clean_text(record.object.id, 120),
            clean_text(record.scope, 40).lower(),
            clean_text(record.group_id, 120),
            clean_text(record.visibility, 40).lower(),
            clean_text(metadata.get("owner_bot_id"), 120),
        )

    def _profile_domain_rows_sync(self, record: MemoryRecord) -> list[sqlite3.Row]:
        rows = self._conn.execute(
            """
            SELECT * FROM memories
            WHERE memory_type IN ('user_profile', 'user_preference', 'user_habit')
              AND platform=? AND subject_kind=? AND subject_id=?
              AND object_kind=? AND object_id=? AND scope=? AND group_id=? AND visibility=?
              AND lifecycle!='archived'
            """,
            (
                clean_text(record.platform, 80),
                clean_text(record.subject.kind, 40),
                clean_text(record.subject.id, 120),
                clean_text(record.object.kind, 40),
                clean_text(record.object.id, 120),
                clean_text(record.scope, 40),
                clean_text(record.group_id, 120),
                clean_text(record.visibility, 40),
            ),
        ).fetchall()
        domain = self._profile_domain(record)
        return [
            row
            for row in rows
            if self._profile_domain(MemoryRecord.from_row(row)) == domain
        ]

    def _profile_single_value_domain_conflict_sync(self, record: MemoryRecord) -> bool:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        extractor = clean_text(metadata.get("extractor"), 40).lower()
        dimension = clean_text(metadata.get("profile_dimension"), 80).lower()
        cardinality = (
            "single"
            if dimension in self.PROFILE_SINGLE_VALUE_DIMENSIONS
            else clean_text(metadata.get("profile_cardinality"), 20).lower() or "multi"
        )
        if not (
            record.memory_type in self.PROFILE_MEMORY_TYPES
            and extractor in self.PROFILE_RULE_EXTRACTORS
            and dimension
            and cardinality == "single"
            and self._profile_state(record) == "active"
        ):
            return False
        for domain_row in self._profile_domain_rows_sync(record):
            other = MemoryRecord.from_row(domain_row)
            other_metadata = other.metadata if isinstance(other.metadata, dict) else {}
            if (
                other.id != record.id
                and self._profile_state(other) == "active"
                and clean_text(other_metadata.get("profile_dimension"), 80).lower()
                == dimension
            ):
                return True
        return False

    def _profile_single_value_invariant_signature(
        self, record: MemoryRecord
    ) -> tuple[Any, ...]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        dimension = clean_text(metadata.get("profile_dimension"), 80).lower()
        cardinality = (
            "single"
            if dimension in self.PROFILE_SINGLE_VALUE_DIMENSIONS
            else clean_text(metadata.get("profile_cardinality"), 20).lower() or "multi"
        )
        return (
            clean_text(record.memory_type, 80),
            self._profile_domain(record),
            self._profile_state(record),
            clean_text(record.lifecycle, 40),
            clean_text(record.review_status, 40),
            clean_text(metadata.get("extractor"), 40).lower(),
            dimension,
            cardinality,
            normalize_profile_value(metadata.get("normalized_value")),
        )

    @staticmethod
    def _profile_state(record: MemoryRecord) -> str:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        state = clean_text(
            metadata.get("profile_state") or metadata.get("profile_status"),
            32,
        ).lower()
        if state in {"candidate", "active", "rejected", "superseded"}:
            return state
        if record.lifecycle == "archived":
            return "superseded" if record.supersedes_id else "rejected"
        if record.review_status == "pending":
            return "candidate"
        return "active"

    async def list_rule_profile_memories(
        self,
        *,
        user_id: str = "",
        scope: str = "",
        memory_types: list[str] | None = None,
        extractors: list[str] | None = None,
        include_archived: bool = True,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_rule_profile_memories_sync,
            user_id,
            scope,
            list(memory_types or []),
            list(extractors or []),
            include_archived,
            limit,
            offset,
        )

    def _list_rule_profile_memories_sync(
        self,
        user_id: str,
        scope: str,
        memory_types: list[str],
        extractors: list[str],
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[MemoryRecord]:
        requested_types = [clean_text(value, 80) for value in memory_types]
        invalid_types = sorted(
            {
                value or "<empty>"
                for value in requested_types
                if value not in self.PROFILE_MEMORY_TYPES
            }
        )
        if invalid_types:
            raise ValueError(
                "unsupported profile memory type filter: " + ", ".join(invalid_types)
            )
        selected_types = list(dict.fromkeys(requested_types)) or sorted(
            self.PROFILE_MEMORY_TYPES
        )

        requested_extractors = [clean_text(value, 40).lower() for value in extractors]
        invalid_extractors = sorted(
            {
                value or "<empty>"
                for value in requested_extractors
                if value not in self.PROFILE_RULE_EXTRACTORS
            }
        )
        if invalid_extractors:
            raise ValueError(
                "unsupported profile extractor filter: " + ", ".join(invalid_extractors)
            )
        selected_extractors = list(dict.fromkeys(requested_extractors)) or sorted(
            self.PROFILE_RULE_EXTRACTORS
        )
        type_marks = ",".join("?" for _ in selected_types)
        extractor_marks = ",".join("?" for _ in selected_extractors)
        where = [
            f"memory_type IN ({type_marks})",
            "json_valid(metadata)",
            f"LOWER(COALESCE(CAST(json_extract(metadata, '$.extractor') AS TEXT), '')) IN ({extractor_marks})",
        ]
        params: list[Any] = [*selected_types, *selected_extractors]
        if user_id:
            where.append("subject_id=?")
            params.append(clean_text(user_id, 120))
        if scope:
            where.append("scope=?")
            params.append(clean_text(scope, 40))
        if not include_archived:
            where.append("lifecycle!='archived'")
        safe_limit = max(1, min(100000, int(limit or 5000)))
        safe_offset = max(0, int(offset or 0))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM memories
                WHERE {" AND ".join(where)}
                ORDER BY subject_id, scope, {_memory_time_sql()}, created_at, id
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    async def list_rule_portrait_facts(
        self,
        *,
        person_id: str = "",
        source_scope: str = "",
        include_inactive: bool = True,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_rule_portrait_facts_sync,
            person_id,
            source_scope,
            include_inactive,
            limit,
            offset,
        )

    def _list_rule_portrait_facts_sync(
        self,
        person_id: str,
        source_scope: str,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        versions = sorted(self.PROFILE_RULE_PORTRAIT_VERSIONS)
        marks = ",".join("?" for _ in versions)
        where = [
            "producer_kind='rule_explicit'",
            f"producer_version IN ({marks})",
        ]
        params: list[Any] = [*versions]
        if person_id:
            where.append("person_id=?")
            params.append(clean_text(person_id, 80))
        if source_scope:
            where.append("source_scope=?")
            params.append(clean_text(source_scope, 80))
        if not include_inactive:
            where.append("status='active'")
        safe_limit = max(1, min(100000, int(limit or 5000)))
        safe_offset = max(0, int(offset or 0))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM portrait_facts
                WHERE {" AND ".join(where)}
                ORDER BY person_id, source_scope, dimension,
                         last_evidence_at, created_at, id
                LIMIT ? OFFSET ?
                """,
                [*params, safe_limit, safe_offset],
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                queue_rows = self._conn.execute(
                    """
                    SELECT * FROM portrait_learning_queue
                    WHERE fact_id=? ORDER BY queue_id
                    """,
                    (row["id"],),
                ).fetchall()
                item["queue_fingerprint"] = self.profile_portrait_queue_fingerprint(
                    queue_rows
                )
                results.append(item)
        return results
