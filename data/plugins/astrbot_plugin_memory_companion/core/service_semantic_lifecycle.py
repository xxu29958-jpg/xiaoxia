from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import Mapping
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    SessionContext,
    clean_text,
    stable_fingerprint,
    utc_now,
)
from .source_query import source_visible
from .source_semantic import (
    enqueue_semantic_source,
    find_active_generation,
)
from .source_semantic import (
    generation_status as source_semantic_generation_status,
)
from .source_semantic import (
    list_generations as list_source_semantic_generations,
)
from .source_semantic import (
    pause_generation as pause_source_semantic_generation,
)
from .source_semantic import (
    register_generation as register_source_semantic_generation,
)
from .source_semantic import (
    resume_generation as resume_source_semantic_generation,
)
from .source_semantic import (
    retire_generation as retire_source_semantic_generation,
)


class MemoryCompanionServiceSemanticLifecycle:
    """Semantic lifecycle; state and transaction ownership remain in the facade."""

    def _source_semantic_build_configuration(
        self,
        ctx: SessionContext,
        provider_id: str,
    ) -> dict[str, Any] | None:
        if not self.config.bool("source_semantic.enabled", False):
            return None
        target_id = clean_text(ctx.current_target_id, 160)
        provider_id = clean_text(provider_id, 160)
        model_revision = clean_text(
            self.config.get("source_semantic.model_revision", ""), 160
        )
        dimensions = self.config.int("source_semantic.dimensions", 0)
        if (
            ctx.scope not in {"private", "group"}
            or not ctx.session_id
            or not target_id
            or not ctx.bot_id
            or not ctx.platform
            or not provider_id
            or not model_revision
            or dimensions < 1
            or dimensions > 65536
        ):
            return None
        target_chars = max(
            64, min(2000, self.config.int("source_semantic.fragment_target_chars", 600))
        )
        overlap_chars = max(
            0,
            min(
                target_chars - 1,
                self.config.int("source_semantic.fragment_overlap_chars", 80),
            ),
        )
        window_max_chars = max(
            target_chars,
            min(8000, self.config.int("source_semantic.window_max_chars", 1800)),
        )
        processing_version = clean_text(
            self.config.get(
                "source_semantic.processing_version", "source-redaction-v1"
            ),
            120,
        )
        if not processing_version:
            return None
        build_scope: dict[str, Any] = {
            "kind": "partition",
            "scope": ctx.scope,
            "session_id": ctx.session_id,
            "object_id": target_id,
            "owner_bot_id": ctx.bot_id,
            "platform": ctx.platform,
            "persona_id": ctx.persona_id,
        }
        return {
            "provider_id": provider_id,
            "model_revision": model_revision,
            "dimensions": dimensions,
            "distance_metric": "cosine",
            "query_encoding": "provider-default-v1",
            "document_encoding": "provider-default-v1",
            "processing_version": processing_version,
            "fragment_profile": f"unicode-codepoint-overlap-v1:{target_chars}:{overlap_chars}",
            "fragment_target_chars": target_chars,
            "fragment_overlap_chars": overlap_chars,
            "window_profile": f"adjacent-one-v1:{window_max_chars}",
            "window_max_chars": window_max_chars,
            "history_backfill": self.config.bool(
                "source_semantic.history_backfill_enabled", False
            ),
            "coverage_mode": (
                "session_history"
                if self.config.bool("source_semantic.history_backfill_enabled", False)
                else "incremental_only"
            ),
            "build_scope": build_scope,
        }

    @staticmethod
    def _source_semantic_config_hash(configuration: Mapping[str, Any]) -> str:
        raw = json.dumps(
            configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _source_semantic_authorizer(self, ctx: SessionContext):
        def authorize(row: dict[str, Any], metadata: dict[str, Any]) -> bool:
            return (
                self.config.bool("memory_capture.reliable_sources_enabled", True)
                and self._scope_feature_enabled(ctx, "capture")
                and self._scope_feature_enabled(ctx, "recall")
                and source_visible(ctx, row, metadata)
            )

        return authorize

    def _project_semantic_source_text(self, text: str, row: dict[str, Any]) -> str:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if metadata.get("capture_profile") == "memory.source-capture.v1":
            projected = text
        else:
            projected = self.injection._redact_sensitive_text(text)
        if not projected:
            return ""
        if (
            metadata.get("capture_profile") != "memory.source-capture.v1"
            and self._timeline_content_is_internal_placeholder(projected)
        ):
            return ""
        return projected

    def _source_semantic_transaction_sync(
        self, operation: Any, args: tuple[Any, ...]
    ) -> Any:
        with self.store._lock, self.store._transaction_sync():
            return operation(self.store._conn, *args)

    async def _source_semantic_database(self, operation: Any, *args: Any) -> Any:
        return await self.store._run_recoverable_database_operation(
            self._source_semantic_transaction_sync,
            operation,
            args,
        )

    def _schedule_source_semantic_maintenance(
        self, ctx: SessionContext, source_id: str = ""
    ) -> None:
        # The bridge helper is also usable by small host/test stand-ins that
        # only provide ``store`` and the summary callback. Treat missing
        # lifecycle/config state as a disabled optional subsystem.
        if getattr(self, "_closing", False) or getattr(self, "_closed", False):
            return
        config = getattr(self, "config", None)
        scope_enabled = getattr(self, "_scope_feature_enabled", None)
        if (
            config is None
            or not config.bool("source_semantic.enabled", False)
            or not callable(scope_enabled)
            or not scope_enabled(ctx, "capture")
            or not scope_enabled(ctx, "recall")
        ):
            return
        provider_id = clean_text(
            self.config.get("source_semantic.provider_id", ""), 160
        )
        if self._source_semantic_build_configuration(ctx, provider_id) is None:
            self._source_semantic_status.update(
                state="disabled", last_error="configuration_incomplete"
            )
            return
        context_key = stable_fingerprint(
            ctx.scope,
            ctx.session_id,
            ctx.current_target_id,
            ctx.bot_id,
            ctx.platform,
            ctx.persona_id,
        )
        if source_id:
            self._source_semantic_pending_sources.setdefault(context_key, set()).add(
                source_id
            )
        if context_key in self._source_semantic_inflight:
            return
        snapshot = self._snapshot_context(ctx)
        self._source_semantic_inflight.add(context_key)

        outcome = {"completed_cleanly": False}

        async def run_maintenance() -> bool | None:
            result = await self._run_source_semantic_maintenance(
                snapshot,
                clean_text(source_id, 160),
                context_key,
            )
            outcome["completed_cleanly"] = result is True
            return result

        task = self._spawn_background(
            run_maintenance(),
            label=f"source-semantic:{context_key[:16]}",
            defer_during_grace=True,
        )
        if task is None:
            self._source_semantic_inflight.discard(context_key)
            return

        def on_done(done_task: asyncio.Task[Any]) -> None:
            self._source_semantic_inflight.discard(context_key)
            if done_task.cancelled():
                return
            try:
                task_error = done_task.exception()
            except Exception:
                return
            if (
                task_error is None
                and outcome["completed_cleanly"]
                and self._source_semantic_pending_sources.get(context_key)
            ):
                self._schedule_source_semantic_maintenance(snapshot)

        task.add_done_callback(on_done)

    async def _run_source_semantic_maintenance(
        self,
        ctx: SessionContext,
        source_id: str,
        context_key: str,
    ) -> bool | None:
        generation = ""
        current_task = asyncio.current_task()
        if source_id:
            self._source_semantic_pending_sources.setdefault(context_key, set()).add(
                source_id
            )
        try:
            provider_id = clean_text(
                self.config.get("source_semantic.provider_id", ""), 160
            )
            provider = (
                await self._embedding_provider_by_id(provider_id)
                if provider_id
                else None
            )
            configuration = self._source_semantic_build_configuration(ctx, provider_id)
            if provider is None or configuration is None:
                self._source_semantic_status.update(
                    state="degraded", last_error="provider_or_configuration_unavailable"
                )
                return
            config_hash = self._source_semantic_config_hash(configuration)

            def get_or_create(conn):
                existing = find_active_generation(conn, config_hash)
                if existing is not None:
                    return existing, False
                generation_id = "ssg_" + secrets.token_hex(16)
                register_source_semantic_generation(
                    conn,
                    generation_id,
                    configuration,
                    created_at=utc_now(),
                )
                created = find_active_generation(conn, config_hash)
                return created, True

            generation_row, _created = await self._source_semantic_database(
                get_or_create
            )
            if not generation_row:
                self._source_semantic_status.update(
                    state="degraded", last_error="generation_unavailable"
                )
                return
            generation = str(generation_row["generation"])
            self._source_semantic_tasks[generation] = current_task
            self._source_semantic_context_keys[generation] = context_key
            if generation_row["state"] == "paused":
                self._source_semantic_status.update(
                    state="paused", last_generation=generation
                )
                return
            while True:
                pending_sources = tuple(
                    sorted(
                        self._source_semantic_pending_sources.get(context_key, set())
                    )
                )
                if not pending_sources:
                    break
                await self._source_semantic_database(
                    lambda conn, ids=pending_sources: [
                        enqueue_semantic_source(
                            conn,
                            generation,
                            pending_id,
                            authorize_source=self._source_semantic_authorizer(ctx),
                        )
                        for pending_id in ids
                    ]
                )
                remaining = self._source_semantic_pending_sources.get(context_key)
                if remaining is not None:
                    remaining.difference_update(pending_sources)
                    if not remaining:
                        self._source_semantic_pending_sources.pop(context_key, None)
            result = await self._run_source_semantic_batch(
                ctx,
                provider,
                provider_id,
                generation,
                configuration,
            )
            return not bool(result.get("last_error")) or bool(
                result.get("retryable_conflict")
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._source_semantic_status.update(
                state="degraded",
                last_error=self._describe_exception(exc),
                last_generation=generation,
            )
            logger.warning(
                "[MemoryCompanion] 原文语义维护失败: generation=%s error=%s",
                generation,
                self._describe_exception(exc),
                exc_info=True,
            )
        finally:
            if (
                generation
                and self._source_semantic_tasks.get(generation) is current_task
            ):
                self._source_semantic_tasks.pop(generation, None)
                self._source_semantic_context_keys.pop(generation, None)
            if not self._source_semantic_pending_sources.get(context_key):
                self._source_semantic_pending_sources.pop(context_key, None)

    async def source_semantic_status(self, generation: str = "") -> dict[str, Any]:
        """Return maintenance state and bounded counts without source text or config secrets."""
        if generation:
            stored = await self._source_semantic_database(
                source_semantic_generation_status, generation
            )
        else:
            rows = await self._source_semantic_database(
                lambda conn: list_source_semantic_generations(conn)
            )
            if not rows:
                return dict(self._source_semantic_status)
            stored = await self._source_semantic_database(
                source_semantic_generation_status,
                str(rows[-1]["generation"]),
            )
        return {
            **{
                key: stored[key]
                for key in (
                    "generation",
                    "state",
                    "base_sequence",
                    "checkpoint_sequence",
                    "source_revision",
                    "scan_complete",
                    "dirty_count",
                    "order_change_count",
                    "document_count",
                    "ready_documents",
                    "pending_documents",
                    "stale_documents",
                )
            },
            "worker": dict(self._source_semantic_status),
        }

    async def pause_source_semantic(self, generation: str) -> bool:
        changed = await self._source_semantic_database(
            lambda conn: pause_source_semantic_generation(
                conn, generation, updated_at=utc_now()
            )
        )
        task = self._source_semantic_tasks.get(generation)
        context_key = self._source_semantic_context_keys.get(generation, "")
        if (
            changed
            and task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if context_key:
            self._source_semantic_inflight.discard(context_key)
        self._source_semantic_status.update(state="paused", last_generation=generation)
        return bool(changed)

    async def resume_source_semantic(self, generation: str) -> bool:
        rows = await self._source_semantic_database(
            lambda conn: [
                row
                for row in list_source_semantic_generations(conn)
                if row["generation"] == generation
            ]
        )
        if not rows or not self.config.bool("source_semantic.enabled", False):
            return False
        stored_config = json.loads(rows[0]["config_json"])
        scope = stored_config.get("build_scope", {})
        ctx = SessionContext(
            session_id=str(scope.get("session_id", "")),
            scope=str(scope.get("scope", "")),
            platform=str(scope.get("platform", "")),
            bot_id=str(scope.get("owner_bot_id", "")),
            persona_id=str(scope.get("persona_id", "")),
            user_id=str(scope.get("object_id", ""))
            if scope.get("scope") == "private"
            else str(scope.get("participant_user_id", "")),
            group_id=str(scope.get("object_id", ""))
            if scope.get("scope") == "group"
            else "",
        )
        current = self._source_semantic_build_configuration(
            ctx,
            clean_text(self.config.get("source_semantic.provider_id", ""), 160),
        )
        if (
            not current
            or self._source_semantic_config_hash(current) != rows[0]["config_hash"]
        ):
            return False
        state = rows[0]["state"]
        if state == "paused":
            resumed = await self._source_semantic_database(
                lambda conn: resume_source_semantic_generation(
                    conn, generation, updated_at=utc_now()
                )
            )
        else:
            resumed = state in {"building", "ready"}
        if not resumed:
            return False
        self._schedule_source_semantic_maintenance(ctx)
        return True

    async def retire_source_semantic(self, generation: str) -> bool:
        changed = await self._source_semantic_database(
            lambda conn: retire_source_semantic_generation(
                conn, generation, updated_at=utc_now()
            )
        )
        task = self._source_semantic_tasks.get(generation)
        context_key = self._source_semantic_context_keys.get(generation, "")
        if (
            changed
            and task is not None
            and task is not asyncio.current_task()
            and not task.done()
        ):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if context_key:
            self._source_semantic_inflight.discard(context_key)
        return bool(changed)
