"""Native transaction owner for source-bound, revisable event interpretations."""

import json
import time
from copy import deepcopy
from typing import TYPE_CHECKING

from .appraisal_attention import select
from .appraisal_rules import check_version, normalize, public_record, revise
from .core_state_sources import operation_for, source_identity
from .core_state_versions import CoreStateError, check_source, digest, encoded
from .working_state_rules import valid_clock

if TYPE_CHECKING:
    from .store import MemoryStore


class AppraisalState:
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
        check_source(source, scopes)
        change = normalize(change, source)
        item_id = (
            change["item_id"]
            or "event_" + digest([owner, operation_for(source, "appraisal-event")])[:32]
        )
        operation = operation_for(source, item_id)
        intent = {k: v for k, v in change.items() if k != "expected_version"}
        request_hash = digest([intent, source_identity(source)])
        with self.store._lock, self.store._transaction_sync():
            self._authorize(owner, authorize)
            self.store._core_check_capture(source)
            replay = self._replay(owner, operation, request_hash)
            if replay is not None:
                self._authorize(owner, authorize)
                return replay
            current = self._current(owner, item_id)
            check_version(current, change, source)
            if current and not self._sources_current(owner, item_id):
                raise CoreStateError("appraisal_source_unavailable")
            record = revise(
                current, change, source, owner=owner, item_id=item_id, now=now
            )
            receipt = self._write(record, operation, request_hash)
            self._authorize(owner, authorize)
            return receipt

    def _write(self, record, operation, request_hash):
        db = self.store._conn
        values = (record["owner"], record["id"], encoded(record))
        db.execute(
            "INSERT INTO appraisal_objects VALUES(?,?,?) ON CONFLICT(owner,id) DO UPDATE SET record=excluded.record",
            values,
        )
        db.execute(
            "INSERT INTO appraisal_revisions VALUES(?,?,?,?)",
            (*values[:2], record["revision"], values[2]),
        )
        receipt = {
            "ok": True,
            "id": record["id"],
            "version": record["version"],
            "revision": record["revision"],
            "replayed": False,
            "current": True,
        }
        db.execute(
            "INSERT INTO appraisal_operations VALUES(?,?,?,?)",
            (record["owner"], operation, request_hash, encoded(receipt)),
        )
        return receipt

    @staticmethod
    def _authorize(owner, authorize):
        if not owner or len(owner) > 128 or not authorize():
            raise CoreStateError("appraisal_scope_changed")

    def _current(self, owner, item_id):
        row = self.store._conn.execute(
            "SELECT record FROM appraisal_objects WHERE owner=? AND id=?",
            (owner, item_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _sources_current(self, owner, item_id):
        rows = self.store._conn.execute(
            "SELECT record FROM appraisal_revisions WHERE owner=? AND id=?",
            (owner, item_id),
        ).fetchall()
        try:
            for row in rows:
                self.store._core_check_capture(json.loads(row[0])["source"])
        except CoreStateError:
            return False
        return bool(rows)

    def _replay(self, owner, operation, request_hash):
        row = self.store._conn.execute(
            "SELECT request_hash,receipt FROM appraisal_operations WHERE owner=? AND operation=?",
            (owner, operation),
        ).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise CoreStateError("appraisal_operation_conflict")
        receipt = json.loads(row[1])
        current = self._current(owner, receipt["id"])
        valid = bool(
            current
            and current["version"] == receipt["version"]
            and self._sources_current(owner, receipt["id"])
        )
        return {**receipt, "replayed": True, "current": valid}

    async def snapshot(
        self,
        owner,
        *,
        authorize,
        limit=8,
        query="",
        goals="",
        mode="recent",
        offset=0,
        revision="",
        now=None,
    ):
        return await self.store._run_recoverable_database_operation(
            self._snapshot,
            owner,
            authorize,
            limit,
            {
                "query": query,
                "goals": goals,
                "mode": mode,
                "offset": offset,
                "cursor": revision,
                "now": time.time() if now is None else now,
            },
        )

    def _snapshot(self, owner, authorize, limit, selection):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise CoreStateError("invalid_appraisal_limit")
        with self.store._lock, self.store._transaction_sync():
            self._authorize(owner, authorize)
            rows = self.store._conn.execute(
                "SELECT record FROM appraisal_objects WHERE owner=? ORDER BY id",
                (owner,),
            ).fetchall()
            records = [json.loads(row[0]) for row in rows]
            valid = [r for r in records if self._sources_current(owner, r["id"])]
            revision = digest([(r["id"], r["version"]) for r in valid])
            selected = select(
                valid, owner=owner, revision=revision, limit=limit, **selection
            )
            self._authorize(owner, authorize)
            return {
                "ok": True,
                **selected,
                "total": len(valid),
                "unavailable": len(records) - len(valid),
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
        if type(before) is not int or before < 0:
            raise CoreStateError("invalid_appraisal_cursor")
        with self.store._lock, self.store._transaction_sync():
            self._authorize(owner, authorize)
            if not self._sources_current(owner, item_id):
                raise CoreStateError("appraisal_source_unavailable")
            rows = self.store._conn.execute(
                "SELECT record FROM appraisal_revisions WHERE owner=? AND id=? "
                "AND (?=0 OR revision<?) ORDER BY revision DESC LIMIT 21",
                (owner, item_id, before, before),
            ).fetchall()
            records = [public_record(json.loads(row[0])) for row in rows[:20]]
            self._authorize(owner, authorize)
            return {
                "ok": True,
                "history": records,
                "next_before": records[-1]["revision"] if len(rows) > 20 else None,
            }
