"""SQLite is the sole authority for admission, claims, cancellation and completion."""

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .schema import SCHEMA_VERSION, STATEMENTS


class JobError(Exception):
    """A stable reason code, suitable for an API response without private content."""


class InterruptedJob(JobError):
    """The job has been cancelled, stopped or fenced by a newer worker."""


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def intent_hash(intent: dict) -> str:
    return hashlib.sha256(canonical(intent).encode()).hexdigest()


def decode(row: sqlite3.Row) -> dict:
    job = dict(row)
    job["payload"] = json.loads(job["payload"])
    job["result"] = json.loads(job["result"]) if job["result"] else None
    job["cancel_requested"] = bool(job["cancel_requested"])
    return job


def record(db: sqlite3.Connection, job_id: str, kind: str, at: float, **detail) -> None:
    db.execute(
        "INSERT INTO job_events(job_id,kind,at,detail) VALUES(?,?,?,?)",
        (job_id, kind, at, canonical(detail)),
    )


class JobStore:
    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        capacity: int = 32,
        max_attempts: int = 3,
    ):
        self.path, self.clock = path, clock
        self.capacity, self.max_attempts = capacity, max_attempts
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise JobError("unsupported_job_schema")
            for statement in STATEMENTS:
                db.execute(statement)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def find(self, owner: str, request_key: str, intent: dict) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE owner=? AND request_key=?",
                (owner, request_key),
            ).fetchone()
        if row is not None and row["intent_hash"] != intent_hash(intent):
            raise JobError("idempotency_conflict")
        return decode(row) if row else None

    def submit(self, owner: str, request_key: str, intent: dict, payload: dict) -> dict:
        if not owner or len(owner) > 128 or not request_key or len(request_key) > 160:
            raise JobError("invalid_job_identity")
        digest, now = intent_hash(intent), self.clock()
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE owner=? AND request_key=?",
                (owner, request_key),
            ).fetchone()
            if row:
                if row["intent_hash"] != digest:
                    raise JobError("idempotency_conflict")
                return decode(row)
            active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling')"
            ).fetchone()[0]
            if active >= self.capacity:
                raise JobError("job_queue_full")
            job_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO jobs(id,owner,request_key,intent_hash,payload,state,created,updated) VALUES(?,?,?,?,?,'queued',?,?)",
                (job_id, owner, request_key, digest, canonical(payload), now, now),
            )
            record(db, job_id, "submitted", now)
            return decode(
                db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )

    def get(self, job_id: str, owner: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)
            ).fetchone()
        if row is None:
            raise JobError("job_not_found")
        return decode(row)

    def cancel(self, job_id: str, owner: str) -> dict:
        now = self.clock()
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND owner=?", (job_id, owner)
            ).fetchone()
            if row is None:
                raise JobError("job_not_found")
            if not row["cancel_requested"]:
                state = {"queued": "cancelled", "running": "cancelling"}.get(
                    row["state"], row["state"]
                )
                db.execute(
                    "UPDATE jobs SET cancel_requested=1,state=?,updated=? WHERE id=?",
                    (state, now, job_id),
                )
                record(db, job_id, "cancel_requested", now, generation_state=state)
            return decode(
                db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            )

    def claim(self, *, lease_seconds: float = 30) -> dict | None:
        now = self.clock()
        with self.connection(write=True) as db:
            rows = db.execute(
                "SELECT * FROM jobs WHERE state='queued' OR (state IN ('running','cancelling') AND lease_until<=?) ORDER BY created,id",
                (now,),
            ).fetchall()
            for row in rows:
                if row["cancel_requested"] or row["attempt"] >= self.max_attempts:
                    state = "cancelled" if row["cancel_requested"] else "failed"
                    db.execute(
                        "UPDATE jobs SET state=?,error=?,token=NULL,lease_until=NULL,updated=? WHERE id=?",
                        (
                            state,
                            "worker_lost" if state == "failed" else None,
                            now,
                            row["id"],
                        ),
                    )
                    record(db, row["id"], state, now, reason="recovery")
                    continue
                token = uuid.uuid4().hex
                db.execute(
                    "UPDATE jobs SET state='running',stage='starting',done=0,total=NULL,attempt=attempt+1,token=?,lease_until=?,updated=? WHERE id=?",
                    (token, now + lease_seconds, now, row["id"]),
                )
                record(
                    db,
                    row["id"],
                    "claimed",
                    now,
                    attempt=row["attempt"] + 1,
                    recovered=row["state"] != "queued",
                )
                return decode(
                    db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
                )
        return None

    def _current(
        self, db: sqlite3.Connection, job_id: str, token: str, now: float
    ) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if (
            row is None
            or row["token"] != token
            or row["lease_until"] is None
            or row["lease_until"] <= now
        ):
            raise InterruptedJob("stale_job_claim")
        if row["cancel_requested"]:
            raise InterruptedJob("job_cancelled")
        if row["state"] != "running":
            raise InterruptedJob("job_not_running")
        return row

    def checkpoint(
        self,
        job_id: str,
        token: str,
        stage: str | None = None,
        done: int = 0,
        total: int | None = None,
    ) -> None:
        if done < 0 or (total is not None and (total < 0 or done > total)):
            raise JobError("invalid_job_progress")
        now = self.clock()
        with self.connection(write=True) as db:
            row = self._current(db, job_id, token, now)
            if stage is not None and (stage, done, total) != (
                row["stage"],
                row["done"],
                row["total"],
            ):
                db.execute(
                    "UPDATE jobs SET stage=?,done=?,total=?,updated=? WHERE id=?",
                    (stage, done, total, now, job_id),
                )
                record(
                    db,
                    job_id,
                    "progress",
                    now,
                    stage=stage,
                    done=done,
                    total=total,
                    attempt=row["attempt"],
                )

    def heartbeat(self, job_id: str, token: str, *, lease_seconds: float = 30) -> None:
        now = self.clock()
        with self.connection(write=True) as db:
            self._current(db, job_id, token, now)
            db.execute(
                "UPDATE jobs SET lease_until=? WHERE id=?",
                (now + lease_seconds, job_id),
            )

    def claimed_payload(self, job_id: str, token: str) -> dict:
        with self.connection() as db:
            row = self._current(db, job_id, token, self.clock())
            return json.loads(row["payload"])

    def complete(self, job_id: str, token: str, result: dict) -> None:
        now = self.clock()
        with self.connection(write=True) as db:
            self._current(db, job_id, token, now)
            db.execute(
                "UPDATE jobs SET state='completed',stage='completed',result=?,token=NULL,lease_until=NULL,updated=? WHERE id=?",
                (canonical(result), now, job_id),
            )
            record(db, job_id, "generation_completed", now, sha256=result.get("sha256"))

    def bind_source(self, job_id: str, token: str, source: dict) -> None:
        """Commit the downloaded recording before using it for generation."""
        now = self.clock()
        with self.connection(write=True) as db:
            row = self._current(db, job_id, token, now)
            payload = json.loads(row["payload"])
            previous = payload.get("source_sha256")
            if previous and previous != source["sha256"]:
                raise JobError("job_source_changed")
            payload.update(
                source_path=source["path"],
                source_sha256=source["sha256"],
                source_bytes=source["bytes"],
                origin_url=payload.get("source_url") or payload.get("origin_url", ""),
                source_url="",
            )
            db.execute(
                "UPDATE jobs SET payload=?,updated=? WHERE id=?",
                (canonical(payload), now, job_id),
            )
            if not previous:
                record(
                    db,
                    job_id,
                    "source_bound",
                    now,
                    sha256=source["sha256"],
                    bytes=source["bytes"],
                )

    def end_attempt(self, job_id: str, token: str, error: str) -> None:
        now = self.clock()
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND token=? AND state IN ('running','cancelling')",
                (job_id, token),
            ).fetchone()
            if row is None or row["lease_until"] <= now:
                return
            state = "cancelled" if row["cancel_requested"] else "failed"
            db.execute(
                "UPDATE jobs SET state=?,stage=?,error=?,token=NULL,lease_until=NULL,updated=? WHERE id=?",
                (state, state, error, now, job_id),
            )
            record(db, job_id, state, now, error=error)

    def events(self, job_id: str, owner: str) -> list[dict]:
        self.get(job_id, owner)
        with self.connection() as db:
            rows = db.execute(
                "SELECT sequence,kind,at,detail FROM job_events WHERE job_id=? ORDER BY sequence DESC LIMIT 128",
                (job_id,),
            ).fetchall()
        return [
            {**dict(row), "detail": json.loads(row["detail"])} for row in reversed(rows)
        ]
