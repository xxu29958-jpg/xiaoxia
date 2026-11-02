from __future__ import annotations

from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)


class MemoryStoreProfileRepairRollback:
    """Profile repair rollback; state and transaction ownership remain in the facade."""

    async def get_profile_repair_operation(
        self, operation_id: str
    ) -> dict[str, Any] | None:
        return await self._run_recoverable_database_operation(
            self._get_profile_repair_operation_sync,
            operation_id,
        )

    def _get_profile_repair_operation_sync(
        self, operation_id: str
    ) -> dict[str, Any] | None:
        operation_id = self._valid_profile_operation_id(operation_id)
        if not operation_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM profile_repair_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["snapshot"] = json_loads(result.get("snapshot"), {})
        result["result"] = json_loads(result.get("result"), {})
        return result

    async def rollback_profile_repairs(
        self,
        *,
        operation_id: str,
        rollback_backup_path: str = "",
    ) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._rollback_profile_repairs_sync,
            operation_id,
            rollback_backup_path,
        )

    def _rollback_profile_repairs_sync(
        self,
        operation_id: str,
        rollback_backup_path: str,
    ) -> dict[str, Any]:
        operation_id = self._valid_profile_operation_id(operation_id)
        if not operation_id:
            raise ValueError("invalid profile repair operation id")
        now = utc_now()
        with self._lock:  # noqa: SIM117
            with self._transaction_sync():
                operation = self._conn.execute(
                    "SELECT * FROM profile_repair_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if operation is None:
                    raise ValueError("profile repair operation not found")
                if clean_text(operation["state"], 40) == "rolled_back":
                    previous = json_loads(operation["result"], {})
                    return {
                        **(previous if isinstance(previous, dict) else {}),
                        "ok": True,
                        "code": "profile_repair_rollback_idempotent",
                        "operation_id": operation_id,
                    }
                if clean_text(operation["state"], 40) not in {"applied", "partial"}:
                    raise ValueError("profile repair operation is not rollbackable")
                snapshot = json_loads(operation["snapshot"], {})
                records = snapshot.get("records") if isinstance(snapshot, dict) else []
                records = records if isinstance(records, list) else []
                results: list[dict[str, Any]] = []
                groups: dict[str, list[dict[str, Any]]] = {}
                for index, item in enumerate(records):
                    if not isinstance(item, dict):
                        continue
                    record_kind = (
                        clean_text(item.get("record_kind"), 32).lower() or "memory"
                    )
                    record_id = clean_text(
                        item.get("record_id") or item.get("memory_id"), 120
                    )
                    group_id = clean_text(item.get("group_id"), 120)
                    if not group_id:
                        group_id = f"legacy:{record_kind}:{record_id}:{index}"
                    groups.setdefault(group_id, []).append(item)

                restored_portrait_people: set[str] = set()
                for group_id, group in groups.items():
                    self._rollback_profile_group(
                        group=group,
                        group_id=group_id,
                        restored_portrait_people=restored_portrait_people,
                        results=results,
                    )

                for person_id in sorted(restored_portrait_people):
                    self._bump_portrait_revision_sync(person_id)

                rolled_back = sum(
                    1 for item in results if item.get("status") == "rolled-back"
                )
                stale = sum(
                    1
                    for item in results
                    if item.get("status")
                    in {
                        "skip-stale",
                        "skip-group-stale",
                        "missing",
                        "invalid-snapshot",
                    }
                )
                guards = sum(
                    1 for item in results if item.get("status") == "guard-verified"
                )
                previous_result = json_loads(operation["result"], {})
                result = (
                    dict(previous_result) if isinstance(previous_result, dict) else {}
                )
                result.update(
                    {
                        "ok": True,
                        "code": "profile_repair_rolled_back",
                        "operation_id": operation_id,
                        "rollback_backup_path": clean_text(rollback_backup_path, 2000),
                        "rollback_results": results,
                        "rolled_back": rolled_back,
                        "rollback_stale": stale,
                    }
                )
                state = (
                    "rolled_back"
                    if rolled_back + guards == len(results) and stale == 0
                    else "partial"
                )
                self._conn.execute(
                    "UPDATE profile_repair_operations SET state=?, result=?, updated_at=? WHERE operation_id=?",
                    (state, json_dumps(result), now, operation_id),
                )
                self._embedding_candidate_cache.clear()
                self._embedding_candidate_cache_revision = ""
        return result

    def _rollback_profile_group(
        self, *, group, group_id, restored_portrait_people, results
    ):
        """Rollback profile group."""
        prepared: list[dict[str, Any]] = []
        failures: dict[tuple[str, str], str] = {}
        prepared, failures = self._prepare_profile_rollback(
            failures=failures, group=group, prepared=prepared
        )

        if failures:
            for item in group:
                record_kind = (
                    clean_text(item.get("record_kind"), 32).lower() or "memory"
                )
                record_id = clean_text(
                    item.get("record_id") or item.get("memory_id"),
                    120,
                )
                result_item = {
                    "record_kind": record_kind,
                    "record_id": record_id,
                    "group_id": group_id,
                    "status": failures.get(
                        (record_kind, record_id),
                        "skip-group-stale",
                    ),
                }
                if record_kind == "memory":
                    result_item["memory_id"] = record_id
                results.append(result_item)
            return

        for prepared_item in prepared:
            item = prepared_item["item"]
            record_kind = prepared_item["record_kind"]
            record_id = prepared_item["record_id"]
            result_item = {
                "record_kind": record_kind,
                "record_id": record_id,
                "group_id": group_id,
            }
            if record_kind == "memory":
                result_item["memory_id"] = record_id
            if bool(item.get("guard_only")):
                result_item["status"] = "guard-verified"
                results.append(result_item)
                continue
            if record_kind == "portrait_fact":
                restored_fact = prepared_item["restored"]
                if not self._write_portrait_fact_snapshot_sync(
                    restored_fact
                ) or not self._restore_portrait_queue_snapshot_sync(
                    record_id, item.get("learning_queue", [])
                ):
                    raise ValueError("invalid portrait repair snapshot")
                person_id = clean_text(restored_fact.get("person_id"), 80)
                if person_id:
                    restored_portrait_people.add(person_id)
                result_item["status"] = "rolled-back"
                results.append(result_item)
                continue

            restored = prepared_item["restored"]
            self._write_memory_record_sync(restored)
            self._conn.execute(
                "DELETE FROM review_queue WHERE memory_id=?",
                (record_id,),
            )
            for review in item.get("review_queue", []):
                if not isinstance(review, dict):
                    continue
                self._conn.execute(
                    """
                    INSERT INTO review_queue(id, memory_id, reason, status, created_at, updated_at)
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        clean_text(review.get("id"), 120),
                        record_id,
                        clean_text(review.get("reason"), 500),
                        clean_text(review.get("status"), 40),
                        clean_text(review.get("created_at"), 80),
                        clean_text(review.get("updated_at"), 80),
                    ),
                )
            self._conn.execute(
                "DELETE FROM memory_embeddings WHERE memory_id=?",
                (record_id,),
            )
            for embedding in item.get("embeddings", []):
                if not isinstance(embedding, dict):
                    continue
                self._conn.execute(
                    """
                    INSERT INTO memory_embeddings(
                        memory_id, provider_id, text_hash, dimension, vector, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        record_id,
                        clean_text(embedding.get("provider_id"), 160),
                        clean_text(embedding.get("text_hash"), 80),
                        max(0, int(embedding.get("dimension") or 0)),
                        clean_text(embedding.get("vector"), 100000),
                        clean_text(embedding.get("created_at"), 80),
                        clean_text(embedding.get("updated_at"), 80),
                    ),
                )
            result_item["status"] = "rolled-back"
            results.append(result_item)

    def _prepare_profile_rollback(self, *, failures, group, prepared):
        """Prepare profile rollback."""
        for item in group:
            record_kind = clean_text(item.get("record_kind"), 32).lower() or "memory"
            record_id = clean_text(item.get("record_id") or item.get("memory_id"), 120)
            failure_key = (record_kind, record_id)
            before = item.get("before") if isinstance(item.get("before"), dict) else {}
            if record_kind == "portrait_fact":
                row = self._conn.execute(
                    "SELECT * FROM portrait_facts WHERE id=?",
                    (record_id,),
                ).fetchone()
                if row is None:
                    failures[failure_key] = "missing"
                    continue
                queue_rows = self._conn.execute(
                    """
                    SELECT * FROM portrait_learning_queue
                    WHERE fact_id=? ORDER BY queue_id
                    """,
                    (record_id,),
                ).fetchall()
                if self.profile_portrait_fact_fingerprint(row) != clean_text(
                    item.get("after_fingerprint"), 80
                ) or self.profile_portrait_queue_fingerprint(queue_rows) != clean_text(
                    item.get("after_queue_fingerprint"), 80
                ):
                    failures[failure_key] = "skip-stale"
                    continue
                queue_snapshot = item.get("learning_queue")
                if (
                    clean_text(before.get("id"), 120) != record_id
                    or not isinstance(queue_snapshot, list)
                    or any(
                        not isinstance(queue_item, dict)
                        or clean_text(queue_item.get("fact_id"), 120) != record_id
                        for queue_item in queue_snapshot
                    )
                ):
                    failures[failure_key] = "invalid-snapshot"
                    continue
                prepared.append(
                    {
                        "item": item,
                        "record_kind": record_kind,
                        "record_id": record_id,
                        "restored": before,
                    }
                )
                continue
            if record_kind != "memory":
                failures[failure_key] = "invalid-snapshot"
                continue
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (record_id,)
            ).fetchone()
            if row is None:
                failures[failure_key] = "missing"
                continue
            current = MemoryRecord.from_row(row)
            if self.profile_memory_fingerprint(current) != clean_text(
                item.get("after_fingerprint"), 80
            ):
                failures[failure_key] = "skip-stale"
                continue
            try:
                restored = self._memory_from_db_snapshot(before)
            except (KeyError, TypeError, ValueError):
                failures[failure_key] = "invalid-snapshot"
                continue
            prepared.append(
                {
                    "item": item,
                    "record_kind": record_kind,
                    "record_id": record_id,
                    "restored": restored,
                }
            )

        return prepared, failures
