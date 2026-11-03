"""The model worker checks the gateway's claim at real processing boundaries."""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from services.jobs.artifacts import MAX_MEDIA_BYTES, describe_artifact, verify_artifact
from services.jobs.store import JobError, JobStore

COVER_PARAMETERS = (
    "source_path",
    "source_url",
    "acapella",
    "pitch",
    "start_seconds",
    "duration_seconds",
)


def runtime_signature(project: Path) -> str:
    digest = hashlib.sha256()
    for name in (
        "services/voice/cover_pipeline.py",
        "services/voice/cover_source.py",
        "services/voice/job_execution.py",
        "services/voice/server_singing.py",
        "services/voice/resident_pipeline.py",
        "services/voice/seed_runtime.py",
        "vendor/sources.json",
        "models.manifest.json",
    ):
        digest.update(name.encode())
        digest.update((project / name).read_bytes())
    return digest.hexdigest()


class JobExecution:
    def __init__(self, root: Path, job_id: str | None, token: str | None):
        if bool(job_id) != bool(token):
            raise JobError("incomplete_job_claim")
        self.root, self.job_id, self.token = root, job_id, token
        self.store = JobStore(root / "state.sqlite3") if job_id else None
        self.checkpoint()

    def validate(self, parameters: dict, reference: str, signature: str) -> None:
        if self.store is None:
            return
        assert self.job_id is not None and self.token is not None
        payload = self.store.claimed_payload(self.job_id, self.token)
        if parameters != {name: payload[name] for name in COVER_PARAMETERS}:
            raise JobError("job_parameters_changed")
        if (reference, signature) != (
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

    def checkpoint(self, stage=None, done=0, total=None) -> None:
        if self.store is not None:
            assert self.job_id is not None and self.token is not None
            self.store.checkpoint(self.job_id, self.token, stage, done, total)

    def bind_source(self, source: Path) -> None:
        if self.store is not None:
            assert self.job_id is not None and self.token is not None
            self.store.bind_source(
                self.job_id, self.token, describe_artifact(self.root, source)
            )

    def download_progress(self, progress: Mapping[str, Any]) -> None:
        done = int(progress.get("downloaded_bytes", 0))
        if self.store is not None and done > MAX_MEDIA_BYTES:
            raise JobError("source_too_large")
        total = progress.get("total_bytes")
        self.checkpoint("downloading", done, max(done, int(total)) if total else None)
