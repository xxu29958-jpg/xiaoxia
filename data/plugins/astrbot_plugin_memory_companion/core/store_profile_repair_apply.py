from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)


class MemoryStoreProfileRepairApply:
    """Profile repair apply; state and transaction ownership remain in the facade."""

    async def apply_profile_repairs(
        self,
        *,
        operation_id: str,
        rule_version: str,
        actions: list[dict[str, Any]],
        backup_path: str,
    ) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._apply_profile_repairs_sync,
            operation_id,
            rule_version,
            deepcopy(actions),
            backup_path,
        )

    def _apply_profile_repairs_sync(
        self,
        operation_id: str,
        rule_version: str,
        actions: list[dict[str, Any]],
        backup_path: str,
    ) -> dict[str, Any]:
        operation_id = self._valid_profile_operation_id(operation_id)
        rule_version = clean_text(rule_version, 80)
        backup_path = clean_text(backup_path, 2000)
        if not operation_id or not rule_version or not backup_path:
            raise ValueError("profile repair operation metadata is invalid")
        if len(actions) > 100000:
            raise ValueError("profile repair action limit exceeded")
        plan_fingerprint = self.profile_repair_plan_fingerprint(actions)
        now = utc_now()

        with self._lock:  # noqa: SIM117
            with self._transaction_sync():
                prior = self._conn.execute(
                    "SELECT * FROM profile_repair_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if prior is not None:
                    if (
                        clean_text(prior["rule_version"], 80) != rule_version
                        or clean_text(prior["plan_fingerprint"], 80) != plan_fingerprint
                    ):
                        raise ValueError(
                            "profile repair operation id already used with a different plan"
                        )
                    if clean_text(prior["state"], 40) == "rolled_back":
                        raise ValueError(
                            "profile repair operation has already been rolled back"
                        )
                    result = json_loads(prior["result"], {})
                    return {
                        **(result if isinstance(result, dict) else {}),
                        "ok": True,
                        "code": "profile_repair_idempotent_replay",
                        "operation_id": operation_id,
                    }

                snapshots: dict[str, dict[str, Any]] = {}
                validated_targets: set[str] = set()
                validated_portrait_targets: set[str] = set()
                changed_ids: set[str] = set()
                affected_portrait_people: set[str] = set()
                results: list[dict[str, Any]] = []
                for raw_action in actions:
                    if not isinstance(raw_action, dict):
                        continue
                    record_kind = (
                        clean_text(raw_action.get("record_kind"), 32).lower()
                        or "memory"
                    )
                    memory_id = clean_text(
                        raw_action.get("record_id") or raw_action.get("memory_id"),
                        120,
                    )
                    action = clean_text(raw_action.get("action"), 24).lower()
                    reason = clean_text(raw_action.get("reason"), 240)
                    if record_kind == "portrait_fact":
                        self._apply_portrait_profile_repair(
                            action,
                            affected_portrait_people,
                            changed_ids,
                            memory_id,
                            now,
                            operation_id,
                            raw_action,
                            record_kind,
                            results,
                            snapshots,
                            validated_portrait_targets,
                        )
                        continue
                    self._apply_memory_profile_repair(
                        action,
                        changed_ids,
                        memory_id,
                        now,
                        operation_id,
                        raw_action,
                        reason,
                        record_kind,
                        results,
                        snapshots,
                        validated_targets,
                    )

                result = self._finish_profile_repair(
                    affected_portrait_people,
                    backup_path,
                    changed_ids,
                    now,
                    operation_id,
                    plan_fingerprint,
                    results,
                    rule_version,
                    snapshots,
                )
        return result

    def _finish_profile_repair(
        self,
        affected_portrait_people,
        backup_path,
        changed_ids,
        now,
        operation_id,
        plan_fingerprint,
        results,
        rule_version,
        snapshots,
    ):
        """Finish profile repair."""
        for person_id in sorted(affected_portrait_people):
            self._bump_portrait_revision_sync(person_id)
        for snapshot in snapshots.values():
            record_kind = (
                clean_text(snapshot.get("record_kind"), 32).lower() or "memory"
            )
            record_id = clean_text(
                snapshot.get("record_id") or snapshot.get("memory_id"),
                120,
            )
            if record_kind == "portrait_fact":
                row = self._conn.execute(
                    "SELECT * FROM portrait_facts WHERE id=?",
                    (record_id,),
                ).fetchone()
                if row is not None:
                    snapshot["after_fingerprint"] = (
                        self.profile_portrait_fact_fingerprint(row)
                    )
                    queue_rows = self._conn.execute(
                        """
                        SELECT * FROM portrait_learning_queue
                        WHERE fact_id=? ORDER BY queue_id
                        """,
                        (record_id,),
                    ).fetchall()
                    snapshot["after_queue_fingerprint"] = (
                        self.profile_portrait_queue_fingerprint(queue_rows)
                    )
                continue
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (record_id,)
            ).fetchone()
            if row is not None:
                snapshot["after_fingerprint"] = self.profile_memory_fingerprint(
                    MemoryRecord.from_row(row)
                )
        snapshot_payload = {
            "schema_version": "profile.repair.snapshot.v2",
            "records": list(snapshots.values()),
        }
        stale_count = sum(1 for item in results if item.get("status") == "skip-stale")
        result = {
            "ok": True,
            "code": "profile_repair_applied",
            "operation_id": operation_id,
            "rule_version": rule_version,
            "plan_fingerprint": plan_fingerprint,
            "backup_path": backup_path,
            "results": results,
            "changed": len(changed_ids),
            "stale": stale_count,
        }
        state = (
            "partial"
            if any(
                item.get("status")
                in {
                    "skip-stale",
                    "missing",
                    "unsupported",
                    "invalid-action",
                    "invalid-canonical",
                    "acl-domain-mismatch",
                }
                for item in results
            )
            else "applied"
        )
        self._conn.execute(
            """
            INSERT INTO profile_repair_operations(
                operation_id, rule_version, state, backup_path, plan_fingerprint,
                snapshot, result, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                operation_id,
                rule_version,
                state,
                backup_path,
                plan_fingerprint,
                json_dumps(snapshot_payload),
                json_dumps(result),
                now,
                now,
            ),
        )
        self._embedding_candidate_cache.clear()
        self._embedding_candidate_cache_revision = ""

        return result

    def _apply_memory_profile_repair(
        self,
        action,
        changed_ids,
        memory_id,
        now,
        operation_id,
        raw_action,
        reason,
        record_kind,
        results,
        snapshots,
        validated_targets,
    ):
        """Apply memory profile repair."""
        if record_kind != "memory":
            results.append(
                {
                    "record_kind": record_kind,
                    "record_id": memory_id,
                    "memory_id": memory_id,
                    "status": "unsupported",
                }
            )
            return
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        if row is None:
            results.append({"memory_id": memory_id, "status": "missing"})
            return
        current = MemoryRecord.from_row(row)
        metadata = current.metadata if isinstance(current.metadata, dict) else {}
        if (
            current.memory_type not in self.PROFILE_MEMORY_TYPES
            or clean_text(metadata.get("extractor"), 40).lower()
            not in self.PROFILE_RULE_EXTRACTORS
        ):
            results.append({"memory_id": memory_id, "status": "unsupported"})
            return
        expected = clean_text(raw_action.get("expected_fingerprint"), 80)
        if not expected or self.profile_memory_fingerprint(current) != expected:
            results.append({"memory_id": memory_id, "status": "skip-stale"})
            return
        if action == "keep":
            results.append({"memory_id": memory_id, "status": "kept"})
            return
        if action not in {"pending", "archive", "merge"}:
            results.append({"memory_id": memory_id, "status": "invalid-action"})
            return
        snapshots.setdefault(
            memory_id,
            self._profile_repair_record_snapshot_sync(
                current,
                group_id=self.profile_repair_group_id(memory_id),
            ),
        )

        records_to_write: list[MemoryRecord] = []
        if action == "merge":
            canonical_id = clean_text(raw_action.get("canonical_id"), 120)
            group_id = self.profile_repair_group_id(canonical_id)
            snapshots[memory_id]["group_id"] = group_id
            canonical_row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?",
                (canonical_id,),
            ).fetchone()
            if canonical_row is None or canonical_id == memory_id:
                results.append({"memory_id": memory_id, "status": "invalid-canonical"})
                return
            canonical = MemoryRecord.from_row(canonical_row)
            if canonical_id not in validated_targets:
                canonical_expected = clean_text(
                    raw_action.get("canonical_expected_fingerprint"),
                    80,
                )
                if (
                    not canonical_expected
                    or self.profile_memory_fingerprint(canonical) != canonical_expected
                ):
                    results.append({"memory_id": memory_id, "status": "skip-stale"})
                    return
                validated_targets.add(canonical_id)
                snapshots.setdefault(
                    canonical_id,
                    self._profile_repair_record_snapshot_sync(
                        canonical,
                        group_id=group_id,
                    ),
                )
            snapshots[canonical_id]["group_id"] = group_id
            merged = self._profile_repair_merge_sync(
                current,
                canonical,
                operation_id=operation_id,
                reason=reason,
                now=now,
            )
            if merged is None:
                results.append(
                    {
                        "memory_id": memory_id,
                        "status": "acl-domain-mismatch",
                    }
                )
                return
            records_to_write.extend(merged)
        else:
            metadata_patch = raw_action.get("metadata_patch")
            if isinstance(metadata_patch, dict):
                allowed_patch_keys = {
                    "profile_dimension",
                    "profile_value",
                    "normalized_value",
                    "profile_polarity",
                    "profile_cardinality",
                    "extraction_quality",
                    "extraction_quality_score",
                    "evidence_strength",
                    "quality_gate_passed",
                }
                metadata = {
                    **metadata,
                    **{
                        key: value
                        for key, value in metadata_patch.items()
                        if key in allowed_patch_keys
                    },
                }
            next_metadata = self._profile_repair_history(
                metadata,
                operation_id=operation_id,
                action=action,
                reason=reason,
                now=now,
            )
            if action == "pending":
                next_metadata["profile_state"] = "candidate"
                next_metadata["profile_status"] = "candidate"
                current.lifecycle = "raw_event"
                current.review_status = "pending"
            else:
                target_state = clean_text(
                    raw_action.get("target_state"),
                    32,
                ).lower()
                if target_state not in {"rejected", "superseded"}:
                    target_state = "rejected"
                if target_state == "superseded":
                    canonical_id = clean_text(
                        raw_action.get("canonical_id"),
                        120,
                    )
                    group_id = self.profile_repair_group_id(canonical_id)
                    snapshots[memory_id]["group_id"] = group_id
                    canonical_row = self._conn.execute(
                        "SELECT * FROM memories WHERE id=?",
                        (canonical_id,),
                    ).fetchone()
                    if canonical_row is None or canonical_id == memory_id:
                        results.append(
                            {
                                "memory_id": memory_id,
                                "status": "invalid-canonical",
                            }
                        )
                        return
                    canonical = MemoryRecord.from_row(canonical_row)
                    if canonical_id not in validated_targets:
                        canonical_expected = clean_text(
                            raw_action.get("canonical_expected_fingerprint"),
                            80,
                        )
                        if (
                            not canonical_expected
                            or self.profile_memory_fingerprint(canonical)
                            != canonical_expected
                        ):
                            results.append(
                                {
                                    "memory_id": memory_id,
                                    "status": "skip-stale",
                                }
                            )
                            return
                        validated_targets.add(canonical_id)
                    snapshots.setdefault(
                        canonical_id,
                        self._profile_repair_record_snapshot_sync(
                            canonical,
                            group_id=group_id,
                            guard_only=True,
                        ),
                    )
                    snapshots[canonical_id]["group_id"] = group_id
                    canonical_metadata = (
                        canonical.metadata
                        if isinstance(canonical.metadata, dict)
                        else {}
                    )
                    if (
                        self._profile_domain(current) != self._profile_domain(canonical)
                        or clean_text(
                            metadata.get("profile_dimension"),
                            80,
                        ).lower()
                        != clean_text(
                            canonical_metadata.get("profile_dimension"),
                            80,
                        ).lower()
                        or self._profile_state(canonical) != "active"
                    ):
                        results.append(
                            {
                                "memory_id": memory_id,
                                "status": "acl-domain-mismatch",
                            }
                        )
                        return
                next_metadata["profile_state"] = target_state
                next_metadata["profile_status"] = target_state
                next_metadata["profile_archive_reason"] = reason
                current.lifecycle = "archived"
                current.review_status = "auto"
                if target_state == "superseded":
                    current.supersedes_id = canonical_id
                    next_metadata["profile_superseded_by"] = current.supersedes_id
            current.metadata = next_metadata
            current.updated_at = now
            records_to_write.append(current)

        for changed in records_to_write:
            self._write_memory_record_sync(changed)
            self._conn.execute(
                "DELETE FROM memory_embeddings WHERE memory_id=?",
                (changed.id,),
            )
            if changed.review_status == "pending":
                self._upsert_review_sync(
                    changed.id,
                    "profile_repair_pending",
                )
            else:
                self._conn.execute(
                    "UPDATE review_queue SET status=?, updated_at=? WHERE memory_id=? AND status='pending'",
                    (
                        "superseded" if changed.supersedes_id else "auto",
                        now,
                        changed.id,
                    ),
                )
            changed_ids.add(changed.id)
        results.append(
            {
                "memory_id": memory_id,
                "status": "applied",
                "action": action,
                "canonical_id": clean_text(raw_action.get("canonical_id"), 120),
            }
        )

    def _apply_portrait_profile_repair(
        self,
        action,
        affected_portrait_people,
        changed_ids,
        memory_id,
        now,
        operation_id,
        raw_action,
        record_kind,
        results,
        snapshots,
        validated_portrait_targets,
    ):
        """Apply portrait profile repair."""
        portrait_key = f"portrait_fact:{memory_id}"
        fact_row = self._conn.execute(
            "SELECT * FROM portrait_facts WHERE id=?",
            (memory_id,),
        ).fetchone()
        if fact_row is None:
            results.append(
                {
                    "record_kind": record_kind,
                    "record_id": memory_id,
                    "status": "missing",
                }
            )
            return
        fact = dict(fact_row)
        if (
            clean_text(fact.get("producer_kind"), 80) != "rule_explicit"
            or clean_text(fact.get("producer_version"), 80)
            not in self.PROFILE_RULE_PORTRAIT_VERSIONS
        ):
            results.append(
                {
                    "record_kind": record_kind,
                    "record_id": memory_id,
                    "status": "unsupported",
                }
            )
            return
        expected = clean_text(raw_action.get("expected_fingerprint"), 80)
        queue_rows = self._conn.execute(
            """
            SELECT * FROM portrait_learning_queue
            WHERE fact_id=? ORDER BY queue_id
            """,
            (memory_id,),
        ).fetchall()
        expected_queue = clean_text(raw_action.get("expected_queue_fingerprint"), 80)
        if (
            not expected
            or self.profile_portrait_fact_fingerprint(fact) != expected
            or (
                expected_queue
                and self.profile_portrait_queue_fingerprint(queue_rows)
                != expected_queue
            )
        ):
            results.append(
                {
                    "record_kind": record_kind,
                    "record_id": memory_id,
                    "status": "skip-stale",
                }
            )
            return
        if action == "keep":
            results.append(
                {
                    "record_kind": record_kind,
                    "record_id": memory_id,
                    "status": "kept",
                }
            )
            return
        if action not in {"pending", "archive"}:
            results.append(
                {
                    "record_kind": record_kind,
                    "record_id": memory_id,
                    "status": "invalid-action",
                }
            )
            return
        snapshots.setdefault(
            portrait_key,
            self._profile_repair_portrait_snapshot_sync(
                fact,
                group_id=self.profile_repair_group_id(memory_id),
            ),
        )
        target_state = "pending"
        canonical_id = ""
        queue_state = "pending_review"
        if action == "archive":
            target_state = clean_text(raw_action.get("target_state"), 32).lower()
            if target_state not in {"rejected", "superseded"}:
                target_state = "rejected"
            queue_state = target_state
        if target_state == "superseded":
            canonical_id = clean_text(raw_action.get("canonical_id"), 120)
            group_id = self.profile_repair_group_id(canonical_id)
            snapshots[portrait_key]["group_id"] = group_id
            canonical_row = self._conn.execute(
                "SELECT * FROM portrait_facts WHERE id=?",
                (canonical_id,),
            ).fetchone()
            if canonical_row is None or canonical_id == memory_id:
                results.append(
                    {
                        "record_kind": record_kind,
                        "record_id": memory_id,
                        "status": "invalid-canonical",
                    }
                )
                return
            canonical = dict(canonical_row)
            canonical_queue = self._conn.execute(
                """
                SELECT * FROM portrait_learning_queue
                WHERE fact_id=? ORDER BY queue_id
                """,
                (canonical_id,),
            ).fetchall()
            if canonical_id not in validated_portrait_targets:
                canonical_expected = clean_text(
                    raw_action.get("canonical_expected_fingerprint"),
                    80,
                )
                canonical_queue_expected = clean_text(
                    raw_action.get("canonical_expected_queue_fingerprint"),
                    80,
                )
                if (
                    not canonical_expected
                    or self.profile_portrait_fact_fingerprint(canonical)
                    != canonical_expected
                    or (
                        canonical_queue_expected
                        and self.profile_portrait_queue_fingerprint(canonical_queue)
                        != canonical_queue_expected
                    )
                ):
                    results.append(
                        {
                            "record_kind": record_kind,
                            "record_id": memory_id,
                            "status": "skip-stale",
                        }
                    )
                    return
                validated_portrait_targets.add(canonical_id)
            if (
                clean_text(fact.get("person_id"), 80)
                != clean_text(canonical.get("person_id"), 80)
                or clean_text(fact.get("dimension"), 80).lower()
                != clean_text(canonical.get("dimension"), 80).lower()
                or clean_text(fact.get("source_scope"), 80)
                != clean_text(canonical.get("source_scope"), 80)
                or clean_text(canonical.get("status"), 40) != "active"
            ):
                results.append(
                    {
                        "record_kind": record_kind,
                        "record_id": memory_id,
                        "status": "acl-domain-mismatch",
                    }
                )
                return
            canonical_key = f"portrait_fact:{canonical_id}"
            snapshots.setdefault(
                canonical_key,
                self._profile_repair_portrait_snapshot_sync(
                    canonical,
                    group_id=group_id,
                    guard_only=True,
                ),
            )
            snapshots[canonical_key]["group_id"] = group_id

        self._conn.execute(
            """
            UPDATE portrait_facts
            SET status=?, supersedes_id=?, revision=revision+1,
                operation_id=?, updated_at=?
            WHERE id=?
            """,
            (
                target_state,
                canonical_id,
                operation_id,
                now,
                memory_id,
            ),
        )
        self._conn.execute(
            """
            UPDATE portrait_learning_queue
            SET state=?, updated_at=?
            WHERE fact_id=? AND state='pending'
            """,
            (queue_state, now, memory_id),
        )
        person_id = clean_text(fact.get("person_id"), 80)
        if person_id:
            affected_portrait_people.add(person_id)
        changed_ids.add(portrait_key)
        results.append(
            {
                "record_kind": record_kind,
                "record_id": memory_id,
                "status": "applied",
                "action": action,
                "canonical_id": canonical_id,
            }
        )
        return
