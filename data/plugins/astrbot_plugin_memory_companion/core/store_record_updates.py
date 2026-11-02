from __future__ import annotations

import asyncio
from typing import Any

from .memory_atom import (
    DURABILITY_LEVELS,
    SENSITIVITY_LEVELS,
    clamp_score,
    normalize_durability,
    normalize_sensitivity,
    normalize_validity_status,
)
from .memory_revision import (
    MemoryRevisionError,
    correction_owner,
    correction_scope,
    current_memory,
    digest,
    memory_ref,
    revised_memory,
)
from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value


class MemoryStoreRecordUpdates:
    """store record updates; the facade owns all shared resources."""

    async def update_memory_owner_bot(self, memory_id: str, owner_bot_id: str) -> bool:
        """Update the Memory Atom owner and its mirrored metadata field."""
        return await self._run_recoverable_database_operation(
            self._update_memory_owner_bot_sync,
            memory_id,
            owner_bot_id,
        )

    def _update_memory_owner_bot_sync(self, memory_id: str, owner_bot_id: str) -> bool:
        memory_id = clean_text(memory_id, 120)
        owner_bot_id = clean_text(owner_bot_id, 120)
        if (
            not memory_id
            or not owner_bot_id
            or owner_bot_id.casefold() in {"self", "bot", "bot_self"}
        ):
            return False
        with self._lock:
            with self._transaction_sync():
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                if not row:
                    return False
                metadata = json_loads(row["metadata"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["owner_bot_id"] = owner_bot_id
                atom = self._memory_atom_record_for_row(
                    row,
                    metadata=json_dumps(metadata),
                    owner_bot_id=owner_bot_id,
                )
                cur = self._conn.execute(
                    """
                    UPDATE memories
                    SET owner_bot_id=?, metadata=?, canonical_key=?, content_fingerprint=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        owner_bot_id,
                        json_dumps(metadata),
                        atom.canonical_key,
                        atom.content_fingerprint,
                        utc_now(),
                        memory_id,
                    ),
                )
                refreshed = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                self._upsert_memory_fts_row(refreshed)
                return cur.rowcount > 0

    async def correct_user_memory(
        self,
        ctx,
        *,
        action="correct",
        memory_id="",
        expected_version="",
        correction_id="",
        content="",
        profile_value="",
        profile_polarity="",
        trace_id="",
    ):
        return await self._run_recoverable_database_operation(
            self._correct_user_memory_sync,
            ctx,
            action,
            memory_id,
            expected_version,
            correction_id,
            content,
            profile_value,
            profile_polarity,
            trace_id,
        )

    def _correct_user_memory_sync(
        self,
        ctx,
        action,
        memory_id,
        expected_version,
        correction_id,
        content,
        profile_value,
        profile_polarity,
        trace_id,
    ):
        if (
            action not in {"correct", "lookup"}
            or not correction_id
            or len(correction_id) > 120
        ):
            raise MemoryRevisionError("invalid_correction_request")
        scope_ref = correction_scope(ctx)
        request_hash = digest(
            [
                memory_id,
                expected_version,
                content,
                profile_value,
                profile_polarity,
                ctx.message_id,
                ctx.message_text,
            ]
        )
        with self._lock, self._transaction_sync():
            existing = self._conn.execute(
                "SELECT * FROM memory_correction_receipts WHERE scope_ref=? AND correction_id=?",
                (scope_ref, correction_id),
            ).fetchone()
            if existing:
                if action != "lookup" and existing["request_hash"] != request_hash:
                    raise MemoryRevisionError("idempotency_conflict")
                return {**json_loads(existing["receipt"], {}), "deduplicated": True}
            if action == "lookup":
                return {"ok": False, "reason_code": "correction_receipt_missing"}
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            old = MemoryRecord.from_row(row) if row else None
            if old is None or not correction_owner(ctx, old):
                raise MemoryRevisionError("memory_not_owned")
            if memory_ref(old)["version"] != expected_version or not current_memory(
                old
            ):
                raise MemoryRevisionError("memory_revision_conflict")
            correction_ref = "correction:" + digest([scope_ref, correction_id])
            replacement = revised_memory(
                old,
                ctx,
                content=content,
                profile_value=profile_value,
                profile_polarity=profile_polarity,
                correction_ref=correction_ref,
                new_id="mem_" + digest([scope_ref, correction_id])[:32],
            )
            old_ref = memory_ref(old)
            old.validity_status, old.lifecycle, old.updated_at = (
                "superseded",
                "archived",
                utc_now(),
            )
            old.metadata = {
                **old.metadata,
                "superseded_by": replacement.id,
                "correction_ref": correction_ref,
            }
            if old.memory_type in self.PROFILE_MEMORY_TYPES:
                old.metadata.update(
                    profile_state="superseded", profile_status="superseded"
                )
            self._write_memory_record_sync(old)
            if self._profile_single_value_domain_conflict_sync(replacement):
                raise MemoryRevisionError("profile_revision_conflict")
            self._write_memory_record_sync(replacement)
            receipt = {
                "ok": True,
                "status": "succeeded",
                "effect_state": "committed",
                "deduplicated": False,
                "correction_id": correction_id,
                "correction_ref": correction_ref,
                "trace_id": trace_id,
                "old_ref": old_ref,
                "new_ref": memory_ref(replacement),
                "committed_at": replacement.created_at,
                "source_message_id": ctx.message_id,
            }
            self._conn.execute(
                "INSERT INTO memory_correction_receipts(scope_ref, correction_id, request_hash, receipt) VALUES(?,?,?,?)",
                (scope_ref, correction_id, request_hash, json_dumps(receipt)),
            )
            return receipt

    async def update_memory_payload(
        self,
        memory_id: str,
        *,
        memory_type: str | None = None,
        content: str | None = None,
        evidence: str | None = None,
        importance: Any | None = None,
        confidence: Any | None = None,
        visibility: str | None = None,
        lifecycle: str | None = None,
        review_status: str | None = None,
        metadata: dict[str, Any] | None = None,
        validity_status: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        salience: Any | None = None,
        durability: str | None = None,
        sensitivity: str | None = None,
    ) -> bool:
        return await self._run_recoverable_database_operation(
            self._update_memory_payload_sync,
            memory_id,
            memory_type,
            content,
            evidence,
            importance,
            confidence,
            visibility,
            lifecycle,
            review_status,
            metadata,
            validity_status,
            valid_from,
            valid_to,
            salience,
            durability,
            sensitivity,
        )

    def _update_memory_payload_sync(
        self,
        memory_id: str,
        memory_type: str | None,
        content: str | None,
        evidence: str | None,
        importance: Any | None,
        confidence: Any | None,
        visibility: str | None,
        lifecycle: str | None,
        review_status: str | None,
        metadata: dict[str, Any] | None,
        validity_status: str | None,
        valid_from: str | None,
        valid_to: str | None,
        salience: Any | None,
        durability: str | None,
        sensitivity: str | None,
    ) -> bool:
        memory_id = clean_text(memory_id, 120)
        with self._lock:
            with self._transaction_sync():
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                if not row:
                    return False
                next_type = (
                    clean_text(
                        memory_type if memory_type is not None else row["memory_type"],
                        80,
                    )
                    or row["memory_type"]
                )
                next_content = clean_text(
                    redact_sensitive_text(
                        content if content is not None else row["content"]
                    ),
                    4000,
                )
                next_evidence = clean_text(
                    redact_sensitive_text(
                        evidence if evidence is not None else row["evidence"]
                    ),
                    4000,
                )
                next_visibility = (
                    clean_text(
                        visibility if visibility is not None else row["visibility"], 40
                    )
                    or row["visibility"]
                )
                next_lifecycle = (
                    clean_text(
                        lifecycle if lifecycle is not None else row["lifecycle"], 40
                    )
                    or row["lifecycle"]
                )
                next_review_status = (
                    clean_text(
                        review_status
                        if review_status is not None
                        else row["review_status"],
                        40,
                    )
                    or row["review_status"]
                )
                next_metadata = (
                    metadata
                    if isinstance(metadata, dict)
                    else json_loads(row["metadata"], {})
                )
                if not isinstance(next_metadata, dict):
                    next_metadata = {}
                next_metadata = redact_sensitive_value(next_metadata)
                prospective = MemoryRecord.from_row(row)
                prospective.memory_type = next_type
                prospective.visibility = next_visibility
                prospective.lifecycle = next_lifecycle
                prospective.review_status = next_review_status
                prospective.metadata = dict(next_metadata)
                current = MemoryRecord.from_row(row)
                if self._profile_single_value_invariant_signature(
                    current
                ) != self._profile_single_value_invariant_signature(
                    prospective
                ) and self._profile_single_value_domain_conflict_sync(prospective):
                    return False
                try:
                    next_importance = max(
                        0.0,
                        min(
                            1.0,
                            float(
                                importance
                                if importance is not None
                                else row["importance"]
                            ),
                        ),
                    )
                except Exception:
                    next_importance = float(row["importance"] or 0.3)
                try:
                    next_confidence = max(
                        0.0,
                        min(
                            1.0,
                            float(
                                confidence
                                if confidence is not None
                                else row["confidence"]
                            ),
                        ),
                    )
                except Exception:
                    next_confidence = float(row["confidence"] or 0.5)
                current_validity = normalize_validity_status(row["validity_status"])
                if validity_status is None:
                    next_validity = current_validity
                    if lifecycle is not None and next_lifecycle == "archived":
                        next_validity = "archived"
                    elif (
                        lifecycle is not None
                        and current_validity == "archived"
                        and next_lifecycle != "archived"
                    ):
                        next_validity = "active"
                else:
                    requested_validity = clean_text(validity_status, 24).lower()
                    next_validity = normalize_validity_status(
                        requested_validity, current_validity
                    )
                if next_lifecycle == "archived" or next_validity == "archived":
                    next_lifecycle = "archived"
                    next_validity = "archived"
                next_valid_from = clean_text(
                    valid_from if valid_from is not None else row["valid_from"],
                    80,
                )
                next_valid_to = clean_text(
                    valid_to if valid_to is not None else row["valid_to"],
                    80,
                )
                next_salience = clamp_score(
                    salience if salience is not None else row["salience"],
                    float(row["salience"] or next_importance),
                )
                requested_durability = clean_text(
                    durability if durability is not None else row["durability"],
                    24,
                ).lower()
                next_durability = normalize_durability(
                    requested_durability,
                    clean_text(row["durability"], 24)
                    if row["durability"] in DURABILITY_LEVELS
                    else "normal",
                )
                requested_sensitivity = clean_text(
                    sensitivity if sensitivity is not None else row["sensitivity"],
                    24,
                ).lower()
                next_sensitivity = normalize_sensitivity(
                    requested_sensitivity,
                    clean_text(row["sensitivity"], 24)
                    if row["sensitivity"] in SENSITIVITY_LEVELS
                    else "private",
                )
                atom = self._memory_atom_record_for_row(
                    row,
                    reset_semantic_keys=memory_type is not None or content is not None,
                    memory_type=next_type,
                    content=next_content,
                    evidence=next_evidence,
                    visibility=next_visibility,
                    lifecycle=next_lifecycle,
                    review_status=next_review_status,
                    metadata=json_dumps(next_metadata),
                    importance=next_importance,
                    confidence=next_confidence,
                )
                cur = self._conn.execute(
                    """
                    UPDATE memories
                    SET memory_type=?,
                        content=?,
                        evidence=?,
                        importance=?,
                        confidence=?,
                        visibility=?,
                        lifecycle=?,
                        validity_status=?,
                        valid_from=?,
                        valid_to=?,
                        salience=?,
                        durability=?,
                        sensitivity=?,
                        review_status=?,
                        metadata=?,
                        canonical_key=?,
                        content_fingerprint=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        next_type,
                        next_content,
                        next_evidence,
                        next_importance,
                        next_confidence,
                        next_visibility,
                        next_lifecycle,
                        next_validity,
                        next_valid_from,
                        next_valid_to,
                        next_salience,
                        next_durability,
                        next_sensitivity,
                        next_review_status,
                        json_dumps(next_metadata),
                        atom.canonical_key,
                        atom.content_fingerprint,
                        utc_now(),
                        memory_id,
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                self._upsert_memory_fts_row(row)
                return cur.rowcount > 0

    async def update_memory_reaction_feedback(
        self,
        memory_id: str,
        *,
        reaction: str,
        evidence: str,
        source_id: str = "",
        mention_delta: float = 0.0,
        confidence_delta: float = 0.0,
        emotional_delta: float = 0.0,
    ) -> bool:
        return await asyncio.to_thread(
            self._update_memory_reaction_feedback_sync,
            memory_id,
            reaction,
            evidence,
            source_id,
            mention_delta,
            confidence_delta,
            emotional_delta,
        )

    def _update_memory_reaction_feedback_sync(
        self,
        memory_id: str,
        reaction: str,
        evidence: str,
        source_id: str,
        mention_delta: float,
        confidence_delta: float,
        emotional_delta: float,
    ) -> bool:
        memory_id = clean_text(memory_id, 120)
        reaction = clean_text(reaction, 60)
        evidence = clean_text(redact_sensitive_text(evidence), 500)
        with self._lock:
            row = self._conn.execute(
                "SELECT metadata, confidence FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            if not row:
                return False
            metadata = json_loads(row["metadata"], {})
            if not isinstance(metadata, dict):
                metadata = {}
            feedback = metadata.get("mention_feedback")
            if not isinstance(feedback, dict):
                feedback = {}
            source_id = clean_text(source_id, 120)
            applied_sources = feedback.get("applied_sources")
            if not isinstance(applied_sources, list):
                applied_sources = []
            if source_id and source_id in applied_sources:
                return False
            if source_id:
                applied_sources.append(source_id)
                feedback["applied_sources"] = applied_sources[-12:]
            count_key = f"{reaction}_count"
            try:
                feedback[count_key] = int(feedback.get(count_key) or 0) + 1
            except Exception:
                feedback[count_key] = 1
            now = utc_now()
            feedback["last_reaction"] = reaction
            feedback["last_reaction_at"] = now
            if evidence:
                feedback["last_evidence"] = evidence
            metadata["mention_feedback"] = feedback
            try:
                mentionability = float(metadata.get("mentionability_score", 0.5) or 0.5)
            except Exception:
                mentionability = 0.5
            mentionability = max(
                0.0, min(1.0, mentionability + float(mention_delta or 0.0))
            )
            metadata["mentionability_score"] = round(mentionability, 3)
            if reaction in {"awkward", "denied"} and mentionability <= 0.35:
                metadata["mention_policy"] = "avoid_unless_asked"
            elif (
                reaction in {"accepted", "comforted", "touched", "nostalgic"}
                and mentionability >= 0.62
            ):
                metadata["mention_policy"] = "soft_echo"
            if reaction == "corrected" and evidence:
                metadata["user_correction"] = {
                    "text": evidence,
                    "created_at": now,
                }
                metadata["mention_policy"] = "avoid_unless_asked"
            try:
                confidence = max(
                    0.0,
                    min(
                        1.0,
                        float(row["confidence"] or 0.5)
                        + float(confidence_delta or 0.0),
                    ),
                )
            except Exception:
                confidence = float(row["confidence"] or 0.5)
            if emotional_delta:
                try:
                    emotional = float(metadata.get("emotional_weight") or 0.0)
                except Exception:
                    emotional = 0.0
                metadata["emotional_weight"] = round(
                    max(0.0, min(1.0, emotional + float(emotional_delta or 0.0))), 3
                )
            cur = self._conn.execute(
                """
                UPDATE memories
                SET metadata=?,
                    confidence=?,
                    updated_at=?
                WHERE id=?
                """,
                (json_dumps(metadata), confidence, now, memory_id),
            )
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
            self._upsert_memory_fts_row(row)
            self._conn.commit()
            return cur.rowcount > 0

    async def delete_memory(self, memory_id: str) -> bool:
        return await asyncio.to_thread(self._delete_memory_sync, memory_id)

    @staticmethod
    def _timeline_ids_in_metadata(value: Any) -> set[str]:
        refs: set[str] = set()
        if isinstance(value, dict):
            for item in value.values():
                refs.update(MemoryStoreRecordUpdates._timeline_ids_in_metadata(item))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                refs.update(MemoryStoreRecordUpdates._timeline_ids_in_metadata(item))
        elif isinstance(value, str):
            ref = value.strip()
            if ref.startswith("timeline:"):
                ref = ref[len("timeline:") :]
            if ref.startswith("tl_") and len(ref) <= 160:
                refs.add(ref)
        return refs

    def _timeline_reference_memories_sync(
        self, event_ids: set[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        if not event_ids:
            return []
        matches: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            rows = self._conn.execute("SELECT id, metadata FROM memories").fetchall()
        for row in rows:
            metadata = json_loads(row["metadata"], {})
            if not isinstance(metadata, dict):
                continue
            if event_ids.intersection(self._timeline_ids_in_metadata(metadata)):
                matches.append((clean_text(row["id"], 120), metadata))
        return matches

    def _mark_timeline_sources_expired_sync(self, event_ids: list[str]) -> int:
        targets = {
            clean_text(value, 160)
            for value in event_ids
            if clean_text(value, 160).startswith("tl_")
        }
        if not targets:
            return 0
        changed = 0
        now = utc_now()
        with self._lock:
            for memory_id, metadata in self._timeline_reference_memories_sync(targets):
                previous = metadata.get("source_expired_event_ids")
                expired = (
                    [
                        clean_text(value, 160)
                        for value in previous
                        if clean_text(value, 160)
                    ]
                    if isinstance(previous, list)
                    else []
                )
                matching = targets.intersection(
                    self._timeline_ids_in_metadata(metadata)
                )
                merged = list(dict.fromkeys([*expired, *sorted(matching)]))
                if merged == expired:
                    continue
                metadata["source_expired_event_ids"] = merged
                self._conn.execute(
                    "UPDATE memories SET metadata=?, updated_at=? WHERE id=?",
                    (json_dumps(metadata), now, memory_id),
                )
                changed += 1
        return changed

    def _delete_memories_referencing_timeline_ids_sync(
        self, event_ids: list[str]
    ) -> int:
        targets = {
            clean_text(value, 160)
            for value in event_ids
            if clean_text(value, 160).startswith("tl_")
        }
        if not targets:
            return 0
        with self._lock:
            memory_ids = [
                memory_id
                for memory_id, _metadata in self._timeline_reference_memories_sync(
                    targets
                )
            ]
            for memory_id in memory_ids:
                self._conn.execute(
                    "DELETE FROM review_queue WHERE memory_id=?", (memory_id,)
                )
                self._conn.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,)
                )
                self._conn.execute(
                    "DELETE FROM relationship_edges WHERE source_memory_id=?",
                    (memory_id,),
                )
                self._conn.execute(
                    "DELETE FROM knowledge_edges WHERE source_memory_id=?", (memory_id,)
                )
                self._delete_memory_fts_row(memory_id)
                self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return len(memory_ids)

    def _delete_memory_sync(self, memory_id: str) -> bool:
        memory_id = clean_text(memory_id, 120)
        with self._lock:
            with self._transaction_sync():
                self._conn.execute(
                    "DELETE FROM review_queue WHERE memory_id=?", (memory_id,)
                )
                self._conn.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id=?", (memory_id,)
                )
                self._conn.execute(
                    "DELETE FROM relationship_edges WHERE source_memory_id=?",
                    (memory_id,),
                )
                self._conn.execute(
                    "DELETE FROM knowledge_edges WHERE source_memory_id=?", (memory_id,)
                )
                self._delete_memory_fts_row(memory_id)
                cur = self._conn.execute(
                    "DELETE FROM memories WHERE id=?", (memory_id,)
                )
                return cur.rowcount > 0
