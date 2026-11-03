"""Kill actual workers around result commitment, recover and verify retained audio."""

import multiprocessing as mp
import tempfile
import unittest
from pathlib import Path

from support import wait_for, wav_bytes

from services.jobs.artifacts import describe_artifact, verify_artifact
from services.jobs.runner import JobRunner
from services.jobs.store import JobStore


def write_result(root, job):
    file = root / "attempts" / job["token"] / "result.wav"
    file.parent.mkdir(parents=True)
    file.write_bytes(wav_bytes())
    return describe_artifact(root, file, engine="fixture-wav")


def crash_worker(root, ready, committed):
    root = Path(root)
    store = JobStore(root / "state.sqlite3")

    def render(job, check):
        result = write_result(root, job)
        if not committed:
            ready.set()
            mp.Event().wait(60)
        return result

    runner = JobRunner(store, render, lease_seconds=0.6)
    runner.start()
    if committed:
        wait_for(
            lambda: store.find("owner", "request", {}),
            lambda job: job is not None and job["state"] == "completed",
        )
        ready.set()
    mp.Event().wait(60)


class RecoveryTests(unittest.TestCase):
    def test_restart_before_and_after_result_commit(self):
        context = mp.get_context("spawn")
        for committed in (False, True):
            with (
                self.subTest(committed=committed),
                tempfile.TemporaryDirectory() as folder,
            ):
                root = Path(folder)
                store = JobStore(root / "state.sqlite3")
                job_id = store.submit("owner", "request", {}, {})["id"]
                ready = context.Event()
                worker = context.Process(
                    target=crash_worker, args=(str(root), ready, committed)
                )
                try:
                    worker.start()
                    self.assertTrue(ready.wait(10))
                finally:
                    if worker.is_alive():
                        worker.terminate()
                    worker.join(10)
                interrupted = store.get(job_id, "owner")
                self.assertEqual(
                    interrupted["state"], "completed" if committed else "running"
                )
                orphan = next((root / "attempts").glob("*/result.wav"))
                recovered = JobStore(root / "state.sqlite3")
                runner = JobRunner(
                    recovered,
                    lambda job, check: write_result(root, job),
                    lease_seconds=0.6,
                )
                runner.start()
                try:
                    saved = wait_for(
                        lambda: recovered.get(job_id, "owner"),
                        lambda job: job["state"] == "completed",
                    )
                finally:
                    runner.close()
                actual = verify_artifact(root, saved["result"])
                self.assertEqual(actual.read_bytes(), wav_bytes())
                self.assertEqual(saved["attempt"], 1 if committed else 2)
                self.assertEqual(actual == orphan, committed)
                self.assertTrue(orphan.exists())
                completed = [
                    e
                    for e in recovered.events(job_id, "owner")
                    if e["kind"] == "generation_completed"
                ]
                self.assertEqual(len(completed), 1)


if __name__ == "__main__":
    unittest.main()
