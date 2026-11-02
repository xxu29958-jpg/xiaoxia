"""Resident IndexTTS speech worker for the shared Ada voice API."""

import asyncio
import hashlib
import io
import json
import os
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["MODELSCOPE_OFFLINE"] = "1"
import soundfile as sf
import torch
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from services.runtime import PROJECT, path, voice_profile

ROOT = path("voice_state")
REPO = path("index_repo")
KEY = (ROOT / "service.key").read_text(encoding="utf-8").strip()
profile, reference = voice_profile()
reference_sha256 = hashlib.sha256(reference.read_bytes()).hexdigest()
engine = None
lock = threading.Lock()
last_synthesis = {}
startup_seconds = None


def authenticate(authorization: str | None = Header(default=None)):
    if not authorization or not secrets.compare_digest(authorization, "Bearer " + KEY):
        raise HTTPException(401, "Unauthorized")


@asynccontextmanager
async def lifespan(app):
    global engine, startup_seconds
    sys.path.insert(0, str(REPO))
    from indextts.infer_v2 import IndexTTS2

    torch.set_num_threads(6)
    started = time.perf_counter()
    aux = path("index_aux")
    engine = await asyncio.to_thread(
        IndexTTS2,
        cfg_path=str(path("index_models") / "config.yaml"),
        model_dir=str(path("index_models")),
        use_fp16=True,
        use_cuda_kernel=False,
        use_deepspeed=False,
        use_qwen_emo=False,
        aux_paths={
            "w2v_bert": str(aux / "w2v-bert-2.0"),
            "campplus": str(aux / "campplus_cn_common.bin"),
            "bigvgan": str(aux / "bigvgan"),
            "semantic_codec": str(aux / "semantic_codec.safetensors"),
        },
    )
    # Warm the reference features and CUDA kernels before accepting chat requests.
    await asyncio.to_thread(
        engine.infer,
        spk_audio_prompt=str(reference),
        text="嗯，我听着呢。",
        output_path=None,
        num_beams=1,
        verbose=False,
    )
    # Both prompt features are cached. This encoder is only used when the
    # reference changes, which this fixed-profile worker does through restart.
    engine.semantic_model.to("cpu")
    torch.cuda.empty_cache()
    startup_seconds = round(time.perf_counter() - started, 3)
    print(json.dumps({"event": "index_ready", "seconds": startup_seconds}), flush=True)
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=650)
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


@app.get("/health")
def health():
    return {
        "status": "ready" if startup_seconds is not None else "loading",
        "engine": "IndexTTS-2",
        "project": str(PROJECT),
        "voice": profile["id"],
        "reference_sha256": reference_sha256,
        "selection": profile["selection"],
        "startup_seconds": startup_seconds,
        "busy": lock.locked(),
        "last_synthesis": last_synthesis,
    }


@app.post("/speech", dependencies=[Depends(authenticate)])
def speech(body: Speech):
    global last_synthesis
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Text is empty")
    with lock:
        started = time.perf_counter()
        if next(engine.gpt.parameters()).device.type != "cuda":
            for model in (
                engine.gpt,
                engine.s2mel,
                engine.bigvgan,
                engine.semantic_codec,
            ):
                model.to(engine.device)
        emotion_options = {}
        if body.emotion != "neutral" and body.emotion_strength:
            emotions = (
                "happy",
                "angry",
                "sad",
                "afraid",
                "disgusted",
                "melancholic",
                "surprised",
                "calm",
            )
            emotion_options = {
                "emo_vector": [float(name == body.emotion) for name in emotions],
                "emo_alpha": body.emotion_strength,
                "use_random": False,
            }
        rate, samples = engine.infer(
            spk_audio_prompt=str(reference),
            text=text,
            output_path=None,
            num_beams=1,
            verbose=False,
            **emotion_options,
        )
        last_synthesis = {
            "engine": "IndexTTS-2",
            "generation_seconds": round(time.perf_counter() - started, 3),
            "audio_seconds": round(len(samples) / rate, 3),
            "emotion": body.emotion if emotion_options else "neutral",
            "emotion_strength": body.emotion_strength if emotion_options else 0,
        }
        buffer = io.BytesIO()
        # Index returns signed 16-bit PCM, not a normalized float waveform.
        sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
        headers = {
            "X-Voice-Engine": "IndexTTS-2",
            "X-Generation-Seconds": str(last_synthesis["generation_seconds"]),
            "X-Audio-Seconds": str(last_synthesis["audio_seconds"]),
            "X-Voice-Emotion": last_synthesis["emotion"],
        }
    return Response(buffer.getvalue(), media_type="audio/wav", headers=headers)


@app.post("/release-gpu", dependencies=[Depends(authenticate)])
def release_gpu():
    # Keep model weights in RAM and reference features cached while Step sings.
    # The next speech request restores these same models without reloading files.
    with lock:
        for model in (engine.gpt, engine.s2mel, engine.bigvgan, engine.semantic_codec):
            model.to("cpu")
        torch.cuda.empty_cache()
    return {"status": "released"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=6191)
