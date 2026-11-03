"""Resident CosyVoice3 worker using Xiaoxia's shared reference and voice API."""

import asyncio
import gc
import hashlib
import io
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

KEY = (path("voice_state") / "service.key").read_text(encoding="utf-8").strip()
profile, reference = voice_profile()
reference_sha256 = hashlib.sha256(reference.read_bytes()).hexdigest()
engine = None
lock = threading.Lock()
last_synthesis = {}
startup_seconds = None
EMOTIONS = {
    "neutral": "",
    "happy": "说话时有一点开心。",
    "angry": "说话时有一点不满。",
    "sad": "说话时有一点难过。",
    "afraid": "说话时有一点担心。",
    "disgusted": "说话时有一点嫌弃。",
    "melancholic": "说话时有一点低落。",
    "surprised": "说话时有一点意外。",
    "calm": "说话时语气平静。",
}


def authenticate(authorization: str | None = Header(default=None)):
    if not authorization or not secrets.compare_digest(authorization, "Bearer " + KEY):
        raise HTTPException(401, "Unauthorized")


def load():
    global engine, startup_seconds
    repo = path("cosy_repo")
    sys.path[:0] = [str(repo), str(repo / "third_party/Matcha-TTS")]
    from cosyvoice.cli.cosyvoice import AutoModel

    torch.set_num_threads(6)
    started = time.perf_counter()
    engine = AutoModel(
        model_dir=str(path("cosy_models")),
        fp16=True,
        load_trt=False,
        load_vllm=False,
    )
    prompt = "You are a helpful assistant."
    transcript = profile["reference_text"]
    engine.add_zero_shot_spk(
        prompt + "<|endofprompt|>" + transcript, str(reference), "neutral"
    )
    base = engine.frontend.spk2info["neutral"]
    # Keep the original audio tokens and embedding for every expression. Cached
    # zero-shot voices ignore the per-call prompt, so cache only new text tokens.
    for emotion, instruction in EMOTIONS.items():
        cached = dict(base)
        prefix = prompt + (" " + instruction if instruction else "")
        cached["prompt_text"], cached["prompt_text_len"] = (
            engine.frontend._extract_text_token(prefix + "<|endofprompt|>" + transcript)
        )
        engine.frontend.spk2info[emotion] = cached
    # All requests reuse the fixed QQ speaker features. These sessions only
    # encode reference audio, so their model weights are no longer needed.
    engine.frontend.speech_tokenizer_session = None
    engine.frontend.campplus_session = None
    gc.collect()
    # The pinned WeText wheel includes its FSTs; load them before serving audio.
    engine.frontend.text_normalize("明天18:30见。", split=False)
    with torch.inference_mode():
        list(
            engine.inference_zero_shot(
                "嗯，我听着呢。", "", "", zero_shot_spk_id="neutral", text_frontend=True
            )
        )
    startup_seconds = round(time.perf_counter() - started, 3)


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(load)
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
        "engine": "CosyVoice3",
        "project": str(PROJECT),
        "voice": profile["id"],
        "reference_sha256": reference_sha256,
        "selection": profile["selection"],
        "startup_seconds": startup_seconds,
        "text_frontend": engine.frontend.text_frontend if engine else None,
        "emotion_control": "mild instruction; strength zero disables it",
        "busy": lock.locked(),
        "last_synthesis": last_synthesis,
    }


@app.post("/speech", dependencies=[Depends(authenticate)])
def speech(body: Speech):
    global last_synthesis
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Text is empty")
    emotion = body.emotion if body.emotion_strength else "neutral"
    with lock, torch.inference_mode():
        started = time.perf_counter()
        if next(engine.model.llm.parameters()).device.type != "cuda":
            for model in (engine.model.llm, engine.model.flow, engine.model.hift):
                model.to("cuda")
        pieces = list(
            engine.inference_zero_shot(
                text, "", "", zero_shot_spk_id=emotion, stream=False, text_frontend=True
            )
        )
        samples = (
            torch.cat([piece["tts_speech"].detach().cpu() for piece in pieces], dim=1)
            .squeeze(0)
            .numpy()
        )
        last_synthesis = {
            "engine": "CosyVoice3",
            "generation_seconds": round(time.perf_counter() - started, 3),
            "audio_seconds": round(len(samples) / engine.sample_rate, 3),
            "emotion": emotion,
        }
        buffer = io.BytesIO()
        sf.write(buffer, samples, engine.sample_rate, format="WAV", subtype="PCM_16")
        headers = {
            "X-Voice-Engine": "CosyVoice3",
            "X-Generation-Seconds": str(last_synthesis["generation_seconds"]),
            "X-Audio-Seconds": str(last_synthesis["audio_seconds"]),
            "X-Voice-Emotion": emotion,
        }
    return Response(buffer.getvalue(), media_type="audio/wav", headers=headers)


@app.post("/release-gpu", dependencies=[Depends(authenticate)])
def release_gpu():
    with lock:
        for model in (engine.model.llm, engine.model.flow, engine.model.hift):
            model.to("cpu")
        torch.cuda.empty_cache()
    return {"status": "released"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=6191)
