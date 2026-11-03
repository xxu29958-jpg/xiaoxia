"""Durable chat requests and delivery facts; generation remains owned by the gateway."""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class PerformanceError(RuntimeError):
    """Stable local reason without provider payloads or private source URLs."""


def encoded(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def unpack(row) -> dict:
    result = dict(row)
    for name in ("parameters", "destination", "generation", "artifact", "receipt"):
        result[name] = json.loads(result[name]) if result[name] else None
    result["cancel_requested"] = bool(result["cancel_requested"])
    return result


class PerformanceStore:
    def __init__(self, path: Path, *, clock=time.time):
        self.path, self.clock = path, clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise PerformanceError("unsupported_performance_schema")
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, destination TEXT NOT NULL,
                title TEXT NOT NULL, parameters TEXT NOT NULL, job_id TEXT,
                generation TEXT, state TEXT NOT NULL DEFAULT 'pending',
                cancel_requested INTEGER NOT NULL DEFAULT 0, token TEXT,
                lease_until REAL, not_before REAL NOT NULL DEFAULT 0,
                artifact TEXT, receipt TEXT, error TEXT,
                created REAL NOT NULL, updated REAL NOT NULL)""")
            db.execute(
                "CREATE INDEX IF NOT EXISTS request_owner ON requests(owner,created)"
            )
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def submit(
        self, owner, destination, message_id, title, parameters, identity
    ) -> dict:
        if not message_id:
            raise PerformanceError("missing_native_message_id")
        key = hashlib.sha256(
            encoded([owner, destination, str(message_id), identity]).encode()
        ).hexdigest()
        now = self.clock()
        with self.connection() as db:
            previous = db.execute(
                "SELECT * FROM requests WHERE id=?", (key,)
            ).fetchone()
            if previous:
                return unpack(previous)
            active = db.execute(
                "SELECT COUNT(*) FROM requests WHERE state IN "
                "('pending','waiting','preparing','sending')"
            ).fetchone()[0]
            if active >= 32:
                raise PerformanceError("performance_queue_full")
            db.execute(
                "INSERT INTO requests(id,owner,destination,title,parameters,created,updated) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    owner,
                    encoded(destination),
                    title,
                    encoded(parameters),
                    now,
                    now,
                ),
            )
            return unpack(
                db.execute("SELECT * FROM requests WHERE id=?", (key,)).fetchone()
            )

    def get(self, owner: str, request_id: str = "") -> dict:
        with self.connection() as db:
            if request_id:
                row = db.execute(
                    "SELECT * FROM requests WHERE id=? AND owner=?", (request_id, owner)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM requests WHERE owner=? ORDER BY created DESC,id DESC LIMIT 1",
                    (owner,),
                ).fetchone()
        if row is None:
            raise PerformanceError("performance_not_found")
        return unpack(row)

    def cancel(self, owner: str, request_id: str = "") -> dict:
        request = self.get(owner, request_id)
        with self.connection() as db:
            db.execute(
                "UPDATE requests SET cancel_requested=1,not_before=0,updated=? WHERE id=? AND owner=?",
                (self.clock(), request["id"], owner),
            )
        return self.get(owner, request["id"])

    def claim(self, lease: float = 60) -> dict | None:
        now = self.clock()
        with self.connection() as db:
            # Retain the token so a late, genuine receipt can still reconcile this fact.
            db.execute(
                "UPDATE requests SET state='unknown',receipt=?,error='delivery_worker_lost',updated=? "
                "WHERE state='sending' AND lease_until<=?",
                (encoded({"kind": "unknown", "reason": "worker_lost"}), now, now),
            )
            row = db.execute(
                "SELECT * FROM requests WHERE state IN ('pending','waiting','preparing') "
                "AND not_before<=? AND (lease_until IS NULL OR lease_until<=?) "
                "ORDER BY created,id LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute(
                "UPDATE requests SET token=?,lease_until=?,updated=? WHERE id=?",
                (token, now + lease, now, row["id"]),
            )
            return unpack(
                db.execute("SELECT * FROM requests WHERE id=?", (row["id"],)).fetchone()
            )

    def current(self, db, request_id, token) -> dict:
        row = db.execute(
            "SELECT * FROM requests WHERE id=? AND token=?", (request_id, token)
        ).fetchone()
        if (
            row is None
            or row["lease_until"] is None
            or row["lease_until"] <= self.clock()
        ):
            raise PerformanceError("stale_performance_claim")
        return unpack(row)

    def check(self, request_id, token, *, allow_cancel=False) -> dict:
        with self.connection() as db:
            request = self.current(db, request_id, token)
        if request["cancel_requested"] and not allow_cancel:
            raise PerformanceError("performance_cancelled")
        return request

    def renew(self, request_id, token, lease: float = 60):
        with self.connection() as db:
            self.current(db, request_id, token)
            db.execute(
                "UPDATE requests SET lease_until=? WHERE id=?",
                (self.clock() + lease, request_id),
            )

    def observe_generation(self, request_id, token, job):
        with self.connection() as db:
            current = self.current(db, request_id, token)
            if current["job_id"] and current["job_id"] != job["id"]:
                raise PerformanceError("generation_identity_changed")
            db.execute(
                "UPDATE requests SET job_id=?,generation=?,state='waiting',updated=? WHERE id=?",
                (job["id"], encoded(job), self.clock(), request_id),
            )

    def defer(self, request_id, token, *, error=None, delay=2):
        with self.connection() as db:
            request = self.current(db, request_id, token)
            if request["state"] == "sending":
                raise PerformanceError("cannot_retry_uncertain_delivery")
            db.execute(
                "UPDATE requests SET state=?,error=?,not_before=?,token=NULL,lease_until=NULL,updated=? WHERE id=?",
                (
                    "waiting" if request["job_id"] else "pending",
                    error,
                    self.clock() + delay,
                    self.clock(),
                    request_id,
                ),
            )

    def finish(self, request_id, token, state, error=None):
        if state not in {"failed", "cancelled"}:
            raise PerformanceError("invalid_performance_end_state")
        with self.connection() as db:
            request = self.current(db, request_id, token)
            if request["state"] == "sending":
                raise PerformanceError("cannot_erase_delivery_attempt")
            db.execute(
                "UPDATE requests SET state=?,error=?,token=NULL,lease_until=NULL,updated=? WHERE id=?",
                (state, error, self.clock(), request_id),
            )

    def preparing(self, request_id, token):
        with self.connection() as db:
            request = self.current(db, request_id, token)
            if request["cancel_requested"]:
                raise PerformanceError("performance_cancelled")
            db.execute(
                "UPDATE requests SET state='preparing',updated=? WHERE id=?",
                (self.clock(), request_id),
            )

    def begin_send(self, request_id, token, artifact):
        with self.connection() as db:
            request = self.current(db, request_id, token)
            if request["cancel_requested"]:
                raise PerformanceError("performance_cancelled")
            generated = request["generation"]["state"]
            expected = "failed" if artifact["format"] == "text" else "completed"
            if request["state"] != "preparing" or generated != expected:
                raise PerformanceError("performance_not_ready")
            db.execute(
                "UPDATE requests SET state='sending',artifact=?,updated=? WHERE id=?",
                (encoded(artifact), self.clock(), request_id),
            )

    def receipt(self, request_id, token, receipt):
        states = {
            "server_accepted": "accepted",
            "adapter_returned": "unknown",
            "unknown": "unknown",
        }
        state = states.get(receipt.get("kind"))
        if state is None:
            raise PerformanceError("invalid_delivery_receipt")
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=? AND token=? AND state IN ('sending','unknown')",
                (request_id, token),
            ).fetchone()
            if row is None:
                raise PerformanceError("stale_delivery_receipt")
            db.execute(
                "UPDATE requests SET state=?,receipt=?,error=NULL,lease_until=NULL,updated=? WHERE id=?",
                (state, encoded(receipt), self.clock(), request_id),
            )
