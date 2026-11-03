"""Resume admitted work, then make at most one unreconciled channel send attempt."""

import asyncio
import contextlib
import hashlib
from pathlib import Path

from .performance_files import encode
from .performance_store import PerformanceError, PerformanceStore


class PerformanceWorker:
    def __init__(
        self,
        root: Path,
        store: PerformanceStore,
        client,
        delivery,
        config,
        *,
        lease=60,
        encoder=encode,
    ):
        self.root, self.store, self.client, self.delivery = (
            root,
            store,
            client,
            delivery,
        )
        self.config, self.lease, self.encoder = config, lease, encoder
        self.task = None
        self.wake = asyncio.Event()
        self.closed = False
        self.failure = None

    def start(self):
        self.task = asyncio.create_task(self.loop(), name="performance-delivery")

    async def close(self):
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def loop(self):
        while not self.closed:
            try:
                request = self.store.claim(self.lease)
                if request:
                    await self.perform(request)
                    continue
            except Exception as error:
                self.failure = type(error).__name__
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=1)
            self.wake.clear()

    async def perform(self, request):
        lost = False

        def check():
            if self.closed or lost:
                raise PerformanceError("performance_worker_stopping")
            return self.store.check(request["id"], request["token"])

        async def renew():
            nonlocal lost
            try:
                while True:
                    await asyncio.sleep(self.lease / 3)
                    self.store.renew(request["id"], request["token"], self.lease)
            except PerformanceError:
                lost = True

        heartbeat = asyncio.create_task(renew())
        try:
            await self.step(request, check)
        except PerformanceError as error:
            if str(error) == "performance_cancelled":
                self.store.defer(request["id"], request["token"], delay=0)
            elif str(error) in {
                "performance_http_400",
                "performance_http_401",
                "performance_http_403",
                "performance_http_404",
                "performance_http_409",
                "performance_http_413",
                "performance_http_422",
                "performance_authorization_revoked",
                "performance_destination_invalid",
                "performance_encoding_failed",
            }:
                self.store.finish(request["id"], request["token"], "failed", str(error))
            elif str(error) not in {
                "stale_performance_claim",
                "performance_worker_stopping",
            }:
                self.store.defer(
                    request["id"], request["token"], error=str(error), delay=15
                )
        except Exception:
            self.store.defer(
                request["id"],
                request["token"],
                error="performance_processing_failed",
                delay=15,
            )
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def step(self, request, check):
        job = await (
            self.client.status(request)
            if request["job_id"]
            else self.client.submit(request)
        )
        self.store.observe_generation(request["id"], request["token"], job)
        current = self.store.check(request["id"], request["token"], allow_cancel=True)
        if current["cancel_requested"]:
            await self.client.cancel(current)
            self.store.finish(current["id"], current["token"], "cancelled")
            return
        if job["state"] == "cancelled" or job["cancel_requested"]:
            self.store.finish(current["id"], current["token"], "cancelled")
            return
        if job["state"] not in {"completed", "failed"}:
            self.store.defer(current["id"], current["token"])
            return
        self.store.preparing(current["id"], current["token"])
        artifact = await self.artifact(current, job, check)
        send = await self.delivery.prepare(current, artifact)
        check()
        # Recheck the generation authority immediately before committing a send attempt.
        latest = await self.client.status(current)
        if latest["cancel_requested"] or latest["state"] == "cancelled":
            self.store.finish(current["id"], current["token"], "cancelled")
            return
        self.store.begin_send(current["id"], current["token"], artifact)
        try:
            receipt = await asyncio.wait_for(send(), timeout=120)
        except BaseException as error:
            self.store.receipt(
                current["id"],
                current["token"],
                {"kind": "unknown", "reason": type(error).__name__},
            )
            if isinstance(error, asyncio.CancelledError):
                raise
        else:
            self.store.receipt(current["id"], current["token"], receipt)

    async def artifact(self, request, job, check):
        if job["state"] == "failed":
            message = f"《{request['title']}》这次没能生成完整歌声，任务已保留，可以查询原因后换来源或重做。"
            return {
                "format": "text",
                "text": message,
                "sha256": hashlib.sha256(message.encode()).hexdigest(),
                "bytes": len(message.encode()),
            }
        folder = self.root / "deliveries" / request["id"] / request["token"]
        source = await self.client.download(request, folder, check)
        return await self.encoder(source, folder, self.config, check)
