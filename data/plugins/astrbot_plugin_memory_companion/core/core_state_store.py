"""Core fact transactions use the native MemoryStore connection and close barrier."""

import json
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from threading import RLock
from time import time_ns
from typing import Any
from uuid import uuid4

from .core_state_definition import shared_record
from .core_state_sources import event_source, operation_for, source_identity
from .core_state_versions import (
    CoreStateError,
    apply_source,
    check_change,
    check_source,
    digest,
    encoded,
    permitted,
    retain_version,
    revision,
)
from .models import MemoryRecord, utc_now
from .source_capture import SourceCapture


class CoreStateAccess:
    """The facade supplies resources; this mixin never opens another database."""

    _conn: sqlite3.Connection
    _lock: RLock
    _transaction_sync: Callable[[], AbstractContextManager]
    _run_recoverable_database_operation: Callable[..., Awaitable[Any]]
    _upsert_memory_fts_row: Callable[[sqlite3.Row | None], None]
    _delete_memory_sync: Callable[[str], bool]
    prepare_shared_core = staticmethod(shared_record)
    core_event_source = staticmethod(event_source)
    core_operation = staticmethod(operation_for)

    async def save_core_memory(self, record, *, expected_revision=None):
        return await self._run_recoverable_database_operation(
            self._save_core_memory_sync, deepcopy(record), expected_revision
        )

    def _save_core_memory_sync(self, record, expected_revision):
        with self._lock, self._transaction_sync():
            current = self._core_current(record.id)
            if current and current.memory_type != "core_memory":
                return {"ok": False, "code": "memory_type_conflict"}
            if expected_revision is not None and expected_revision != revision(current):
                return {
                    "ok": False,
                    "code": "revision_conflict",
                    "current_revision": revision(current),
                }
            return self._core_write(
                record,
                current,
                {
                    "kind": "management",
                    "recorded_at": utc_now(),
                    "received_ns": time_ns(),
                },
            )

    def _core_current(self, memory_id):
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        return MemoryRecord.from_row(row) if row else None

    def _core_write(self, record, current, source):
        record.memory_type = "core_memory"
        record.ensure_defaults()
        if current:
            retain_version(self._conn, current)
            record.created_at = current.created_at
        record.metadata = {
            **record.metadata,
            "core_revision": revision(current) + 1,
            "core_memory": True,
            "core_version": uuid4().hex,
        }
        apply_source(record, source)
        record.updated_at = utc_now()
        record.canonical_key = record.content_fingerprint = ""
        data = record.to_db()
        columns = ",".join(data)
        parameters = ",".join(":" + key for key in data)
        updates = ",".join(f"{key}=excluded.{key}" for key in data if key != "id")
        self._conn.execute(
            f"INSERT INTO memories ({columns}) VALUES ({parameters}) ON CONFLICT(id) DO UPDATE SET {updates}",
            data,
        )
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (record.id,)
        ).fetchone()
        self._upsert_memory_fts_row(row)
        retain_version(self._conn, record, source)
        return {"ok": True, "id": record.id, "revision": revision(record)}

    async def change_core_memories(
        self, *, owner, operation, intent, changes, scopes, source, authorize
    ):
        try:
            return await self._run_recoverable_database_operation(
                self._change_core_memories_sync,
                owner,
                operation,
                deepcopy(intent),
                deepcopy(changes),
                deepcopy(scopes),
                deepcopy(source),
                authorize,
            )
        except CoreStateError as error:
            return {"ok": False, "error": str(error)}

    def _change_core_memories_sync(
        self, owner, operation, intent, changes, scopes, source, authorize
    ):
        if not owner or not operation or len(owner) > 128 or len(operation) > 128:
            raise CoreStateError("core_operation_required")
        check_source(source, scopes)
        request_hash = digest([intent, source_identity(source)])
        with self._lock, self._transaction_sync():
            if not authorize():
                raise CoreStateError("core_scope_changed")
            self._core_check_capture(source)
            replay = self._core_replay(owner, operation, request_hash)
            if replay is not None:
                return replay
            if not changes:
                raise CoreStateError("core_memory_not_found")
            self._core_check_members(changes, scopes, intent)
            current = {
                record.id: self._core_current(record.id) for record, _ in changes
            }
            for record, expected in changes:
                check_change(current[record.id], record, expected, scopes, source)
            writes = self._core_apply(intent, changes, current, source)
            if not authorize():
                raise CoreStateError("core_scope_changed")
            receipt = {
                "ok": True,
                "writes": writes,
                "operation": operation,
                "committed_at": utc_now(),
                "replayed": False,
                "current": True,
            }
            self._conn.execute(
                "INSERT INTO core_memory_mutations(owner,operation,request_hash,receipt) VALUES(?,?,?,?)",
                (owner, operation, request_hash, encoded(receipt)),
            )
            return receipt

    def _core_check_capture(self, source):
        ref = source.get("capture")
        if ref:
            latest = SourceCapture(self).lookup(ref["operation"])
            if (
                latest.get("source_state") != "current"
                or latest.get("source_version") != ref["source_version"]
            ):
                raise CoreStateError("core_source_unavailable")

    def _core_check_members(self, changes, scopes, intent):
        if len(changes) > 1000 or len({r.id for r, _ in changes}) != len(changes):
            raise CoreStateError("invalid_core_changes")
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE memory_type='core_memory'"
        ).fetchall()
        actual = {
            record.id
            for row in rows
            if permitted(record := MemoryRecord.from_row(row), scopes)
            and record.metadata.get("core_label", "").casefold()
            == intent["label"].casefold()
        }
        expected = {
            r.id for r, version in changes if version or self._core_current(r.id)
        }
        if actual != expected:
            raise CoreStateError("core_members_changed")
        for record, _ in changes:
            if (
                record.metadata.get("core_label", "").casefold()
                != intent["label"].casefold()
            ):
                raise CoreStateError("invalid_core_changes")

    def _core_apply(self, intent, changes, current, source):
        writes = []
        for record, _ in changes:
            if intent["action"] == "delete":
                if not self._delete_memory_sync(record.id):
                    raise CoreStateError("core_memory_not_found")
                writes.append({"ok": True, "id": record.id, "deleted": True})
            elif intent["action"] == "set":
                receipt = self._core_write(record, current[record.id], source)
                writes.append({**receipt, "version": record.metadata["core_version"]})
            else:
                raise CoreStateError("invalid_core_action")
        return writes

    def _core_replay(self, owner, operation, request_hash):
        row = self._conn.execute(
            "SELECT request_hash,receipt FROM core_memory_mutations WHERE owner=? AND operation=?",
            (owner, operation),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise CoreStateError("core_operation_conflict")
        receipt = json.loads(row["receipt"])
        still_current = []
        for write in receipt["writes"]:
            current = self._core_current(write["id"])
            still_current.append(
                current is None
                if write.get("deleted")
                else current is not None
                and current.metadata.get("core_version") == write["version"]
            )
        return {**receipt, "replayed": True, "current": all(still_current)}

    async def core_memory_history(self, memory_ids, scopes, *, before=0, limit=20):
        return await self._run_recoverable_database_operation(
            self._core_memory_history_sync, memory_ids, scopes, before, limit
        )

    def _core_memory_history_sync(self, memory_ids, scopes, before, limit):
        results = []
        with self._lock:
            for memory_id in memory_ids:
                current = self._core_current(memory_id)
                if current is None or not permitted(current, scopes):
                    raise CoreStateError("core_scope_changed")
                rows = self._conn.execute(
                    "SELECT * FROM core_memory_revisions WHERE memory_id=? AND (?=0 OR revision<?) ORDER BY revision DESC LIMIT ?",
                    (memory_id, before, before, max(1, min(20, limit))),
                ).fetchall()
                results.extend(
                    {
                        **dict(row),
                        "record": json.loads(row["record"]),
                        "source": json.loads(row["source"]),
                        "current": row["revision"] == revision(current),
                    }
                    for row in rows
                )
        return results
