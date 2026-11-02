from __future__ import annotations

import math
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .assertions import ASSERTION_MEMORY_TYPE
from .models import (
    MemoryRecord,
    clean_text,
    stable_fingerprint,
    utc_now,
)
from .profile_quality import normalize_profile_value, profile_quality_decision


@dataclass(frozen=True)
class ProfileSelection:
    """Named result of the select profile canonical phase."""

    domain_rows: list[Any]
    exact_records: list[MemoryRecord]
    canonical: MemoryRecord
    existing_metadata: dict[str, Any]
    existing_state: str


@dataclass(frozen=True)
class ProfileEvidence:
    """Named result of the merge profile evidence phase."""

    all_records: list[MemoryRecord]
    refs: list[str]
    statement_fingerprints: list[str]
    qualified_refs: list[str]
    qualified_statement_fingerprints: list[str]
    independent_evidence_count: int
    evidence_added: bool


class MemoryStoreProfileAssertions:
    """store profile assertions; the facade owns all shared resources."""

    async def upsert_assertion(self, record: MemoryRecord) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._upsert_assertion_sync,
            deepcopy(record),
        )

    def _upsert_assertion_sync(self, record: MemoryRecord) -> dict[str, Any]:
        """Merge one assertion into the canonical record for its claim slot.

        Assertions are ordinary ``memories`` rows so recall, injection, ACL,
        decay and the panel all apply to them without a second runtime.  What
        this adds is the merge: the same claim extracted from ten conversations
        must become one row with ten sources, not ten rows that compete for the
        same injection slot.

        Merging keys on (domain, dimension, polarity, normalized value).  A
        single-value dimension also supersedes the previous value, because
        "now lives in Beijing" really does replace "lived in Shanghai"; a
        multi-value dimension never does, because two different habits are not
        two versions of one habit.  A polarity flip on the same wording is
        treated as a correction rather than a contradiction, and the older row
        is kept with a pointer to its successor so past-tense questions can
        still be answered.
        """
        record.ensure_defaults()
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        dimension = clean_text(metadata.get("profile_dimension"), 80).lower()
        polarity = (
            clean_text(metadata.get("profile_polarity"), 40).lower() or "positive"
        )
        normalized = normalize_profile_value(metadata.get("normalized_value"))
        profile_value = clean_text(metadata.get("profile_value"), 240)
        if (
            clean_text(record.memory_type, 80).lower() != ASSERTION_MEMORY_TYPE
            or not dimension
            or not profile_value
            or not normalized
            or normalize_profile_value(profile_value) != normalized
        ):
            return {"ok": False, "code": "assertion_invalid", "memory_id": ""}

        now = utc_now()
        incoming_refs = [
            clean_text(item, 160)
            for item in (metadata.get("assertion_evidence_refs") or [])
            if clean_text(item, 160)
        ][:16]
        cardinality = (
            clean_text(metadata.get("profile_cardinality"), 20).lower() or "multi"
        )
        with self._lock:
            with self._transaction_sync():
                domain_rows = self._assertion_domain_rows_sync(record)
                exact: list[MemoryRecord] = []
                same_dimension: list[MemoryRecord] = []
                corrected: list[MemoryRecord] = []
                for row in domain_rows:
                    candidate = MemoryRecord.from_row(row)
                    if (
                        candidate.lifecycle == "archived"
                        or candidate.validity_status != "active"
                    ):
                        continue
                    candidate_metadata = (
                        candidate.metadata
                        if isinstance(candidate.metadata, dict)
                        else {}
                    )
                    candidate_dimension = clean_text(
                        candidate_metadata.get("profile_dimension"), 80
                    ).lower()
                    if candidate_dimension != dimension:
                        continue
                    if (
                        clean_text(
                            candidate_metadata.get("profile_polarity"), 40
                        ).lower()
                        != polarity
                    ):
                        # Same dimension, same value, opposite polarity: the
                        # claim was corrected rather than duplicated.
                        if (
                            normalize_profile_value(
                                candidate_metadata.get("normalized_value")
                            )
                            == normalized
                        ):
                            corrected.append(candidate)
                        continue
                    same_dimension.append(candidate)
                    if (
                        normalize_profile_value(
                            candidate_metadata.get("normalized_value")
                        )
                        == normalized
                    ):
                        exact.append(candidate)

                canonical = exact[0] if exact else record
                incoming_stable = (
                    record.lifecycle == "stable_memory"
                    and record.review_status != "pending"
                )
                if exact and incoming_stable:
                    canonical.lifecycle = record.lifecycle
                    canonical.review_status = record.review_status
                    canonical.content = record.content
                    canonical.sayability = record.sayability
                merged_metadata = dict(
                    canonical.metadata if isinstance(canonical.metadata, dict) else {}
                )
                merged_refs = [
                    clean_text(item, 160)
                    for item in (
                        merged_metadata.get("assertion_evidence_refs")
                        if isinstance(
                            merged_metadata.get("assertion_evidence_refs"), list
                        )
                        else []
                    )
                    if clean_text(item, 160)
                ]
                merged_refs = list(dict.fromkeys([*merged_refs, *incoming_refs]))[:32]
                if (
                    exact
                    and not incoming_stable
                    and canonical.review_status != "pending"
                ):
                    # An uncertain repeat may add provenance without demoting a
                    # previously supported fact or replacing its active state.
                    metadata = {
                        key: value
                        for key, value in metadata.items()
                        if key
                        not in {
                            "profile_state",
                            "assertion_durability",
                            "extraction_quality_score",
                        }
                    }
                merged_metadata.update(metadata)
                merged_metadata["assertion_evidence_refs"] = merged_refs
                merged_metadata["assertion_evidence_count"] = len(merged_refs)
                merged_metadata["profile_dimension"] = dimension
                merged_metadata["profile_polarity"] = polarity
                merged_metadata["normalized_value"] = normalized
                merged_metadata["profile_cardinality"] = cardinality
                merged_metadata["extraction_quality_score"] = round(
                    max(
                        float(merged_metadata.get("extraction_quality_score") or 0.0),
                        float(metadata.get("extraction_quality_score") or 0.0),
                    ),
                    4,
                )
                if incoming_refs:
                    merged_metadata["assertion_first_seen_at"] = (
                        clean_text(
                            merged_metadata.get("assertion_first_seen_at")
                            or record.created_at,
                            80,
                        )
                        or now
                    )
                    merged_metadata["assertion_last_seen_at"] = now
                canonical.metadata = merged_metadata
                canonical.confidence = max(
                    float(canonical.confidence or 0.0), float(record.confidence or 0.0)
                )
                canonical.importance = max(
                    float(canonical.importance or 0.0), float(record.importance or 0.0)
                )
                canonical.merged_count = max(
                    1, int(canonical.merged_count or 1) + (1 if exact else 0)
                )
                canonical.updated_at = now
                canonical.content_fingerprint = ""
                canonical.ensure_defaults()
                self._write_memory_record_sync(canonical)

                superseded: list[str] = []
                if incoming_stable:
                    replaced = same_dimension if cardinality == "single" else []
                    for other in [*replaced, *corrected]:
                        if other.id == canonical.id:
                            continue
                        other_metadata = (
                            dict(other.metadata)
                            if isinstance(other.metadata, dict)
                            else {}
                        )
                        other_metadata["profile_state"] = "superseded"
                        other_metadata["assertion_superseded_by"] = canonical.id
                        other_metadata["assertion_superseded_at"] = now
                        other.metadata = other_metadata
                        other.lifecycle = "archived"
                        other.validity_status = "superseded"
                        other.supersedes_id = canonical.id
                        other.updated_at = now
                        self._write_memory_record_sync(other)
                        self._conn.execute(
                            "DELETE FROM memory_embeddings WHERE memory_id=?",
                            (other.id,),
                        )
                        self._conn.execute(
                            "UPDATE review_queue SET status='superseded', updated_at=? "
                            "WHERE memory_id=? AND status='pending'",
                            (now, other.id),
                        )
                        superseded.append(other.id)
        self._embedding_candidate_cache.clear()
        self._embedding_candidate_cache_revision = ""
        return {
            "ok": True,
            "memory_id": canonical.id,
            "created": not exact,
            "merged_sources": len(merged_refs),
            "superseded": superseded,
        }

    def _assertion_domain_rows_sync(self, record: MemoryRecord) -> list[sqlite3.Row]:
        """Rows sharing the assertion's owner/scope domain.

        ``_profile_domain`` reads the owner from metadata, which nothing keeps in
        sync with the column: reusing it made every lookup miss and turned each
        re-extraction into a duplicate row.
        """
        domain = list(self._profile_domain(record))
        domain[-1] = clean_text(record.owner_bot_id, 120)
        return self._conn.execute(
            """
            SELECT * FROM memories
            WHERE memory_type=?
              AND platform=? AND subject_kind=? AND subject_id=?
              AND object_kind=? AND object_id=? AND scope=? AND group_id=? AND visibility=?
              AND owner_bot_id=?
            """,
            (ASSERTION_MEMORY_TYPE, *domain),
        ).fetchall()

    async def upsert_profile_candidate(self, record: MemoryRecord) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._upsert_profile_candidate_sync,
            deepcopy(record),
        )

    def _upsert_profile_candidate_sync(self, record: MemoryRecord) -> dict[str, Any]:
        record.ensure_defaults()
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        extractor = clean_text(metadata.get("extractor"), 40).lower()
        dimension = clean_text(metadata.get("profile_dimension"), 80).lower()
        profile_value = clean_text(metadata.get("profile_value"), 240)
        normalized = normalize_profile_value(metadata.get("normalized_value"))
        polarity = clean_text(metadata.get("profile_polarity"), 40).lower()
        if (
            record.memory_type not in self.PROFILE_MEMORY_TYPES
            or extractor not in self.PROFILE_RULE_EXTRACTORS
            or not dimension
            or not profile_value
            or not normalized
            or normalize_profile_value(profile_value) != normalized
        ):
            return {"ok": False, "code": "profile_candidate_invalid", "memory_id": ""}
        try:
            raw_quality_score = float(metadata.get("extraction_quality_score"))
        except (TypeError, ValueError):
            return {"ok": False, "code": "profile_candidate_invalid", "memory_id": ""}
        if not math.isfinite(raw_quality_score):
            return {"ok": False, "code": "profile_candidate_invalid", "memory_id": ""}
        quality_score = max(0.0, min(1.0, raw_quality_score))

        requested_state = clean_text(
            metadata.get("profile_state") or metadata.get("profile_status"),
            32,
        ).lower()
        explicit_active = bool(
            requested_state == "active"
            and clean_text(metadata.get("extraction_quality"), 40).lower() == "explicit"
            and clean_text(metadata.get("evidence_strength"), 40).lower()
            == "direct_statement"
            and metadata.get("quality_gate_passed") is True
            and quality_score >= 0.8
        )
        requested_state = "active" if explicit_active else "candidate"
        try:
            required_evidence = max(
                1 if explicit_active else 2,
                min(10, int(metadata.get("required_evidence_count") or 2)),
            )
        except (TypeError, ValueError):
            required_evidence = 1 if explicit_active else 2
        incoming_refs = self._profile_incoming_evidence_refs(record)
        incoming_statement_fingerprints = self._profile_incoming_statement_fingerprints(
            record
        )
        now = utc_now()

        with self._lock:  # noqa: SIM117
            with self._transaction_sync():
                selection = self._select_profile_canonical(
                    dimension=dimension,
                    incoming_refs=incoming_refs,
                    incoming_statement_fingerprints=incoming_statement_fingerprints,
                    normalized=normalized,
                    polarity=polarity,
                    record=record,
                )

                evidence = self._merge_profile_evidence(
                    exact_records=selection.exact_records,
                    incoming_refs=incoming_refs,
                    incoming_statement_fingerprints=incoming_statement_fingerprints,
                    record=record,
                )

                merged_metadata = dict(selection.existing_metadata)
                merged_metadata.update(metadata)
                merged_metadata["extractor"] = extractor
                merged_metadata["profile_dimension"] = dimension
                merged_metadata["profile_polarity"] = polarity
                merged_metadata["normalized_value"] = clean_text(
                    metadata.get("normalized_value"), 240
                )
                merged_metadata["profile_value"] = profile_value
                try:
                    existing_quality_score = float(
                        selection.existing_metadata.get("extraction_quality_score")
                        or 0.0
                    )
                except (TypeError, ValueError):
                    existing_quality_score = 0.0
                merged_metadata["extraction_quality_score"] = round(
                    max(quality_score, existing_quality_score),
                    4,
                )
                merged_metadata["profile_evidence_refs"] = evidence.refs
                merged_metadata["profile_statement_fingerprints"] = (
                    evidence.statement_fingerprints
                )
                merged_metadata["profile_qualified_evidence_refs"] = (
                    evidence.qualified_refs
                )
                merged_metadata["profile_qualified_statement_fingerprints"] = (
                    evidence.qualified_statement_fingerprints
                )
                merged_metadata["evidence_count"] = len(evidence.refs)
                merged_metadata["independent_evidence_count"] = (
                    evidence.independent_evidence_count
                )
                merged_metadata["required_evidence_count"] = required_evidence
                merged_metadata["profile_cardinality"] = (
                    "single"
                    if dimension in self.PROFILE_SINGLE_VALUE_DIMENSIONS
                    else clean_text(metadata.get("profile_cardinality"), 20) or "multi"
                )

                activation_metadata = {
                    **merged_metadata,
                    "profile_state": "active",
                    "profile_status": "active",
                }
                activation_allowed, _activation_reason = profile_quality_decision(
                    activation_metadata,
                    require_active=True,
                )

                next_state = (
                    "active"
                    if (
                        selection.existing_state == "active"
                        or activation_allowed
                        and (
                            requested_state == "active"
                            or evidence.independent_evidence_count >= required_evidence
                        )
                    )
                    else "candidate"
                )
                merged_metadata["profile_state"] = next_state
                merged_metadata["profile_status"] = next_state
                merged_metadata["profile_status_updated_at"] = now
                selection.canonical.metadata = merged_metadata
                if not selection.exact_records or requested_state == "active":
                    selection.canonical.content = record.content
                    selection.canonical.memory_type = record.memory_type
                    selection.canonical.tags = list(
                        dict.fromkeys(
                            [*(selection.canonical.tags or []), *(record.tags or [])]
                        )
                    )
                selection.canonical.evidence = self._profile_evidence_text(
                    evidence.all_records
                )
                selection.canonical.confidence = max(
                    float(selection.canonical.confidence or 0.0), quality_score
                )
                selection.canonical.importance = max(
                    float(selection.canonical.importance or 0.0),
                    float(record.importance or 0.0),
                )
                selection.canonical.merged_count = max(1, len(evidence.refs))
                selection.canonical.lifecycle = (
                    "stable_memory" if next_state == "active" else "raw_event"
                )
                selection.canonical.review_status = (
                    "auto" if next_state == "active" else "pending"
                )
                selection.canonical.updated_at = now
                selection.canonical.content_fingerprint = ""
                selection.canonical.ensure_defaults()
                self._write_memory_record_sync(selection.canonical)

                superseded_ids = self._supersede_profile_candidates(
                    canonical=selection.canonical,
                    dimension=dimension,
                    domain_rows=selection.domain_rows,
                    exact_records=selection.exact_records,
                    merged_metadata=merged_metadata,
                    next_state=next_state,
                    now=now,
                )

                if next_state == "candidate":
                    self._upsert_review_sync(
                        selection.canonical.id, "profile_candidate_requires_evidence"
                    )
                    self._conn.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=?",
                        (selection.canonical.id,),
                    )
                else:
                    self._conn.execute(
                        "UPDATE review_queue SET status='auto', updated_at=? WHERE memory_id=? AND status='pending'",
                        (now, selection.canonical.id),
                    )
                self._embedding_candidate_cache.clear()
                self._embedding_candidate_cache_revision = ""

        return {
            "ok": True,
            "code": "profile_candidate_upserted",
            "memory_id": selection.canonical.id,
            "profile_status": next_state,
            "evidence_count": len(evidence.refs),
            "independent_evidence_count": evidence.independent_evidence_count,
            "evidence_added": evidence.evidence_added,
            "superseded_ids": superseded_ids,
        }

    def _supersede_profile_candidates(
        self,
        *,
        canonical,
        dimension,
        domain_rows,
        exact_records,
        merged_metadata,
        next_state,
        now,
    ):
        """Supersede profile candidates."""
        superseded_ids: list[str] = []
        duplicate_ids = [item.id for item in exact_records[1:]]
        cardinality = clean_text(merged_metadata.get("profile_cardinality"), 20).lower()
        if next_state == "active" and cardinality == "single":
            for row in domain_rows:
                other = MemoryRecord.from_row(row)
                other_metadata = (
                    other.metadata if isinstance(other.metadata, dict) else {}
                )
                if other.id == canonical.id:
                    continue
                if (
                    clean_text(other_metadata.get("profile_dimension"), 80).lower()
                    != dimension
                ):
                    continue
                if self._profile_state(other) == "active":
                    duplicate_ids.append(other.id)
        for memory_id in list(dict.fromkeys(duplicate_ids)):
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            if row is None:
                continue
            other = MemoryRecord.from_row(row)
            other_metadata = (
                dict(other.metadata) if isinstance(other.metadata, dict) else {}
            )
            other_metadata["profile_state"] = "superseded"
            other_metadata["profile_status"] = "superseded"
            other_metadata["profile_superseded_by"] = canonical.id
            other_metadata["profile_status_updated_at"] = now
            other.metadata = other_metadata
            other.lifecycle = "archived"
            other.supersedes_id = canonical.id
            other.updated_at = now
            self._write_memory_record_sync(other)
            self._conn.execute(
                "DELETE FROM memory_embeddings WHERE memory_id=?", (other.id,)
            )
            self._conn.execute(
                "UPDATE review_queue SET status='superseded', updated_at=? WHERE memory_id=?",
                (now, other.id),
            )
            superseded_ids.append(other.id)

        return superseded_ids

    def _merge_profile_evidence(
        self, *, exact_records, incoming_refs, incoming_statement_fingerprints, record
    ):
        """Merge profile evidence."""
        all_records = [*exact_records, record]
        refs: list[str] = []
        statement_fingerprints: list[str] = []
        for item in exact_records:
            refs.extend(self._profile_evidence_refs(item))
            statement_fingerprints.extend(self._profile_statement_fingerprints(item))
        refs.extend(incoming_refs)
        statement_fingerprints.extend(incoming_statement_fingerprints)
        refs = list(dict.fromkeys(refs))[:256]
        statement_fingerprints = list(dict.fromkeys(statement_fingerprints))[:256]
        qualified_refs: list[str] = []
        qualified_statement_fingerprints: list[str] = []
        for item in exact_records:
            item_metadata = item.metadata if isinstance(item.metadata, dict) else {}
            stored_refs = item_metadata.get("profile_qualified_evidence_refs")
            stored_statements = item_metadata.get(
                "profile_qualified_statement_fingerprints"
            )
            if isinstance(stored_refs, list) and isinstance(stored_statements, list):
                qualified_refs.extend(stored_refs)
                qualified_statement_fingerprints.extend(stored_statements)
            elif self._profile_evidence_passes_quality_gate(item):
                qualified_refs.extend(self._profile_evidence_refs(item))
                qualified_statement_fingerprints.extend(
                    self._profile_statement_fingerprints(item)
                )
        if self._profile_evidence_passes_quality_gate(record):
            qualified_refs.extend(incoming_refs)
            qualified_statement_fingerprints.extend(incoming_statement_fingerprints)
        qualified_refs = list(dict.fromkeys(qualified_refs))[:256]
        qualified_statement_fingerprints = list(
            dict.fromkeys(qualified_statement_fingerprints)
        )[:256]
        independent_evidence_count = min(
            len(qualified_refs),
            len(qualified_statement_fingerprints),
        )
        existing_refs: list[str] = []
        for item in exact_records:
            existing_refs.extend(self._profile_evidence_refs(item))
        existing_refs = list(dict.fromkeys(existing_refs))
        evidence_added = any(ref not in existing_refs for ref in incoming_refs)

        return ProfileEvidence(
            all_records=all_records,
            refs=refs,
            statement_fingerprints=statement_fingerprints,
            qualified_refs=qualified_refs,
            qualified_statement_fingerprints=qualified_statement_fingerprints,
            independent_evidence_count=independent_evidence_count,
            evidence_added=evidence_added,
        )

    def _select_profile_canonical(
        self,
        *,
        dimension,
        incoming_refs,
        incoming_statement_fingerprints,
        normalized,
        polarity,
        record,
    ):
        """Select profile canonical."""
        domain_rows = self._profile_domain_rows_sync(record)
        exact_records: list[MemoryRecord] = []
        for row in domain_rows:
            candidate = MemoryRecord.from_row(row)
            candidate_metadata = (
                candidate.metadata if isinstance(candidate.metadata, dict) else {}
            )
            if (
                clean_text(candidate_metadata.get("profile_dimension"), 80).lower()
                != dimension
            ):
                continue
            candidate_polarity = clean_text(
                candidate_metadata.get("profile_polarity"),
                40,
            ).lower()
            if (
                normalize_profile_value(candidate_metadata.get("normalized_value"))
                == normalized
                and candidate_polarity == polarity
            ):
                exact_records.append(candidate)

        exact_records.sort(
            key=lambda item: (
                self._profile_state(item) == "active",
                int(item.merged_count or 1),
                item.updated_at,
            ),
            reverse=True,
        )
        if exact_records:
            canonical = exact_records[0]
            existing_metadata = (
                dict(canonical.metadata) if isinstance(canonical.metadata, dict) else {}
            )
            existing_state = self._profile_state(canonical)
        else:
            canonical = deepcopy(record)
            profile_key = stable_fingerprint(
                *self._profile_domain(record),
                dimension,
                polarity,
                normalized,
            )
            canonical.id = f"profile_{profile_key}"
            if self._conn.execute(
                "SELECT 1 FROM memories WHERE id=?", (canonical.id,)
            ).fetchone():
                collision_seed = stable_fingerprint(
                    profile_key,
                    record.id,
                    *incoming_refs,
                    *incoming_statement_fingerprints,
                )
                sequence = 1
                while True:
                    candidate_id = (
                        f"profile_{stable_fingerprint(collision_seed, str(sequence))}"
                    )
                    if (
                        self._conn.execute(
                            "SELECT 1 FROM memories WHERE id=?", (candidate_id,)
                        ).fetchone()
                        is None
                    ):
                        canonical.id = candidate_id
                        break
                    sequence += 1
            existing_metadata = {}
            existing_state = "candidate"

        return ProfileSelection(
            domain_rows=domain_rows,
            exact_records=exact_records,
            canonical=canonical,
            existing_metadata=existing_metadata,
            existing_state=existing_state,
        )
