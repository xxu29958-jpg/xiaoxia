"""Authenticated admission and control endpoints; never hold the GPU request lock."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeAlias

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.jobs.artifacts import snapshot_source, verify_artifact
from services.jobs.runner import JobRunner
from services.jobs.store import JobError, JobStore

OwnerHeader: TypeAlias = Annotated[
    str, Header(alias="X-Owner-Id", min_length=1, max_length=128)
]
RequestKey: TypeAlias = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=160)
]


@dataclass
class JobService:
    store: JobStore
    runner: JobRunner
    root: Path
    reference: str
    signature: str


def service(request: Request) -> JobService:
    current = getattr(request.app.state, "media_jobs", None)
    if current is None:
        raise HTTPException(503, "jobs_not_ready")
    return current


CurrentService: TypeAlias = Annotated[JobService, Depends(service)]


class CoverJob(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source_path: str = Field(default="", max_length=4096)
    source_url: str = Field(default="", max_length=4096)
    acapella: bool = False
    pitch: int = Field(default=0, ge=-12, le=12)
    start_seconds: float = Field(default=0, ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def source_present(self):
        if bool(self.source_path) == bool(self.source_url):
            raise ValueError("Provide exactly one source_path or source_url")
        if self.source_url and not self.source_url.startswith(("https://", "http://")):
            raise ValueError("A recording URL must use HTTP or HTTPS")
        return self


def public_job(job: dict) -> dict:
    result = job["result"]
    return {
        **{
            name: job[name]
            for name in (
                "id",
                "state",
                "stage",
                "done",
                "total",
                "attempt",
                "created",
                "updated",
                "error",
            )
        },
        "cancel_requested": job["cancel_requested"],
        "delivery_suppressed": job["cancel_requested"],
        "artifact": {k: v for k, v in result.items() if k != "path"}
        if result
        else None,
        "delivery": "not_recorded",  # Generation is not a channel receipt or playback.
    }


def translate(error: JobError) -> HTTPException:
    code = str(error)
    status = {
        "job_not_found": 404,
        "job_queue_full": 429,
        "source_not_found": 422,
        "source_empty": 422,
        "source_too_large": 413,
    }.get(code, 409)
    return HTTPException(status, code)


def router(authenticate) -> APIRouter:
    api = APIRouter(prefix="/jobs", dependencies=[Depends(authenticate)])

    @api.post("/covers", status_code=202)
    def submit(
        body: CoverJob,
        owner: OwnerHeader,
        request_key: RequestKey,
        current: CurrentService,
    ):
        intent = body.model_dump()
        try:
            previous = current.store.find(owner, request_key, intent)
            payload = snapshot_source(current.root, intent) if previous is None else {}
            payload.update(
                reference_sha256=current.reference, runtime_signature=current.signature
            )
            job = previous or current.store.submit(owner, request_key, intent, payload)
        except JobError as error:
            raise translate(error) from error
        current.runner.wake.set()
        return public_job(job)

    @api.get("/{job_id}")
    def read(job_id: str, owner: OwnerHeader, current: CurrentService):
        try:
            return public_job(current.store.get(job_id, owner))
        except JobError as error:
            raise translate(error) from error

    @api.post("/{job_id}/cancel")
    def cancel(job_id: str, owner: OwnerHeader, current: CurrentService):
        try:
            return public_job(current.store.cancel(job_id, owner))
        except JobError as error:
            raise translate(error) from error

    @api.get("/{job_id}/events")
    def events(job_id: str, owner: OwnerHeader, current: CurrentService):
        try:
            return current.store.events(job_id, owner)
        except JobError as error:
            raise translate(error) from error

    @api.get("/{job_id}/result")
    def result(job_id: str, owner: OwnerHeader, current: CurrentService):
        try:
            job = current.store.get(job_id, owner)
            if job["cancel_requested"]:
                raise JobError("delivery_cancelled")
            if job["state"] != "completed":
                raise JobError("result_not_ready")
            file = verify_artifact(current.root, job["result"])
        except JobError as error:
            raise translate(error) from error
        return FileResponse(
            file,
            media_type="audio/wav",
            headers={"X-Artifact-SHA256": job["result"]["sha256"]},
        )

    return api
