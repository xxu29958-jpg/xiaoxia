from __future__ import annotations

import asyncio

from astrbot.api import logger


class AutoCollectManagerWorker:
    """Worker; shared runtime resources belong to the facade."""

    async def _worker(self) -> None:
        """串行处理队列中的图片，以限制视觉模型负载。"""
        while True:
            job = await self.queue.get()
            try:
                await self._process_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.counters["failed"] = self.counters.get("failed", 0) + 1
                logger.error(
                    "[meme_manager] 自动收集处理图片失败：%s",
                    exc,
                    exc_info=True,
                )
            finally:
                self._inflight.discard(f"{job.target_pack_id}:{job.digest}")
                job.snapshot_path.unlink(missing_ok=True)
                self.queue.task_done()
