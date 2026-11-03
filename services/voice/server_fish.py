"""Fish S2 delivery followed by the resident QQ-reference voice converter."""

import asyncio
import hashlib
import io
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TQDM_DISABLE"] = "1"
import numpy as np
import requests
import soundfile as sf
import torch
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from services.runtime import PROJECT, path, voice_profile

ROOT = path("voice_state")
KEY = (ROOT / "service.key").read_text(encoding="utf-8").strip()
profile, reference = voice_profile()
reference_sha256 = hashlib.sha256(reference.read_bytes()).hexdigest()
model = codec = reference_codes = None
startup_seconds = None
last_synthesis = {}
lock = threading.Lock()
client = requests.Session()
client.trust_env = False
client.headers["Authorization"] = "Bearer " + KEY
ENGINE = "Fish S2 + Seed-VC"
EMOTIONS = {
    "neutral": "",
    "happy": "[laughing tone]",
    "angry": "[slightly annoyed]",
    "sad": "[sad]",
    "afraid": "[worried]",
    "disgusted": "[disapproving tone]",
    "melancholic": "[low voice]",
    "surprised": "[surprised]",
    "calm": "[gentle tone]",
}


def authenticate(authorization: str | None = Header(default=None)):
    if not authorization or not secrets.compare_digest(authorization, "Bearer " + KEY):
        raise HTTPException(401, "Unauthorized")


def load():
    global model, codec, reference_codes, startup_seconds
    sys.path.insert(0, str(path("fish_repo")))
    from fish_speech.models.dac.modded_dac import WindowLimitedTransformer
    from fish_speech.models.text2semantic.inference import (
        encode_audio,
        load_codec_model,
    )
    from fish_speech.models.text2semantic.llama import (
        DualARTransformer,
        precompute_freqs_cis,
    )

    torch.set_num_threads(6)
    started = time.perf_counter()
    with torch.device("meta"):
        model = DualARTransformer.from_pretrained(
            path("fish_models"), load_weights=True, max_length=4096
        )
    cfg = model.config
    model.freqs_cis = precompute_freqs_cis(cfg.max_seq_len, cfg.head_dim, cfg.rope_base)
    model.fast_freqs_cis = precompute_freqs_cis(
        cfg.num_codebooks, cfg.fast_head_dim, cfg.rope_base
    )
    model.causal_mask = torch.ones(
        cfg.max_seq_len, cfg.max_seq_len, dtype=torch.bool
    ).tril_()
    model = model.to(device="cuda", dtype=torch.bfloat16).eval()
    model._cache_setup_done = False
    codec = load_codec_model(path("fish_models") / "codec.pth", "cpu", torch.bfloat16)
    # These subclasses always supply their own per-window mask to forward().
    # Their inherited 32768-square masks are unused (four GiB in this checkpoint).
    for module in codec.modules():
        if isinstance(module, WindowLimitedTransformer):
            module.causal_mask = torch.empty(0, dtype=torch.bool)
    with torch.inference_mode():
        codec.to("cuda")
    reference_codes = encode_audio(reference, codec, "cuda").cpu()
    torch.cuda.synchronize()
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


def speech_chunks(text):
    """Keep sentence boundaries and bound each codec decode for longer replies."""
    pending = ""
    for sentence in re.findall(r"[^。！？!?]+[。！？!?]?|[。！？!?]", text):
        if pending and len(pending) + len(sentence) > 160:
            yield pending
            pending = ""
        while len(sentence) > 160:
            yield sentence[:160]
            sentence = sentence[160:]
        pending += sentence
    if pending:
        yield pending


@app.get("/health")
def health():
    return {
        "status": "ready" if startup_seconds is not None else "loading",
        "engine": ENGINE,
        "project": str(PROJECT),
        "voice": profile["id"],
        "reference_sha256": reference_sha256,
        "startup_seconds": startup_seconds,
        "emotion_control": "Fish native voice instructions; strength zero disables them",
        "busy": lock.locked(),
        "last_synthesis": last_synthesis,
    }


@app.post("/speech", dependencies=[Depends(authenticate)])
def speech(body: Speech):
    from fish_speech.models.text2semantic.inference import (
        decode_one_token_ar,
        decode_to_audio,
        generate_long,
    )

    global last_synthesis
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Text is empty")
    emotion = body.emotion if body.emotion_strength else "neutral"
    with (
        lock,
        torch.inference_mode(),
        tempfile.TemporaryDirectory(prefix="fish-speech-", dir=ROOT / "temp") as temp,
    ):
        started = time.perf_counter()
        model.to("cuda")
        codec.to("cuda")
        torch.manual_seed(42)
        pieces = []
        for chunk in speech_chunks(text):
            responses = generate_long(
                model=model,
                device="cuda",
                decode_one_token=decode_one_token_ar,
                text="<|speaker:0|>" + EMOTIONS[emotion] + chunk,
                max_new_tokens=2048,
                prompt_text=[profile["reference_text"]],
                prompt_tokens=[reference_codes],
                top_p=0.7,
                top_k=30,
                temperature=0.7,
            )
            for response in responses:
                if response.action == "sample":
                    pieces.append(
                        decode_to_audio(response.codes, codec).float().cpu().numpy()
                    )
        samples = np.concatenate(pieces)
        synthesis_seconds = time.perf_counter() - started
        source = Path(temp) / "delivery.wav"
        sf.write(source, samples, codec.sample_rate, subtype="FLOAT")
        converted_at = time.perf_counter()
        converted = client.post(
            "http://127.0.0.1:6192/convert-speech",
            json={"source_path": str(source)},
            timeout=90,
        )
        converted.raise_for_status()
        samples, rate = sf.read(io.BytesIO(converted.content), dtype="float32")
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        if rms:
            samples *= min(10 ** (-24 / 20) / rms, 0.95 / float(np.abs(samples).max()))
        buffer = io.BytesIO()
        sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
        last_synthesis = {
            "engine": ENGINE,
            "generation_seconds": round(time.perf_counter() - started, 3),
            "synthesis_seconds": round(synthesis_seconds, 3),
            "conversion_seconds": round(time.perf_counter() - converted_at, 3),
            "audio_seconds": round(len(samples) / rate, 3),
            "emotion": emotion,
            "source_float_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    return Response(
        buffer.getvalue(),
        media_type="audio/wav",
        headers={
            "X-Voice-Engine": ENGINE,
            "X-Generation-Seconds": str(last_synthesis["generation_seconds"]),
            "X-Audio-Seconds": str(last_synthesis["audio_seconds"]),
            "X-Voice-Emotion": emotion,
        },
    )


@app.post("/release-gpu", dependencies=[Depends(authenticate)])
def release_gpu():
    with lock, torch.inference_mode():
        model.to("cpu")
        codec.to("cpu")
        torch.cuda.empty_cache()
    return {"status": "released"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=6191)
