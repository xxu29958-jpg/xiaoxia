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

from services.runtime import CONFIG, PROJECT, path
from services.voice.cover_pipeline import CoverPipeline
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


@app.get("/health")
def health():
    return {
        "status": "ready" if startup_seconds is not None else "loading",
        "engine": "SoulX-Singer + Seed-VC",
        "voice": "xiaxia",
        "project": str(PROJECT),
        "reference_sha256": reference_sha256,
        "startup_seconds": startup_seconds,
        "busy": lock.locked(),
        "last_synthesis": last_synthesis,
        "capabilities": ["original_score", "full_performance_conversion"],
    }


@app.post("/speech", dependencies=[Depends(authenticate)])
def sing(body: Lyrics):
    global last_synthesis
    started = time.perf_counter()
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
def cover(body: Cover):
    """Return a complete song or opera performance in the shared voice."""
    global last_synthesis
    output = ROOT / "songs" / uuid.uuid4().hex
    source = Path(body.source_path)
    download_seconds = 0.0
    if body.source_url:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError

        output.mkdir(parents=True)
        download_started = time.perf_counter()
        try:
            with YoutubeDL(
                {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "outtmpl": str(output / "source.%(ext)s"),
                    "ffmpeg_location": CONFIG["ffmpeg"],
                    "proxy": CONFIG["download_proxy"] or None,
                    "quiet": True,
                    "socket_timeout": 20,
                    "retries": 1,
                    "fragment_retries": 1,
                }
            ) as downloader:
                info = downloader.extract_info(body.source_url, download=True)
                source = Path(downloader.prepare_filename(info))
                (output / "source-page.json").write_text(
                    json.dumps(
                        {
                            "url": body.source_url,
                            "title": info.get("title"),
                            "uploader": info.get("uploader"),
                            "duration": info.get("duration"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
        except DownloadError as exc:
            raise HTTPException(422, "source_unavailable") from exc
        download_seconds = time.perf_counter() - download_started
    if not source.is_file():
        raise HTTPException(404, "Source audio was not found")
    with lock:
        try:
            report = cover_engine.convert(
                source,
                output,
                acapella=body.acapella,
                pitch=body.pitch,
                start_seconds=body.start_seconds,
                duration_seconds=body.duration_seconds,
            )
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
        },
    )


if __name__ == "__main__":
    import uvicorn

    (ROOT / "temp").mkdir(exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=6192)
