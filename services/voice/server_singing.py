"""Resident score singing and voice conversion for the shared local voice API."""

import asyncio
import hashlib
import io
import json
import secrets
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from services.jobs.store import JobError
from services.runtime import CONFIG, PROJECT, path
from services.voice.cover_pipeline import CoverPipeline
from services.voice.cover_source import prepare_source
from services.voice.job_execution import JobExecution, runtime_signature
from services.voice.resident_pipeline import SingingPipeline
from services.voice.singing_score import write_score

ROOT = path("voice_state")

KEY = (ROOT / "service.key").read_text(encoding="utf-8").strip()
engine = None
cover_engine = None
lock = threading.Lock()
last_synthesis = {}
startup_seconds = None
reference_sha256 = None
RUNTIME_SIGNATURE = runtime_signature(PROJECT)


def authenticate(authorization: str | None = Header(default=None)):
    if not authorization or not secrets.compare_digest(authorization, "Bearer " + KEY):
        raise HTTPException(401, "Unauthorized")


@asynccontextmanager
async def lifespan(app):
    global engine, cover_engine, startup_seconds, reference_sha256
    started = time.perf_counter()
    engine = await asyncio.to_thread(SingingPipeline)
    reference_sha256 = hashlib.sha256(
        Path(engine.seed_args["target"]).read_bytes()
    ).hexdigest()
    # Exercise both generation stages before the first chat request.
    with tempfile.TemporaryDirectory(prefix="song-warm-", dir=ROOT / "temp") as temp:
        folder = Path(temp)
        score = write_score("晚风轻轻吹过窗，你把今天放一旁。", folder / "score")
        await asyncio.to_thread(engine.generate, score, folder / "audio")
    cover_engine = CoverPipeline(engine.seed, engine.seed_args)
    startup_seconds = round(time.perf_counter() - started, 3)
    print(
        json.dumps({"event": "singing_ready", "seconds": startup_seconds}), flush=True
    )
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


class Lyrics(BaseModel):
    text: str = Field(min_length=1, max_length=650)


class Cover(BaseModel):
    source_path: str = ""
    source_url: str = ""
    acapella: bool = False
    pitch: int = Field(default=0, ge=-12, le=12)
    start_seconds: float = Field(default=0, ge=0)
    duration_seconds: float | None = Field(default=None, gt=0)


class SpeechConversion(BaseModel):
    source_path: str


@app.post("/convert-speech", dependencies=[Depends(authenticate)])
def convert_speech(body: SpeechConversion):
    """Reuse the resident converter without retaining each chat in the song library."""
    if cover_engine is None:
        raise HTTPException(503, "singing_not_ready")
    with (
        lock,
        tempfile.TemporaryDirectory(prefix="speech-vc-", dir=ROOT / "temp") as temp,
    ):
        report = cover_engine.convert(Path(body.source_path), Path(temp), acapella=True)
        return Response(Path(report["output"]).read_bytes(), media_type="audio/wav")


@app.get("/health")
def health():
    return {
        "status": "ready" if startup_seconds is not None else "loading",
        "engine": "SoulX-Singer + Seed-VC",
        "voice": "xiaxia",
        "project": str(PROJECT),
        "reference_sha256": reference_sha256,
        "startup_seconds": startup_seconds,
        "job_protocol": "cover-v1",
        "runtime_signature": RUNTIME_SIGNATURE,
        "busy": lock.locked(),
        "last_synthesis": last_synthesis,
        "capabilities": ["original_score", "full_performance_conversion"],
    }


@app.post("/speech", dependencies=[Depends(authenticate)])
def sing(body: Lyrics):
    global last_synthesis
    started = time.perf_counter()
    if engine is None:
        raise HTTPException(503, "singing_not_ready")
    with lock, tempfile.TemporaryDirectory(prefix="song-", dir=ROOT / "temp") as temp:
        folder = Path(temp)
        try:
            score = write_score(body.text, folder / "score")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        report = engine.generate(score, folder / "audio")
        audio_path = report["samples"]["custom"]["output"]
        samples, rate = sf.read(audio_path, dtype="float32")
        # Match the existing conversational playback volume without altering pitch.
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        if rms:
            samples *= min(10 ** (-24 / 20) / rms, 0.95 / float(np.abs(samples).max()))
        buffer = io.BytesIO()
        sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
        last_synthesis = {
            "engine": "SoulX-Singer + Seed-VC",
            "mode": "singing",
            "generation_seconds": round(time.perf_counter() - started, 3),
            "audio_seconds": round(len(samples) / rate, 3),
            "svs_seconds": report["svs_seconds"],
            "conversion_seconds": report["conversion_seconds"],
        }
    return Response(
        buffer.getvalue(),
        media_type="audio/wav",
        headers={
            "X-Voice-Engine": last_synthesis["engine"],
            "X-Generation-Seconds": str(last_synthesis["generation_seconds"]),
            "X-Audio-Seconds": str(last_synthesis["audio_seconds"]),
        },
    )


@app.post("/cover", dependencies=[Depends(authenticate)])
def cover(
    body: Cover,
    x_job_id: str | None = Header(default=None),
    x_job_token: str | None = Header(default=None),
):
    """Return a complete performance while honoring a durable claim when supplied."""
    global last_synthesis
    if cover_engine is None:
        raise HTTPException(503, "singing_not_ready")
    try:
        control = JobExecution(ROOT / "jobs", x_job_id, x_job_token)
        control.validate(body.model_dump(), reference_sha256 or "", RUNTIME_SIGNATURE)
        output = (
            ROOT / "jobs" / "backend" / x_job_id / x_job_token
            if x_job_id and x_job_token
            else ROOT / "songs" / uuid.uuid4().hex
        )
        source, download_seconds = prepare_source(
            body.model_dump(), output, CONFIG, control
        )
    except JobError as error:
        raise HTTPException(409 if x_job_id else 422, str(error)) from error
    with lock:
        try:
            report = cover_engine.convert(
                source,
                output,
                acapella=body.acapella,
                pitch=body.pitch,
                start_seconds=body.start_seconds,
                duration_seconds=body.duration_seconds,
                checkpoint=control.checkpoint,
            )
            control.checkpoint("finalizing")
        except JobError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        report["source_url"] = body.source_url
        report["download_seconds"] = round(download_seconds, 3)
        report["generation_seconds"] = round(
            report["generation_seconds"] + download_seconds, 3
        )
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        last_synthesis = {
            key: value for key, value in report.items() if key != "windows"
        }
    return FileResponse(
        report["output"],
        media_type="audio/wav",
        headers={
            "X-Voice-Engine": report["engine"],
            "X-Generation-Seconds": str(report["generation_seconds"]),
            "X-Audio-Seconds": str(report["audio_seconds"]),
            "X-Result-Path": report["output"],
            "X-Job-Control": "cover-v1",
        },
    )


if __name__ == "__main__":
    import uvicorn

    (ROOT / "temp").mkdir(exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=6192)
