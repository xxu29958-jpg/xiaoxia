"""Own immutable input and retained MP3 delivery artifacts, separately from chat temp files."""

import asyncio
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

from .performance_client import MAX_BYTES
from .performance_store import PerformanceError


def snapshot(root: Path, parameters: dict) -> tuple[dict, dict]:
    payload, identity = dict(parameters), dict(parameters)
    if not parameters["source_path"]:
        return payload, identity
    source = Path(parameters["source_path"])
    if not source.is_file() or source.is_symlink():
        raise PerformanceError("performance_source_missing")
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=inputs, delete=False) as output:
        pending = Path(output.name)
        try:
            digest = copy_input(source, output)
        except BaseException:
            output.close()
            pending.unlink(missing_ok=True)
            raise
    suffix = source.suffix.lower()
    if len(suffix) > 16 or not suffix[1:].isalnum():
        suffix = ".audio"
    destination = inputs / (digest + suffix)
    pending.replace(destination)
    payload["source_path"] = str(destination)
    identity["source_path"] = "sha256:" + digest
    return payload, identity


def copy_input(source: Path, output) -> str:
    digest, length = hashlib.sha256(), 0
    with source.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            length += len(chunk)
            if length > MAX_BYTES:
                raise PerformanceError("performance_source_too_large")
            digest.update(chunk)
            output.write(chunk)
    output.flush()
    os.fsync(output.fileno())
    if length == 0:
        raise PerformanceError("performance_source_empty")
    return digest.hexdigest()


async def encode(source: Path, folder: Path, config: dict, check) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    pending, target = folder / "delivery.pending.mp3", folder / "delivery.mp3"
    check()
    process = await asyncio.create_subprocess_exec(
        config.get("ffmpeg_path", "ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(pending),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        await asyncio.wait_for(process.communicate(), timeout=120)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    check()
    if process.returncode or not pending.is_file() or pending.stat().st_size == 0:
        raise PerformanceError("performance_encoding_failed")
    with pending.open("rb+") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
        os.fsync(file.fileno())
    pending.replace(target)
    return {
        "path": str(target),
        "sha256": digest,
        "bytes": target.stat().st_size,
        "format": "mp3",
    }


def verify(artifact: dict) -> Path:
    file = Path(artifact["path"])
    if not file.is_file() or file.stat().st_size != artifact["bytes"]:
        raise PerformanceError("delivery_artifact_missing")
    with file.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != artifact["sha256"]:
        raise PerformanceError("delivery_artifact_changed")
    return file
