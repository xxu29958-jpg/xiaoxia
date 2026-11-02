from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    clean_text,
    utc_now,
)
from .service_support import (
    LOCAL_TZ,
)


class MemoryCompanionServiceMaintenanceDispatch:
    """service maintenance dispatch; the facade owns all shared resources."""

    def _ensure_acl_projection_reconcile_loop(self) -> None:
        """Start one retained reconciler for the in-process projections.

        The synchronous ACL read must stay free of database I/O, so the
        projection is refreshed in a worker thread: it compares the stored
        revision and rebuilds — plus broadcasts cache invalidation — only when
        data actually changed.
        """
        if self._closing or self._closed:
            return
        if self._acl_reconcile_task is not None and not self._acl_reconcile_task.done():
            return
        task = self._spawn_background(
            self._acl_projection_reconcile_loop(),
            label="acl-projection-reconcile",
        )
        self._acl_reconcile_task = task
        if task is not None:
            self._acl_reconcile_status["state"] = "scheduled"

    async def _acl_projection_reconcile_loop(self) -> None:
        interval_seconds = float(self._ACL_RECONCILE_INTERVAL_SECONDS)
        await asyncio.sleep(max(5.0, interval_seconds / 4))
        while not self._closing and not self._closed:
            try:
                self._acl_reconcile_status["state"] = "running"
                result = await asyncio.to_thread(self.store.reconcile_caches_sync)
                self._acl_reconcile_status.update(
                    {
                        "state": "scheduled",
                        "last_error": "",
                        "last_completed_at": utc_now(),
                        "last_revision": str(result.get("revision", "")),
                        "last_changed": bool(result.get("changed")),
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._acl_reconcile_status.update(
                    {
                        "state": "degraded",
                        "last_error": self._describe_exception(exc),
                        "last_completed_at": utc_now(),
                    }
                )
                logger.warning(
                    "[MemoryCompanion] ACL 投影对账失败: %s", exc, exc_info=True
                )
            await asyncio.sleep(interval_seconds)

    def _ensure_wal_checkpoint_loop(self) -> None:
        """Start one retained, low-frequency WAL truncation task.

        The dedicated candidate-load read connection can keep the passive
        auto-checkpoint from ever truncating the ``-wal`` file, so it grows to
        its high-water mark (a 146MB WAL was observed). This task periodically
        runs a best-effort ``wal_checkpoint(TRUNCATE)`` when the WAL exceeds a
        threshold, bounding its size without touching the request path.
        """
        if self._closing or self._closed:
            return
        if not self.config.bool("maintenance.wal_checkpoint_enabled", True):
            return
        if (
            self._wal_checkpoint_task is not None
            and not self._wal_checkpoint_task.done()
        ):
            return
        task = self._spawn_background(
            self._wal_checkpoint_loop(),
            label="wal-checkpoint",
        )
        self._wal_checkpoint_task = task
        if task is not None:
            self._wal_checkpoint_status["state"] = "scheduled"

    async def _wal_checkpoint_loop(self) -> None:
        interval_seconds = max(
            60,
            self.config.int("maintenance.wal_checkpoint_interval_seconds", 1800),
        )
        min_wal_bytes = max(
            1024 * 1024,
            self.config.int("maintenance.wal_checkpoint_min_bytes", 8 * 1024 * 1024),
        )
        await asyncio.sleep(max(30, interval_seconds // 6))
        while (
            not self._closing
            and not self._closed
            and self.config.bool("maintenance.wal_checkpoint_enabled", True)
        ):
            try:
                self._wal_checkpoint_status["state"] = "running"
                result = await self.store.wal_checkpoint_truncate(
                    min_wal_bytes=min_wal_bytes
                )
                self._wal_checkpoint_status.update(
                    {
                        "state": "scheduled",
                        "last_error": "",
                        "last_completed_at": utc_now(),
                        "last_result": result,
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._wal_checkpoint_status.update(
                    {
                        "state": "degraded",
                        "last_error": self._describe_exception(exc),
                        "last_completed_at": utc_now(),
                    }
                )
                logger.warning(
                    "[MemoryCompanion] WAL checkpoint 任务失败: %s", exc, exc_info=True
                )
            await asyncio.sleep(interval_seconds)

    def _ensure_lifecycle_maintenance_dispatcher(self) -> None:
        """Start one retained, low-frequency natural-forgetting scheduler."""
        if (
            self._closing
            or self._closed
            or not self.config.bool("maintenance.auto_schedule_enabled", True)
        ):
            return
        if (
            self._maintenance_dispatch_task is not None
            and not self._maintenance_dispatch_task.done()
        ):
            return
        task = self._spawn_background(
            self._lifecycle_maintenance_dispatch_loop(),
            label="lifecycle-maintenance",
        )
        self._maintenance_dispatch_task = task
        if task is not None:
            self._maintenance_dispatch_status["state"] = "scheduled"

    async def _lifecycle_maintenance_dispatch_loop(self) -> None:
        startup_delay = max(
            5,
            self.config.int("maintenance.auto_startup_delay_seconds", 120),
        )
        interval_hours = max(
            1,
            self.config.int("maintenance.auto_interval_hours", 24),
        )
        await asyncio.sleep(startup_delay)
        while (
            not self._closing
            and not self._closed
            and self.config.bool("maintenance.auto_schedule_enabled", True)
        ):
            try:
                self._maintenance_dispatch_status["state"] = "running"
                result = await self.sleep_maintenance(
                    reason="scheduled_natural_forgetting"
                )
                self._maintenance_dispatch_status.update(
                    {
                        "state": "scheduled",
                        "last_error": "",
                        "last_completed_at": clean_text(result.get("ran_at"), 80),
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._maintenance_dispatch_status.update(
                    {
                        "state": "degraded",
                        "last_error": self._describe_exception(exc),
                        "last_completed_at": utc_now(),
                    }
                )
                logger.warning(
                    "[MemoryCompanion] 自动自然遗忘维护失败: %s", exc, exc_info=True
                )
            await asyncio.sleep(interval_hours * 3600)

    async def read_unified_profile_portrait(
        self, request: dict[str, Any], *, limit: int = 8
    ) -> dict[str, Any]:
        """Read a pre-authorized low-sensitivity summary through the Bridge."""
        return await self.portraits.read_summary(request, limit=limit)

    async def unified_profile_portrait_status(self, person_id: str) -> dict[str, Any]:
        """Expose synchronization state without returning portrait facts."""
        return await self.portraits.status(person_id)

    async def run_unified_profile_portrait_batch(
        self, person_id: str, *, run_day: str = ""
    ) -> dict[str, Any]:
        return await self.portraits.run_daily_batch(person_id, run_day=run_day)

    def _portrait_window_hours(self) -> tuple[int, int]:
        start = max(0, min(23, self.config.int("portrait.update_window_start_hour", 3)))
        end = max(1, min(24, self.config.int("portrait.update_window_end_hour", 5)))
        if end <= start:
            start, end = 3, 5
        return start, end

    def _portrait_seconds_until_window(self, now: datetime | None = None) -> float:
        local_now = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
        start, end = self._portrait_window_hours()
        if start <= local_now.hour < end:
            return 0.0
        candidate = local_now.replace(hour=start, minute=0, second=0, microsecond=0)
        if local_now.hour >= end:
            candidate += timedelta(days=1)
        return max(1.0, (candidate - local_now).total_seconds())

    def _ensure_portrait_daily_dispatcher(self) -> None:
        """Start one retained scheduler after the first eligible observation."""
        if (
            self._closing
            or self._closed
            or not self.config.bool("portrait.daily_dispatch_enabled", True)
        ):
            return
        if (
            self._portrait_dispatch_task is not None
            and not self._portrait_dispatch_task.done()
        ):
            return
        task = self._spawn_background(
            self._portrait_daily_dispatch_loop(), label="portrait-daily-dispatch"
        )
        self._portrait_dispatch_task = task
        if task is not None:
            self._portrait_dispatch_status["state"] = "scheduled"

    async def _portrait_daily_dispatch_loop(self) -> None:
        """Run only in the configured daily window, never on a message path."""
        while (
            not self._closing
            and not self._closed
            and self.config.bool("portrait.daily_dispatch_enabled", True)
        ):
            try:
                delay = self._portrait_seconds_until_window()
                if delay > 0:
                    await asyncio.sleep(delay)
                if self._closing or self._closed:
                    break
                await self.run_due_portrait_batches(force_window=True)
                # One bounded retry window supports the configured second
                # attempt without turning evidence capture into LLM work.
                await asyncio.sleep(15 * 60)
                if self._portrait_seconds_until_window() == 0:
                    await self.run_due_portrait_batches(force_window=True)
                # Do not repeatedly re-enter the same window after the two
                # bounded attempts.  The next pass begins on the next local
                # calendar day.
                next_start = (datetime.now(LOCAL_TZ) + timedelta(days=1)).replace(
                    hour=self._portrait_window_hours()[0],
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                await asyncio.sleep(
                    max(60.0, (next_start - datetime.now(LOCAL_TZ)).total_seconds())
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._portrait_dispatch_status.update(
                    {
                        "state": "degraded",
                        "last_result": clean_text(exc, 160) or "bridge_degraded",
                        "last_completed_at": utc_now(),
                    }
                )
                await asyncio.sleep(15 * 60)

    async def run_due_portrait_batches(
        self, *, force_window: bool = False
    ) -> dict[str, Any]:
        now = datetime.now(LOCAL_TZ)
        if not force_window and self._portrait_seconds_until_window(now) > 0:
            return {
                "ok": True,
                "code": "portrait_window_closed",
                "processed": 0,
                "results": [],
            }
        run_day = now.date().isoformat()
        people = await self.store.list_pending_portrait_people(limit=500)
        results: list[dict[str, Any]] = []
        for person_id in people:
            result = await self.portraits.run_daily_batch(person_id, run_day=run_day)
            results.append(
                {
                    "person_id": person_id,
                    "code": clean_text(result.get("code"), 80),
                    "ok": bool(result.get("ok")),
                }
            )
        code = (
            "profile_exact"
            if any(item["ok"] for item in results)
            else "portrait_insufficient_evidence"
        )
        self._portrait_dispatch_status.update(
            {
                "state": "ready",
                "last_run_day": run_day,
                "last_result": code,
                "last_completed_at": utc_now(),
            }
        )
        return {"ok": True, "code": code, "processed": len(results), "results": results}
