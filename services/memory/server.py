"""Loopback-only multilingual embeddings for AstrBot memory and meme retrieval."""

import asyncio
import base64
import secrets
from array import array
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType
from pydantic import BaseModel, Field

from services.runtime import path

ROOT = path("memory_state")
MODEL = "ada-e5-small"
KEY = (ROOT / "local-key.txt").read_text(encoding="utf-8").strip()
embedding = None
encode_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global embedding
    TextEmbedding.add_custom_model(
        model=MODEL,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf="intfloat/multilingual-e5-small"),
        dim=384,
        model_file="model.onnx",
        license="MIT",
    )
    embedding = await asyncio.to_thread(
        TextEmbedding,
        model_name=MODEL,
        specific_model_path=str(path("memory_models")),
        local_files_only=True,
        threads=2,
        providers=["CPUExecutionProvider"],
        cuda=False,
    )
    await asyncio.to_thread(lambda: list(embedding.embed(["query: ready"])))
    yield
    embedding = None


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    dimensions: int | None = None
    encoding_format: str = Field(default="float", pattern="^(float|base64)$")


@app.get("/health")
async def health():
    return {"ready": embedding is not None, "model": MODEL, "dimensions": 384}


@app.post("/v1/embeddings")
async def embeddings(body: EmbeddingRequest, authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, "Bearer " + KEY):
        raise HTTPException(401, "Local API key required")
    if body.model != MODEL or body.dimensions not in (None, 384):
        raise HTTPException(400, "Unsupported model or dimensions")
    texts = [body.input] if isinstance(body.input, str) else body.input
    if not 1 <= len(texts) <= 64 or any(not x or len(x) > 8000 for x in texts):
        raise HTTPException(
            400, "Expected 1-64 nonempty texts of at most 8000 characters"
        )
    # E5 recommends the query prefix on both sides of symmetric similarity tasks.
    async with encode_lock:
        vectors = await asyncio.to_thread(
            lambda: list(embedding.embed(["query: " + x for x in texts], batch_size=8))
        )
    data = []
    for index, vector in enumerate(vectors):
        values = vector.tolist()
        encoded = (
            base64.b64encode(array("f", values).tobytes()).decode("ascii")
            if body.encoding_format == "base64"
            else values
        )
        data.append({"object": "embedding", "index": index, "embedding": encoded})
    return {"object": "list", "model": MODEL, "data": data}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=11435, access_log=False)
