"""One native transaction owner for goals and continuing conversational matters."""

import json
import time
from copy import deepcopy
from typing import TYPE_CHECKING

from .core_state_sources import operation_for, source_identity
from .core_state_versions import CoreStateError, check_source, digest, encoded
from .working_state_rules import (
    check_version,
    normalize_change,
    object_id,
    public_record,
    revised,
    valid_clock,
    verify_quote,
    visible,
)

if TYPE_CHECKING:
    from .store import MemoryStore


class WorkingState:
    """Borrow the live store's connection, transaction lock and recovery barrier."""

    def __init__(self, store: "MemoryStore"):
        self.store = store

    async def change(self, *, owner, change, source, scopes, authorize, now=None):
        try:
            return await self.store._run_recoverable_database_operation(
                self._change,
                owner,
                deepcopy(change),
                deepcopy(source),
                deepcopy(scopes),
                authorize,
                time.time() if now is None else now,
            )
        except CoreStateError as exc:
            return {"ok": False, "error": str(exc)}

    def _change(self, owner, change, source, scopes, authorize, now):
        valid_clock(now)
        change = normalize_change(change)
        explicit_id = bool(change["item_id"])
        check_source(source, scopes)
        verify_quote(change, source)
        db = self.store._conn
        with self.store._lock, self.store._transaction_sync():
            self._authorize(owner, authorize)
            self.store._core_check_capture(source)
            item_id = self._resolve_id(owner, change)
            change["item_id"] = item_id
            operation = operation_for(source, item_id)
            if source["kind"] == "management":
                operation = digest([source.get("request_id"), item_id])
            intent = {k: v for k, v in change.items() if k != "expected_version"}
            request_hash = digest([intent, source_identity(source)])
            replay = self._replay(owner, operation, request_hash)
            if replay is not None:
                self._authorize(owner, authorize)
                return replay
            current = self._current(owner, item_id)
            check_version(current, change, source)
            if current is None and (change["action"] != "save" or explicit_id):
                raise CoreStateError("working_object_missing")
            receipt = self._apply(owner, item_id, change, source, current, now)
            self._authorize(owner, authorize)
            db.execute(
                "INSERT INTO working_operations VALUES(?,?,?,?)",
                (owner, operation, request_hash, encoded(receipt)),
            )
            return receipt

    @staticmethod
    def _authorize(owner, authorize):
        if not owner or len(owner) > 128 or not authorize():
            raise CoreStateError("working_scope_changed")

    def _current(self, owner, item_id):
        row = self.store._conn.execute(
            "SELECT record FROM working_objects WHERE owner=? AND id=?",
            (owner, item_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _resolve_id(self, owner, change):
        if change["item_id"]:
            return change["item_id"]
        key = object_id(owner, change["kind"] or "topic", change["title"])
        row = self.store._conn.execute(
            "SELECT id FROM working_names WHERE owner=? AND name_hash=?", (owner, key)
        ).fetchone()
        if row is not None and not row[0]:
            raise CoreStateError("working_title_ambiguous_use_id")
        return row[0] if row else key

    def _apply(self, owner, item_id, change, source, current, now):
        deleted = change["action"] == "forget"
        record = None
        if deleted:
            self.store._conn.execute(
                "DELETE FROM working_objects WHERE owner=? AND id=?", (owner, item_id)
            )
        else:
            record = revised(
                current, change, source, owner=owner, item_id=item_id, now=now
            )
            self.write(record)
        return {
            "ok": True,
            "id": item_id,
            "action": change["action"],
            "version": record["version"] if record else "",
            "deleted": deleted,
            "replayed": False,
            "current": True,
        }

    def write(self, record, *, importing=False):
        """Called only inside a native transaction, including legacy adoption."""
        self._bind_name(record, importing)
        values = (record["owner"], record["id"], encoded(record))
        self.store._conn.execute(
            "INSERT INTO working_objects VALUES(?,?,?) ON CONFLICT(owner,id) DO UPDATE SET record=excluded.record",
            values,
        )
        self.store._conn.execute(
            "INSERT INTO working_revisions VALUES(?,?,?,?)",
            (*values[:2], record["revision"], values[2]),
        )

    def _bind_name(self, record, importing):
        owner, item_id = record["owner"], record["id"]
        key = object_id(owner, record["kind"], record["title"])
        row = self.store._conn.execute(
            "SELECT id FROM working_names WHERE owner=? AND name_hash=?", (owner, key)
        ).fetchone()
        if row and row[0] != item_id:
            if not importing:
                current = self._current(owner, item_id)
                if (
                    row[0]
                    or not current
                    or object_id(owner, current["kind"], current["title"]) != key
                ):
                    raise CoreStateError("working_title_ambiguous_use_id")
            item_id = ""
        self.store._conn.execute(
            "INSERT INTO working_names VALUES(?,?,?) ON CONFLICT(owner,name_hash) DO UPDATE SET id=excluded.id",
            (owner, key, item_id),
        )

    def _replay(self, owner, operation, request_hash):
        row = self.store._conn.execute(
            "SELECT request_hash,receipt FROM working_operations WHERE owner=? AND operation=?",
            (owner, operation),
        ).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise CoreStateError("working_operation_conflict")
        receipt = json.loads(row[1])
        current = self._current(owner, receipt["id"])
        still_current = (
            current is None
            if receipt["deleted"]
            else (current is not None and current["version"] == receipt["version"])
        )
        return {**receipt, "replayed": True, "current": still_current}

    async def snapshot(
        self,
        owner,
        *,
        authorize,
        now=None,
        query="",
        offset=0,
        revision="",
        include_closed=False,
        limit=12,
    ):
        return await self.store._run_recoverable_database_operation(
            self._snapshot,
            owner,
            authorize,
            time.time() if now is None else now,
            query,
            offset,
            revision,
            include_closed,
            limit,
        )

    def _snapshot(
        self, owner, authorize, now, query, offset, revision, include_closed, limit
    ):
        valid_clock(now)
        if not isinstance(offset, int) or offset < 0:
            raise CoreStateError("invalid_working_cursor")
        with self.store._lock, self.store._transaction_sync():
            self._authorize(owner, authorize)
            rows = self.store._conn.execute(
                "SELECT record FROM working_objects WHERE owner=? ORDER BY id", (owner,)
            ).fetchall()
            records = [json.loads(row[0]) for row in rows]
            selected = [
                r
                for r in records
                if (include_closed or visible(r, now))
                and all(
                    w.casefold() in (r["title"] + " " + r["content"]).casefold()
                    for w in query.split()
                )
            ]
            selected.sort(
                key=lambda r: (r["kind"] == "goal", r["updated_at"], r["id"]),
                reverse=True,
            )
            fingerprint = digest(
                [
                    query.casefold().split(),
                    include_closed,
                    [[r["id"], r["version"]] for r in selected],
                ]
            )
            if offset and revision != fingerprint:
                raise CoreStateError("working_snapshot_changed_restart_query")
            end = offset + max(1, min(50, limit))
            self._authorize(owner, authorize)
            return {
                "ok": True,
                "items": [public_record(r) for r in selected[offset:end]],
                "total": len(selected),
                "revision": fingerprint,
                "next_offset": end if end < len(selected) else None,
                "observed_at": now,
                "scope": "working_objects",
                "include_closed": include_closed,
            }

    async def history(self, owner, item_id, *, authorize, before=0):
        return await self.store._run_recoverable_database_operation(
            self._history,
            owner,
            item_id,
            authorize,
            before,
        )

    def _history(self, owner, item_id, authorize, before):
        with self.store._lock, self.store._transaction_sync():
            self._authorize(owner, authorize)
            current = self._current(owner, item_id)
            if current is None:
                raise CoreStateError("working_object_missing")
            rows = self.store._conn.execute(
                "SELECT record FROM working_revisions WHERE owner=? AND id=? "
                "AND (?=0 OR revision<?) ORDER BY revision DESC LIMIT 21",
                (owner, item_id, before, before),
            ).fetchall()
            records = [json.loads(row[0]) for row in rows]
            self._authorize(owner, authorize)
            return {
                "ok": True,
                "history": [public_record(r) for r in records[:20]],
                "current_version": current["version"],
                "next_before": records[19]["revision"] if len(records) > 20 else None,
            }
