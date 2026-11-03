"""HTTP consumer journeys use a real journal, worker, immutable input and WAV file."""

import asyncio
import hashlib
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from support import wait_for, wav_bytes

from services.jobs.artifacts import describe_artifact
from services.jobs.runner import JobRunner
from services.jobs.store import JobStore
from services.voice.job_api import JobService, router

HEADERS = {"Authorization": "Bearer test-key", "X-Owner-Id": "owner"}


def authenticate(authorization: str | None = Header(default=None)):
    if authorization != HEADERS["Authorization"]:
        raise HTTPException(401, "Unauthorized")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original.wav"
        self.source.write_bytes(wav_bytes())
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.calls = 0
        self.render = self.render_fixture
        self.app = FastAPI(lifespan=self.lifespan)
        self.app.include_router(router(authenticate))

    @asynccontextmanager
    async def lifespan(self, app):
        self.store = JobStore(self.root / "jobs/state.sqlite3")
        runner = JobRunner(self.store, self.render)
        app.state.media_jobs = JobService(
            self.store, runner, self.root / "jobs", "reference", "signature"
        )
        runner.start()
        try:
            yield
        finally:
            self.release.set()
            await asyncio.to_thread(runner.close)
            app.state.media_jobs = None

    def render_fixture(self, job, check):
        self.calls += 1
        check("converting", 1, 2)
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("test_timeout")
        file = self.root / "jobs" / f"{job['id']}.wav"
        file.write_bytes(wav_bytes())
        return describe_artifact(self.root / "jobs", file, engine="fixture-wav")

    def submit(self, client, key="request", **changes):
        return client.post(
            "/jobs/covers",
            headers={**HEADERS, "Idempotency-Key": key},
            json={"source_path": str(self.source), **changes},
        )

    def completed(self, client, job_id):
        return wait_for(
            lambda: client.get(f"/jobs/{job_id}", headers=HEADERS).json(),
            lambda job: job["state"] == "completed",
        )

    def test_complete_readback_restart_duplicate_and_corruption(self):
        with TestClient(self.app) as client:
            submitted = self.submit(client)
            self.assertEqual(submitted.status_code, 202)
            job_id = submitted.json()["id"]
            complete = self.completed(client, job_id)
            self.assertEqual(complete["delivery"], "not_recorded")
            self.assertNotIn("path", complete["artifact"])
            self.assertNotIn("token", complete)
            result = client.get(f"/jobs/{job_id}/result", headers=HEADERS)
            self.assertEqual(result.content, wav_bytes())
            self.assertEqual(
                result.headers["X-Artifact-SHA256"],
                hashlib.sha256(result.content).hexdigest(),
            )
            original = self.store.get(job_id, "owner")["payload"]["source_path"]
            self.source.write_bytes(b"original changed after admission")
            self.assertEqual(Path(original).read_bytes(), wav_bytes())
            route_count = len(self.app.routes)
        with TestClient(self.app) as client:
            repeated = self.submit(client)
            self.assertEqual(repeated.json()["id"], job_id)
            self.assertEqual(repeated.json()["state"], "completed")
            self.assertEqual(self.calls, 1)
            self.assertEqual(len(self.app.routes), route_count)
            events = client.get(f"/jobs/{job_id}/events", headers=HEADERS).json()
            self.assertEqual(
                [e["kind"] for e in events].count("generation_completed"), 1
            )
            artifact = self.store.get(job_id, "owner")["result"]["path"]
            Path(artifact).write_bytes(b"damaged result")
            result = client.get(f"/jobs/{job_id}/result", headers=HEADERS)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()["detail"], "artifact_changed")

    def test_cancel_while_worker_waits_suppresses_late_result(self):
        self.release.clear()
        with TestClient(self.app) as client:
            job_id = self.submit(client).json()["id"]
            self.assertTrue(self.started.wait(2))
            progress = client.get(f"/jobs/{job_id}", headers=HEADERS).json()
            self.assertEqual(
                (progress["stage"], progress["done"], progress["total"]),
                ("converting", 1, 2),
            )
            cancel = client.post(f"/jobs/{job_id}/cancel", headers=HEADERS)
            self.assertEqual(cancel.json()["state"], "cancelling")
            self.release.set()
            job = wait_for(
                lambda: self.store.get(job_id, "owner"),
                lambda job: job["state"] == "cancelled",
            )
            self.assertIsNone(job["result"])
            self.assertTrue((self.root / "jobs" / f"{job_id}.wav").exists())
            response = client.get(f"/jobs/{job_id}/result", headers=HEADERS)
            self.assertEqual(response.json()["detail"], "delivery_cancelled")

    def test_cancel_completed_keeps_generation_fact_and_suppresses_delivery(self):
        with TestClient(self.app) as client:
            job_id = self.submit(client).json()["id"]
            artifact = self.completed(client, job_id)["artifact"]
            cancelled = client.post(f"/jobs/{job_id}/cancel", headers=HEADERS).json()
            self.assertEqual(cancelled["state"], "completed")
            self.assertTrue(cancelled["delivery_suppressed"])
            self.assertEqual(cancelled["artifact"], artifact)
            self.assertEqual(
                client.get(f"/jobs/{job_id}/result", headers=HEADERS).status_code, 409
            )

    def test_identity_authentication_and_input_failures(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.post("/jobs/covers").status_code, 401)
            job_id = self.submit(client).json()["id"]
            foreign = {**HEADERS, "X-Owner-Id": "other-owner"}
            for suffix in ("", "/events", "/result"):
                self.assertEqual(
                    client.get(f"/jobs/{job_id}{suffix}", headers=foreign).status_code,
                    404,
                )
            self.assertEqual(
                client.post(f"/jobs/{job_id}/cancel", headers=foreign).status_code, 404
            )
            conflict = self.submit(client, pitch=1)
            self.assertEqual(conflict.json()["detail"], "idempotency_conflict")
            invalid = [
                {"source_url": "https://example.invalid/audio"},
                {"source_path": "", "source_url": "file:///private.wav"},
                {"source_path": ""},
                {"pitch": 13},
                {"duration_seconds": 0},
                {"unrecognized": "field"},
                {"source_path": str(self.root / "missing.wav")},
            ]
            for number, body in enumerate(invalid):
                with self.subTest(body=body):
                    self.assertEqual(
                        self.submit(client, key=str(number), **body).status_code, 422
                    )

    def test_missing_lifespan_is_unavailable(self):
        client = TestClient(self.app)
        self.assertEqual(self.submit(client).status_code, 503)


if __name__ == "__main__":
    unittest.main()
