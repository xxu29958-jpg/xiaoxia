"""Short task-control requests and streaming result reads to the local voice gateway."""

import hashlib
import os
from pathlib import Path

import aiohttp

from .performance_store import PerformanceError

MAX_BYTES = 2 * 1024**3


class PerformanceClient:
    def __init__(self, config):
        self.config = config
        self.base = config.get("voice_base_url", "http://127.0.0.1:6190").rstrip("/")

    def headers(self, owner, key=None):
        secret = (
            Path(self.config.get("voice_key_file", ".runtime/voice/service.key"))
            .read_text(encoding="utf-8")
            .strip()
        )
        result = {"Authorization": "Bearer " + secret, "X-Owner-Id": owner}
        if key:
            result["Idempotency-Key"] = key
        return result

    async def call(self, method, path, owner, *, body=None, key=None):
        try:
            async with aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=10)
            ) as client:
                async with client.request(
                    method,
                    self.base + path,
                    headers=self.headers(owner, key),
                    json=body,
                ) as response:
                    if response.status not in (200, 202):
                        raise PerformanceError(f"performance_http_{response.status}")
                    return await response.json()
        except (aiohttp.ClientError, TimeoutError):
            raise PerformanceError("performance_gateway_unavailable") from None

    async def submit(self, request):
        return await self.call(
            "POST",
            "/jobs/covers",
            request["owner"],
            body=request["parameters"],
            key=request["id"],
        )

    async def status(self, request):
        return await self.call("GET", f"/jobs/{request['job_id']}", request["owner"])

    async def cancel(self, request):
        return await self.call(
            "POST", f"/jobs/{request['job_id']}/cancel", request["owner"]
        )

    async def download(self, request, folder: Path, check):
        folder.mkdir(parents=True, exist_ok=True)
        pending, target = folder / "result.download", folder / "result.wav"
        try:
            async with aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=300)
            ) as client:
                async with client.get(
                    self.base + f"/jobs/{request['job_id']}/result",
                    headers=self.headers(request["owner"]),
                ) as response:
                    if response.status != 200:
                        raise PerformanceError(f"performance_http_{response.status}")
                    await self.receive(response, pending, check)
        except (aiohttp.ClientError, TimeoutError):
            raise PerformanceError("performance_gateway_unavailable") from None
        check()
        pending.replace(target)
        return target

    @staticmethod
    async def receive(response, pending: Path, check):
        digest, length = hashlib.sha256(), 0
        with pending.open("wb") as file:
            async for chunk in response.content.iter_chunked(256 * 1024):
                check()
                length += len(chunk)
                if length > MAX_BYTES:
                    raise PerformanceError("performance_result_too_large")
                digest.update(chunk)
                file.write(chunk)
            file.flush()
            os.fsync(file.fileno())
        if digest.hexdigest() != response.headers.get("X-Artifact-SHA256"):
            raise PerformanceError("performance_result_changed")
