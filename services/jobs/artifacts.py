"""Immutable media intake and verified attempt results, kept separate from delivery."""

import hashlib
import os
import tempfile
from pathlib import Path

from .store import JobError

MAX_MEDIA_BYTES = 2 * 1024**3


def snapshot_source(root: Path, request: dict) -> dict:
    payload = dict(request)
    if not request.get("source_path"):
        return payload
    source = Path(request["source_path"])
    if not source.is_file() or source.is_symlink():
        raise JobError("source_not_found")
    if source.stat().st_size > MAX_MEDIA_BYTES:
        raise JobError("source_too_large")
    folder = root / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="intake-", dir=folder, delete=False
    ) as temporary:
        pending = Path(temporary.name)
        try:
            digest, length = copy_source(source, temporary)
        except BaseException:
            temporary.close()
            pending.unlink(missing_ok=True)
            raise
    destination = folder / f"{digest}{source.suffix.lower()}"
    try:
        if destination.exists():
            verify_artifact(
                root, {"path": str(destination), "sha256": digest, "bytes": length}
            )
        else:
            # Same digest implies the same content; no job state is published yet.
            pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)
    payload.update(
        source_path=str(destination), source_sha256=digest, source_bytes=length
    )
    return payload


def copy_source(source: Path, output) -> tuple[str, int]:
    digest, length = hashlib.sha256(), 0
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            length += len(chunk)
            if length > MAX_MEDIA_BYTES:
                raise JobError("source_too_large")
            output.write(chunk)
            digest.update(chunk)
    output.flush()
    os.fsync(output.fileno())
    if length == 0:
        raise JobError("source_empty")
    return digest.hexdigest(), length


def describe_artifact(root: Path, file: Path, **metadata) -> dict:
    if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
        raise JobError("artifact_outside_job_store")
    with file.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        **metadata,
        "path": str(file.resolve()),
        "sha256": digest,
        "bytes": file.stat().st_size,
    }


def verify_artifact(root: Path, result: dict) -> Path:
    file = Path(result["path"])
    if not file.is_file():
        raise JobError("artifact_missing")
    actual = describe_artifact(root, file)
    if actual["sha256"] != result["sha256"] or actual["bytes"] != result["bytes"]:
        raise JobError("artifact_changed")
    return file
