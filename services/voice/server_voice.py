"""Shared voice entrypoint: speech, original singing and CPU recognition."""

import asyncio
import json
import os
import re
import secrets
import tempfile
import threading
import time
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Annotated, Literal

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["MODELSCOPE_OFFLINE"] = "1"

import requests
import torch
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from funasr import AutoModel
from pydantic import BaseModel, Field

from services.jobs.runner import JobRunner
from services.jobs.store import JobStore
from services.runtime import CONFIG, PROJECT, path, voice_profile
from services.voice.job_api import JobService
from services.voice.job_api import router as job_router
from services.voice.job_execution import runtime_signature
from services.voice.job_renderer import RemoteCoverRenderer

ROOT = path("voice_state")
KEY = (ROOT / "service.key").read_text(encoding="utf-8").strip()
profile, reference = voice_profile()
recognizer = None
lock = threading.Lock()
recognition_lock = threading.Lock()
speech_active = threading.Event()
last_synthesis = {}
last_cover = {}
last_recognition = {}
client = requests.Session()
client.trust_env = False
client.headers["Authorization"] = "Bearer " + KEY


def authenticate(authorization: str | None = Header(default=None)):
    if not authorization or not secrets.compare_digest(authorization, "Bearer " + KEY):
        raise HTTPException(401, "Unauthorized")


@asynccontextmanager
async def lifespan(app):
    global recognizer
    torch.set_num_threads(6)
    recognizer = await asyncio.to_thread(
        AutoModel,
        model=str(path("recognition_models")),
        device="cpu",
        disable_update=True,
        disable_pbar=True,
        ncpu=6,
    )
    print(
        json.dumps({"event": "voice_gateway_ready", "recognizer": "SenseVoiceSmall"}),
        flush=True,
    )
    jobs_root = ROOT / "jobs"
    store = JobStore(jobs_root / "state.sqlite3")
    signature = runtime_signature(PROJECT)
    renderer = RemoteCoverRenderer(
        jobs_root,
        lock,
        key=KEY,
        engine=CONFIG["speech_engine"],
        reference=profile["reference_sha256"],
        signature=signature,
    )
    runner = JobRunner(store, renderer)
    app.state.media_jobs = JobService(
        store, runner, jobs_root, profile["reference_sha256"], signature
    )
    runner.start()
    try:
        yield
    finally:
        app.state.media_jobs = None
        await asyncio.to_thread(runner.close)
        if not runner.thread.is_alive():
            renderer.client.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(job_router(authenticate))


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=650)
    mode: Literal["speech", "singing"] = "speech"
    emotion: Literal[
        "neutral",
        "happy",
        "angry",
        "sad",
        "afraid",
        "disgusted",
        "melancholic",
        "surprised",
        "calm",
    ] = "neutral"
    emotion_strength: float = Field(default=0.08, ge=0, le=1)


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
        "status": "ready" if recognizer else "loading",
        "engine": "Xiaoxia Voice Gateway",
        "voice": profile["id"],
        "project": str(PROJECT),
        "reference_sha256": profile["reference_sha256"],
        "busy": lock.locked(),
        "speech_active": speech_active.is_set(),
        "capabilities": ["speech", "transcription", "singing", "cover"],
        "last_synthesis": last_synthesis,
        "last_cover": last_cover,
        "last_recognition": last_recognition,
    }


@app.post("/speech", dependencies=[Depends(authenticate)])
def speech(body: Speech):
    global last_synthesis
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Text is empty")
    with lock:
        started = time.perf_counter()
        if body.mode == "speech" and CONFIG["speech_engine"] != "fish":
            speech_active.set()
        if body.mode == "singing" and CONFIG["speech_engine"] == "fish":
            client.post(
                "http://127.0.0.1:6191/release-gpu", timeout=60
            ).raise_for_status()
        payload: dict[str, str | float] = {"text": text}
        if body.mode == "speech":
            payload.update(emotion=body.emotion, emotion_strength=body.emotion_strength)
        try:
            generated = client.post(
                f"http://127.0.0.1:{6191 if body.mode == 'speech' else 6192}/speech",
                json=payload,
                timeout=120,
            )
        finally:
            if body.mode == "speech":
                speech_active.clear()
        if generated.status_code != 200:
            raise HTTPException(generated.status_code, generated.text)
        last_synthesis = {
            "engine": generated.headers["X-Voice-Engine"],
            "voice": profile["id"],
            "mode": body.mode,
            "generation_seconds": round(time.perf_counter() - started, 3),
            "audio_seconds": float(generated.headers["X-Audio-Seconds"]),
            "emotion": generated.headers.get("X-Voice-Emotion", "neutral"),
        }
        return Response(
            generated.content,
            media_type="audio/wav",
            headers={
                "X-Voice-Engine": last_synthesis["engine"],
                "X-Generation-Seconds": str(last_synthesis["generation_seconds"]),
                "X-Audio-Seconds": str(last_synthesis["audio_seconds"]),
                "X-Voice-Mode": body.mode,
                "X-Voice-Emotion": last_synthesis["emotion"],
            },
        )


@app.post("/cover", dependencies=[Depends(authenticate)])
def cover(body: Cover):
    """Forward a complete performance without occupying the speech request lock."""
    global last_cover
    # The 4B speech model shares one 16 GB GPU with performance conversion.
    # Finish one complete request before lending its memory to the singer.
    with lock if CONFIG["speech_engine"] == "fish" else nullcontext():
        if CONFIG["speech_engine"] == "fish":
            client.post(
                "http://127.0.0.1:6191/release-gpu", timeout=60
            ).raise_for_status()
        generated = client.post(
            "http://127.0.0.1:6192/cover", json=body.model_dump(), timeout=3600
        )
    if generated.status_code != 200:
        return Response(
            generated.content,
            status_code=generated.status_code,
            media_type="application/json",
        )
    last_cover = {
        "engine": generated.headers["X-Voice-Engine"],
        "generation_seconds": float(generated.headers["X-Generation-Seconds"]),
        "audio_seconds": float(generated.headers["X-Audio-Seconds"]),
    }
    return Response(
        generated.content,
        media_type="audio/wav",
        headers={
            name: generated.headers[name]
            for name in (
                "X-Voice-Engine",
                "X-Generation-Seconds",
                "X-Audio-Seconds",
                "X-Result-Path",
            )
        },
    )


@app.post("/v1/audio/transcriptions", dependencies=[Depends(authenticate)])
async def transcriptions(
    file: Annotated[UploadFile, File()],
    model: Annotated[str, Form()] = "ada-sensevoice",
):
    global last_recognition
    active_recognizer = recognizer
    if active_recognizer is None:
        raise HTTPException(503, "recognizer_not_ready")
    audio = await file.read()
    with tempfile.NamedTemporaryFile(
        suffix=Path(file.filename or "audio.wav").suffix,
        delete=False,
        dir=ROOT / "temp",
    ) as temp:
        temp.write(audio)
        path = Path(temp.name)
    try:

        def recognize():
            started = time.perf_counter()
            with recognition_lock, torch.inference_mode():
                result = active_recognizer.generate(
                    input=str(path), language="auto", use_itn=True, disable_pbar=True
                )
            text = re.sub(
                r"<\|.*?\|>", "", "".join(item.get("text", "") for item in result)
            ).strip()
            return text, round(time.perf_counter() - started, 3)

        text, elapsed = await asyncio.to_thread(recognize)
        last_recognition = {
            "engine": "SenseVoiceSmall",
            "seconds": elapsed,
            "characters": len(text),
        }
        return {"text": text}
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn

    (ROOT / "temp").mkdir(exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=6190)
