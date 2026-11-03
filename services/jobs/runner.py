"""One bounded worker with renewable claims; completed media is never auto-sent."""

import threading
from collections.abc import Callable

from .store import InterruptedJob, JobError, JobStore

Renderer = Callable[[dict, Callable[..., None]], dict]


class JobRunner:
    def __init__(self, store: JobStore, render: Renderer, *, lease_seconds: float = 30):
        self.store, self.render, self.lease_seconds = store, render, lease_seconds
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._run, name="media-jobs", daemon=True)
        self.failure: str | None = None

    def start(self) -> None:
        self.thread.start()

    def close(self, timeout: float = 5) -> None:
        self.stopping.set()
        self.wake.set()
        self.thread.join(timeout)
        # An unfinished request keeps its lease until expiry. It is never marked done.

    def _run(self) -> None:
        while not self.stopping.is_set():
            try:
                job = self.store.claim(lease_seconds=self.lease_seconds)
                if job is not None:
                    self.perform(job)
                    continue
            except Exception as error:
                self.failure = type(error).__name__
            self.wake.wait(0.5)
            self.wake.clear()

    def perform(self, job: dict) -> None:
        finished = threading.Event()
        lost = threading.Event()

        def checkpoint(stage=None, done=0, total=None):
            if self.stopping.is_set() or lost.is_set():
                raise InterruptedJob("worker_stopping")
            self.store.checkpoint(job["id"], job["token"], stage, done, total)

        def renew():
            while not finished.wait(self.lease_seconds / 3):
                if self.stopping.is_set():
                    lost.set()
                    return
                try:
                    self.store.heartbeat(
                        job["id"], job["token"], lease_seconds=self.lease_seconds
                    )
                except Exception:
                    lost.set()
                    return

        heartbeat = threading.Thread(target=renew, name="media-job-lease", daemon=True)
        heartbeat.start()
        try:
            checkpoint()
            result = self.render(job, checkpoint)
            checkpoint()
            self.store.complete(job["id"], job["token"], result)
        except InterruptedJob as error:
            if not self.stopping.is_set() and not lost.is_set():
                self.store.end_attempt(job["id"], job["token"], str(error))
        except Exception as error:
            reason = str(error) if isinstance(error, JobError) else "generation_failed"
            self.store.end_attempt(job["id"], job["token"], reason)
        finally:
            finished.set()
            heartbeat.join()
