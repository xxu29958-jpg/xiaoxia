"""Real loopback HTTP and shared SQLite claims fence model RPC and late output."""

import tempfile
import threading
import unittest
from pathlib import Path

import requests
from support import ModelServer, wait_for, wav_bytes

from services.jobs.artifacts import snapshot_source, verify_artifact
from services.jobs.runner import JobRunner
from services.jobs.store import JobError, JobStore
from services.voice.job_api import CoverJob
from services.voice.job_execution import JobExecution
from services.voice.job_renderer import RemoteCoverRenderer


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = JobStore(self.root / "state.sqlite3")
        source = self.root / "input.wav"
        source.write_bytes(wav_bytes())
        self.intent = CoverJob(source_path=str(source)).model_dump()
        self.payload = snapshot_source(self.root, self.intent)
        self.payload.update(reference_sha256="reference", runtime_signature="signature")
        self.server = ModelServer(self.root)
        self.addCleanup(self.server.close)
        self.renderer = RemoteCoverRenderer(
            self.root,
            threading.Lock(),
            key="test-key",
            engine="fixture",
            reference="reference",
            signature="signature",
            singer_url=self.server.url,
        )
        self.addCleanup(self.renderer.client.close)
        self.runner = JobRunner(self.store, self.renderer)
        self.job = self.store.submit("owner", "request", self.intent, self.payload)

    def test_rpc_wav_readback_and_persisted_backend_progress(self):
        self.runner.start()
        self.addCleanup(self.runner.close)
        job = wait_for(
            lambda: self.store.get(self.job["id"], "owner"),
            lambda job: job["state"] == "completed",
        )
        self.assertEqual(
            verify_artifact(self.root, job["result"]).read_bytes(), wav_bytes()
        )
        self.assertEqual(job["result"]["audio_seconds"], 0.1)
        progress = [
            e["detail"]
            for e in self.store.events(job["id"], "owner")
            if e["kind"] == "progress"
        ]
        self.assertTrue(
            any(e["stage"] == "converting" and e["done"] == 2 for e in progress)
        )
        self.assertEqual(self.server.calls, 1)

    def test_cancel_reaches_backend_and_late_audio_cannot_complete(self):
        self.server.release.clear()
        self.runner.start()
        self.addCleanup(self.runner.close)
        self.assertTrue(self.server.started.wait(2))
        self.store.cancel(self.job["id"], "owner")
        self.server.release.set()
        job = wait_for(
            lambda: self.store.get(self.job["id"], "owner"),
            lambda job: job["state"] == "cancelled",
        )
        self.assertEqual(self.server.failure, "job_cancelled")
        self.assertIsNone(job["result"])

    def test_old_backend_or_changed_runtime_never_starts_model(self):
        claim = self.store.claim()
        assert claim is not None
        for field, bad_value, reason in (
            ("protocol", "", "backend_job_protocol_unavailable"),
            ("reference", "different", "job_runtime_changed"),
            ("signature", "different", "job_runtime_changed"),
        ):
            original = getattr(self.server, field)
            setattr(self.server, field, bad_value)
            with self.subTest(field=field), self.assertRaisesRegex(JobError, reason):
                self.renderer(claim, lambda *args: None)
            setattr(self.server, field, original)
        self.assertEqual(self.server.calls, 0)

    def test_backend_rejects_changed_body_before_generation(self):
        claim = self.store.claim()
        self.assertIsNotNone(claim)
        assert claim is not None
        parameters = CoverJob(**{k: self.payload[k] for k in self.intent}).model_dump()
        parameters["pitch"] = 2
        with requests.Session() as client:
            client.trust_env = False
            response = client.post(
                self.server.url + "/cover",
                json=parameters,
                timeout=2,
                headers={
                    "Authorization": "Bearer test-key",
                    "X-Job-Id": claim["id"],
                    "X-Job-Token": claim["token"],
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "job_parameters_changed")
        self.assertEqual(self.server.calls, 0)

    def test_job_requires_both_claim_headers_and_keeps_legacy_calls(self):
        with self.assertRaisesRegex(JobError, "incomplete_job_claim"):
            JobExecution(self.root, self.job["id"], None)
        JobExecution(self.root, None, None).validate({}, "", "")


if __name__ == "__main__":
    unittest.main()
