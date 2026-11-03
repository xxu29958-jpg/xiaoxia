"""Dispatch through existing GPU ordering and retain only verified WAV results."""

import os
import wave
from contextlib import contextmanager
from pathlib import Path

import requests

from services.jobs.artifacts import MAX_MEDIA_BYTES, describe_artifact, verify_artifact
from services.jobs.store import JobError
from services.voice.job_execution import COVER_PARAMETERS


class RemoteCoverRenderer:
    def __init__(
        self,
        root: Path,
        lock,
        *,
        key: str,
        engine: str,
        reference: str,
        signature: str,
        singer_url="http://127.0.0.1:6192",
        speech_url="http://127.0.0.1:6191",
    ):
        self.root, self.lock, self.engine = root, lock, engine
        self.reference, self.signature = reference, signature
        self.singer_url, self.speech_url = singer_url, speech_url
        self.client = requests.Session()
        self.client.trust_env = False
        self.client.headers["Authorization"] = "Bearer " + key

    @contextmanager
    def resource(self, checkpoint):
        acquired = False
        try:
            if self.engine == "fish":
                checkpoint("waiting_for_gpu")
                while not self.lock.acquire(timeout=0.1):
                    checkpoint()
                acquired = True
                checkpoint("preparing")
                self.client.post(
                    self.speech_url + "/release-gpu", timeout=60
                ).raise_for_status()
            checkpoint()
            yield
        finally:
            if acquired:
                self.lock.release()

    def __call__(self, job: dict, checkpoint) -> dict:
        payload = job["payload"]
        if (self.reference, self.signature) != (
            payload["reference_sha256"],
            payload["runtime_signature"],
        ):
            raise JobError("job_runtime_changed")
        if payload.get("source_sha256"):
            verify_artifact(
                self.root,
                {
                    "path": payload["source_path"],
                    "sha256": payload["source_sha256"],
                    "bytes": payload["source_bytes"],
                },
            )
        status = self.client.get(self.singer_url + "/health", timeout=5)
        status.raise_for_status()
        health = status.json()
        if health.get("job_protocol") != "cover-v1":
            raise JobError("backend_job_protocol_unavailable")
        if (health.get("reference_sha256"), health.get("runtime_signature")) != (
            payload["reference_sha256"],
            payload["runtime_signature"],
        ):
            raise JobError("job_runtime_changed")
        folder = self.root / "attempts" / job["id"] / job["token"]
        folder.mkdir(parents=True, exist_ok=True)
        with self.resource(checkpoint):
            checkpoint("rendering")
            with self.client.post(
                self.singer_url + "/cover",
                json={k: payload[k] for k in COVER_PARAMETERS},
                headers={"X-Job-Id": job["id"], "X-Job-Token": job["token"]},
                timeout=(5, 3600),
                stream=True,
            ) as response:
                response.raise_for_status()
                if response.headers.get("X-Job-Control") != "cover-v1":
                    raise JobError("backend_job_protocol_unavailable")
                output = self.receive(response, folder, checkpoint)
                model = response.headers["X-Voice-Engine"]
        with wave.open(str(output), "rb") as audio:
            if audio.getnframes() == 0 or audio.getnchannels() not in (1, 2):
                raise JobError("invalid_generated_audio")
            duration = audio.getnframes() / audio.getframerate()
        return describe_artifact(
            self.root,
            output,
            engine=model,
            audio_seconds=duration,
            reference_sha256=payload["reference_sha256"],
            runtime_signature=payload["runtime_signature"],
        )

    def receive(self, response, folder: Path, checkpoint) -> Path:
        pending, target = folder / "result.download", folder / "result.wav"
        length = 0
        with pending.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                checkpoint("receiving", length)
                length += len(chunk)
                if length > MAX_MEDIA_BYTES:
                    raise JobError("result_too_large")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        checkpoint("finalizing")
        pending.replace(target)
        return target
