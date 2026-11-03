"""Exercise the actual SQLite authority, including competing and killed processes."""

import multiprocessing as mp
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from services.jobs.store import InterruptedJob, JobError, JobStore


def competing_claim(database, barrier, answers):
    store = JobStore(Path(database), clock=lambda: 100)
    barrier.wait(10)
    job = store.claim()
    answers.put(job["token"] if job else None)


def interrupted_submit(database, ready, committed):
    import services.jobs.store as module

    store = JobStore(Path(database), clock=lambda: 100)
    if not committed:
        original = module.record

        def boundary(*args, **kwargs):
            original(*args, **kwargs)
            ready.set()
            mp.Event().wait(60)

        module.record = boundary
    store.submit("owner", "request", {"text": "intent"}, {"text": "source"})
    ready.set()
    mp.Event().wait(60)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "jobs.sqlite3"
        self.now = 100.0
        self.store = JobStore(self.path, clock=lambda: self.now)

    def submit(self, request="one", owner="owner"):
        return self.store.submit(
            owner, request, {"source": "recording"}, {"source": "snapshot"}
        )

    def test_duplicate_intent_commits_once_even_after_restart(self):
        first = self.submit()
        for _ in range(10):
            self.assertEqual(
                JobStore(self.path).submit(
                    "owner",
                    "one",
                    {"source": "recording"},
                    {"source": "ignored new runtime"},
                )["id"],
                first["id"],
            )
        self.assertEqual(
            [e["kind"] for e in self.store.events(first["id"], "owner")], ["submitted"]
        )
        with self.assertRaisesRegex(JobError, "idempotency_conflict"):
            self.store.submit("owner", "one", {"source": "different"}, {})

    def test_ownership_is_checked_before_read_cancel_or_events(self):
        job = self.submit()
        for action in (self.store.get, self.store.cancel, self.store.events):
            with self.assertRaisesRegex(JobError, "job_not_found"):
                action(job["id"], "another-owner")
        self.assertEqual(self.store.get(job["id"], "owner")["state"], "queued")

    def test_queue_limit_keeps_idempotent_retries_available(self):
        limited = JobStore(self.path, capacity=1)
        first = limited.submit("owner", "one", {}, {})
        self.assertEqual(limited.submit("owner", "one", {}, {})["id"], first["id"])
        with self.assertRaisesRegex(JobError, "job_queue_full"):
            limited.submit("owner", "two", {}, {})

    def test_cancel_before_claim_never_runs_and_is_idempotent(self):
        job = self.submit()
        self.store.cancel(job["id"], "owner")
        self.store.cancel(job["id"], "owner")
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get(job["id"], "owner")["state"], "cancelled")
        self.assertEqual(len(self.store.events(job["id"], "owner")), 2)

    def test_cancel_fences_completion_and_keeps_it_out_of_committed_facts(self):
        self.submit()
        job = self.store.claim()
        assert job is not None
        self.store.cancel(job["id"], "owner")
        with self.assertRaisesRegex(InterruptedJob, "job_cancelled"):
            self.store.complete(job["id"], job["token"], {"sha256": "late"})
        self.store.end_attempt(job["id"], job["token"], "job_cancelled")
        saved = JobStore(self.path).get(job["id"], "owner")
        self.assertEqual(saved["state"], "cancelled")
        self.assertIsNone(saved["result"])

    def test_cancelling_after_completion_retains_generated_fact_but_suppresses_delivery(
        self,
    ):
        self.submit()
        job = self.store.claim()
        assert job is not None
        self.store.complete(job["id"], job["token"], {"sha256": "completed"})
        saved = self.store.cancel(job["id"], "owner")
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["result"], {"sha256": "completed"})
        self.assertTrue(saved["cancel_requested"])

    def test_expired_worker_cannot_complete_or_report_progress_after_recovery(self):
        self.submit()
        old = self.store.claim()
        assert old is not None
        self.now += 31
        new = self.store.claim()
        assert new is not None
        self.assertEqual(new["attempt"], 2)
        self.assertNotEqual(new["token"], old["token"])
        for action in (
            lambda: self.store.complete(old["id"], old["token"], {}),
            lambda: self.store.checkpoint(old["id"], old["token"], "late"),
        ):
            with self.assertRaisesRegex(InterruptedJob, "stale_job_claim"):
                action()
        self.store.end_attempt(old["id"], old["token"], "old failure")
        self.store.complete(new["id"], new["token"], {"sha256": "new"})
        self.assertEqual(
            self.store.get(new["id"], "owner")["result"], {"sha256": "new"}
        )

    def test_renewal_prevents_claim_and_recovery_attempts_are_bounded(self):
        self.submit()
        job = self.store.claim()
        assert job is not None
        self.now += 20
        self.store.heartbeat(job["id"], job["token"])
        self.now += 15
        self.assertIsNone(self.store.claim())
        for _ in range(2):
            self.now += 31
            self.assertIsNotNone(self.store.claim())
        self.now += 31
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get(job["id"], "owner")["error"], "worker_lost")

    def test_bound_recording_survives_worker_restart_and_cannot_change(self):
        self.submit()
        job = self.store.claim()
        assert job is not None
        self.store.bind_source(
            job["id"],
            job["token"],
            {"path": "saved.wav", "sha256": "first", "bytes": 100},
        )
        self.now += 31
        resumed = self.store.claim()
        assert resumed is not None
        self.assertEqual(resumed["payload"]["source_sha256"], "first")
        with self.assertRaisesRegex(JobError, "job_source_changed"):
            self.store.bind_source(
                resumed["id"],
                resumed["token"],
                {"path": "other.wav", "sha256": "changed", "bytes": 100},
            )

    def test_future_schema_is_rejected_without_rewriting_it(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version=99")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(JobError, "unsupported_job_schema"):
            JobStore(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_two_processes_cannot_claim_the_same_job(self):
        self.submit()
        context = mp.get_context("spawn")
        barrier, answers = context.Barrier(3), context.Queue()
        workers = [
            context.Process(
                target=competing_claim, args=(str(self.path), barrier, answers)
            )
            for _ in range(2)
        ]
        try:
            for worker in workers:
                worker.start()
            barrier.wait(10)
            results = [answers.get(timeout=10) for _ in workers]
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(sum(value is not None for value in results), 1)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                worker.join()

    def test_process_death_before_and_after_transaction_commit(self):
        context = mp.get_context("spawn")
        for committed in (False, True):
            with self.subTest(committed=committed):
                database = self.path.with_name(f"crash-{committed}.sqlite3")
                ready = context.Event()
                worker = context.Process(
                    target=interrupted_submit, args=(str(database), ready, committed)
                )
                try:
                    worker.start()
                    self.assertTrue(ready.wait(10))
                finally:
                    if worker.is_alive():
                        worker.terminate()
                    worker.join(10)
                saved = JobStore(database).find("owner", "request", {"text": "intent"})
                self.assertEqual(saved is not None, committed)
                if saved:
                    self.assertEqual(saved["state"], "queued")


if __name__ == "__main__":
    unittest.main()
