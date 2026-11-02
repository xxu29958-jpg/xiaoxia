from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .models import (
    SessionContext,
    utc_now,
)
from .source_evidence import (
    message_source_version,
)
from .source_semantic import (
    acknowledge_dirty as acknowledge_semantic_dirty,
)
from .source_semantic import (
    build_semantic_window,
    capture_semantic_fence,
    expand_semantic_order_changes,
    list_semantic_dirty,
    pack_semantic_vector,
    prune_semantic_documents,
    read_semantic_neighbors,
    read_semantic_source,
    scan_generation_sources,
    semantic_document_id,
    semantic_document_is_ready,
    split_source_fragments,
    store_semantic_document,
    validate_semantic_snapshot,
)
from .source_semantic import (
    generation_status as source_semantic_generation_status,
)
from .source_semantic import (
    mark_generation_ready as mark_source_semantic_generation_ready,
)


class MemoryCompanionServiceSemanticIndexing:
    """Semantic indexing; state and transaction ownership remain in the facade."""

    async def _run_source_semantic_batch(
        self,
        ctx: SessionContext,
        provider: Any,
        provider_id: str,
        generation: str,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        authorize = self._source_semantic_authorizer(ctx)
        if self._source_semantic_build_configuration(ctx, provider_id) != configuration:
            self._source_semantic_status.update(
                state="degraded",
                last_error="configuration_changed",
                last_generation=generation,
            )
            return {
                "provider_calls": 0,
                "completed_sources": 0,
                "coverage_mode": configuration.get("coverage_mode", ""),
            }
        source_limit = max(
            1, min(32, self.config.int("source_semantic.sources_per_run", 4))
        )
        call_limit = max(
            1, min(32, self.config.int("source_semantic.provider_calls_per_run", 8))
        )
        self._source_semantic_status.update(
            state="running",
            last_error="",
            last_generation=generation,
        )
        await self._source_semantic_database(
            lambda conn: expand_semantic_order_changes(
                conn, generation, limit=max(16, source_limit * 4)
            )
        )
        before = await self._source_semantic_database(
            source_semantic_generation_status, generation
        )
        scan = {"done": before["scan_complete"], "scanned": 0}
        if (
            configuration.get("history_backfill") is True
            and not before["scan_complete"]
        ):
            scan = await self._source_semantic_database(
                lambda conn: scan_generation_sources(
                    conn,
                    generation,
                    limit=source_limit,
                    authorize_source=authorize,
                    updated_at=utc_now(),
                )
            )
            scan = {"done": bool(scan["done"]), "scanned": len(scan["sources"])}
        dirty = await self._source_semantic_database(
            lambda conn: list_semantic_dirty(conn, generation, limit=source_limit)
        )
        provider_calls = 0
        completed_sources = 0
        last_error = ""
        retryable_conflict = False
        for item in dirty:
            source_id = str(item["source_id"])
            sequence = int(item["change_sequence"])
            if (
                item["operation"] == "delete"
                or not item["new_present"]
                or not item["new_build_scope_matches"]
            ):
                completed = await self._finish_source_semantic_source(
                    generation,
                    source_id,
                    sequence,
                    (),
                    expected_revision=None,
                )
                if completed:
                    completed_sources += 1
                continue
            try:
                result = await self._index_source_semantically(
                    ctx,
                    provider,
                    provider_id,
                    generation,
                    configuration,
                    source_id,
                    sequence,
                    call_limit - provider_calls,
                )
                provider_calls += int(result.get("provider_calls", 0))
                if result.get("complete"):
                    completed_sources += 1
                    continue
                last_error = str(result.get("error") or "batch_limit_reached")
                retryable_conflict = last_error.endswith(
                    "semantic_source_revision_changed"
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = self._describe_exception(exc)
                retryable_conflict = last_error.endswith(
                    "semantic_source_revision_changed"
                )
                break
        try:
            await self._source_semantic_database(
                lambda conn: mark_source_semantic_generation_ready(
                    conn,
                    generation,
                    updated_at=utc_now(),
                )
            )
        except ValueError as exc:
            if str(exc) not in {
                "semantic_generation_not_building",
                "semantic_generation_not_found",
            }:
                last_error = str(exc)
        after = await self._source_semantic_database(
            source_semantic_generation_status, generation
        )
        self._source_semantic_status.update(
            state=after["state"],
            last_error=last_error,
            last_run_at=utc_now(),
            last_generation=generation,
            last_result={
                "scan_sources": scan.get("scanned", 0),
                "scan_complete": after["scan_complete"],
                "dirty_remaining": after["dirty_count"],
                "order_changes_remaining": after["order_change_count"],
                "ready_documents": after["ready_documents"],
                "provider_calls": provider_calls,
                "completed_sources": completed_sources,
                "coverage_mode": configuration.get("coverage_mode", ""),
                "last_error": last_error,
                "retryable_conflict": retryable_conflict,
            },
        )
        return dict(self._source_semantic_status["last_result"])

    async def _index_source_semantically(
        self,
        ctx: SessionContext,
        provider: Any,
        provider_id: str,
        generation: str,
        configuration: dict[str, Any],
        source_id: str,
        change_sequence: int,
        remaining_calls: int,
    ) -> dict[str, Any]:
        authorize = self._source_semantic_authorizer(ctx)
        project_text = self._project_semantic_source_text

        def read_bundle(conn):
            fence = capture_semantic_fence(conn, generation)
            raw = conn.execute(
                "SELECT * FROM timeline WHERE id=?", (source_id,)
            ).fetchone()
            if raw is None:
                return fence, None, []
            raw_dict = dict(raw)
            version = message_source_version(raw_dict)
            anchor = read_semantic_source(
                conn,
                generation,
                source_id,
                version,
                authorize_source=authorize,
                project_text=project_text,
            )
            neighbors = read_semantic_neighbors(
                conn,
                generation,
                anchor,
                authorize_source=authorize,
                project_text=project_text,
            )
            return fence, anchor, neighbors

        try:
            fence, anchor, neighbors = await self._source_semantic_database(read_bundle)
        except ValueError as exc:
            if str(exc) in {
                "semantic_source_unauthorized",
                "semantic_source_unavailable",
                "semantic_source_text_unavailable",
            }:
                completed = await self._finish_source_semantic_source(
                    generation,
                    source_id,
                    change_sequence,
                    (),
                    expected_revision=None,
                )
                return {"complete": completed, "provider_calls": 0}
            raise
        if anchor is None or not anchor["text"]:
            completed = await self._finish_source_semantic_source(
                generation,
                source_id,
                change_sequence,
                (),
                expected_revision=int(fence["source_revision"]),
            )
            return {"complete": completed, "provider_calls": 0}

        neighbors = [item for item in neighbors if item.get("text")]
        previous = None
        following = None
        if neighbors:
            anchor_position = self._source_semantic_position(anchor)
            for item in neighbors:
                if self._source_semantic_position(item) < anchor_position:
                    previous = item
                elif self._source_semantic_position(item) > anchor_position:
                    following = item
        fragments = split_source_fragments(
            anchor["text"],
            target_chars=int(configuration["fragment_target_chars"]),
            overlap_chars=int(configuration["fragment_overlap_chars"]),
        )
        keep_ids: list[str] = []
        provider_calls = 0
        for fragment in fragments:
            fragment_spec = {
                "view_kind": "fragment",
                "anchor_source_id": anchor["source_id"],
                "anchor_source_version": anchor["source_version"],
                "char_start": fragment["char_start"],
                "char_end": fragment["char_end"],
                "input_text": fragment["text"],
                "dependencies": [
                    {
                        "source_id": anchor["source_id"],
                        "source_version": anchor["source_version"],
                        "char_start": fragment["char_start"],
                        "char_end": fragment["char_end"],
                        "role": "anchor",
                    }
                ],
            }
            specs = [fragment_spec]
            if previous is not None or following is not None:
                specs.append(
                    {
                        "view_kind": "window",
                        **build_semantic_window(
                            anchor,
                            char_start=fragment["char_start"],
                            char_end=fragment["char_end"],
                            previous=previous,
                            following=following,
                            max_chars=int(configuration["window_max_chars"]),
                        ),
                    }
                )
            for spec in specs:
                document_id = semantic_document_id(
                    generation,
                    spec["anchor_source_id"],
                    spec["view_kind"],
                    spec["char_start"],
                    spec["char_end"],
                )

                def candidate_ready(conn, current=spec):
                    validate_semantic_snapshot(
                        conn,
                        generation=generation,
                        expected_config_hash=fence["config_hash"],
                        source_change_sequence=int(fence["source_revision"]),
                        view_kind=current["view_kind"],
                        anchor_source_id=current["anchor_source_id"],
                        anchor_source_version=current["anchor_source_version"],
                        char_start=current["char_start"],
                        char_end=current["char_end"],
                        input_text=current["input_text"],
                        dependencies=current["dependencies"],
                        authorize_source=authorize,
                        project_text=project_text,
                    )
                    return semantic_document_is_ready(
                        conn,
                        generation=generation,
                        view_kind=current["view_kind"],
                        anchor_source_id=current["anchor_source_id"],
                        char_start=current["char_start"],
                        char_end=current["char_end"],
                        input_text=current["input_text"],
                        dependencies=current["dependencies"],
                    )

                if await self._source_semantic_database(candidate_ready):
                    keep_ids.append(document_id)
                    continue
                if provider_calls >= max(0, remaining_calls):
                    return {
                        "complete": False,
                        "provider_calls": provider_calls,
                        "error": "provider_call_limit_reached",
                    }
                if (
                    self._source_semantic_build_configuration(ctx, provider_id)
                    != configuration
                ):
                    return {
                        "complete": False,
                        "provider_calls": provider_calls,
                        "error": "configuration_changed",
                    }
                await self._source_semantic_database(
                    lambda conn, current=spec: store_semantic_document(
                        conn,
                        generation=generation,
                        view_kind=current["view_kind"],
                        anchor_source_id=current["anchor_source_id"],
                        anchor_source_version=current["anchor_source_version"],
                        char_start=current["char_start"],
                        char_end=current["char_end"],
                        input_text=current["input_text"],
                        source_change_sequence=int(fence["source_revision"]),
                        dependencies=current["dependencies"],
                        expected_config_hash=fence["config_hash"],
                        authorize_source=authorize,
                        project_text=project_text,
                        updated_at=utc_now(),
                    )
                )
                provider_calls += 1
                await self._acquire_source_semantic_background_slot()
                try:
                    vector = await self._embed_text_with_provider(
                        provider,
                        spec["input_text"],
                        provider_id=provider_id,
                        usage_task="source_semantic_embedding",
                        strict_input=True,
                        input_limit=int(configuration["window_max_chars"]),
                    )
                finally:
                    self._embedding_background_semaphore.release()
                if (
                    self._source_semantic_build_configuration(ctx, provider_id)
                    != configuration
                ):
                    return {
                        "complete": False,
                        "provider_calls": provider_calls,
                        "error": "configuration_changed",
                    }
                vector = self._normalize_embedding_vector(vector)
                if len(vector) != int(configuration["dimensions"]):
                    raise ValueError("semantic_vector_dimension_mismatch")
                packed = pack_semantic_vector(vector)
                await self._source_semantic_database(
                    lambda conn, current=spec, blob=packed: store_semantic_document(
                        conn,
                        generation=generation,
                        view_kind=current["view_kind"],
                        anchor_source_id=current["anchor_source_id"],
                        anchor_source_version=current["anchor_source_version"],
                        char_start=current["char_start"],
                        char_end=current["char_end"],
                        input_text=current["input_text"],
                        source_change_sequence=int(fence["source_revision"]),
                        dependencies=current["dependencies"],
                        expected_config_hash=fence["config_hash"],
                        authorize_source=authorize,
                        project_text=project_text,
                        vector=blob,
                        vector_dimension=len(vector),
                        updated_at=utc_now(),
                    )
                )
                keep_ids.append(document_id)

        completed = await self._finish_source_semantic_source(
            generation,
            source_id,
            change_sequence,
            keep_ids,
            expected_revision=int(fence["source_revision"]),
        )
        return {"complete": completed, "provider_calls": provider_calls}

    async def _acquire_source_semantic_background_slot(self) -> None:
        """Acquire a shared embedding slot while yielding to waiting queries."""
        while True:
            while self._source_semantic_query_waiters:  # noqa: ASYNC110 - bounded priority handoff; cancellation remains interruptible
                await asyncio.sleep(0.01)
            await self._embedding_background_semaphore.acquire()
            if not self._source_semantic_query_waiters:
                return
            self._embedding_background_semaphore.release()

    @staticmethod
    def _source_semantic_position(source: Mapping[str, Any]) -> tuple[float, str, str]:
        try:
            instant = float(source.get("source_sort_time"))
        except (TypeError, ValueError):
            parsed = datetime.fromisoformat(
                str(source.get("occurred_at", "")).replace("Z", "+00:00")
            )
            instant = parsed.timestamp()
        return (
            instant,
            str(source.get("created_at") or ""),
            str(source.get("source_id") or ""),
        )

    async def _finish_source_semantic_source(
        self,
        generation: str,
        source_id: str,
        change_sequence: int,
        keep_document_ids: tuple[str, ...] | list[str],
        *,
        expected_revision: int | None,
    ) -> bool:
        def finish(conn):
            row = conn.execute(
                "SELECT state FROM source_semantic_generations WHERE generation=?",
                (generation,),
            ).fetchone()
            if row is None or row["state"] not in {"building", "ready"}:
                return False
            if expected_revision is not None:
                revision = conn.execute(
                    "SELECT revision FROM source_semantic_revision WHERE singleton=1"
                ).fetchone()
                if revision is None or int(revision[0]) != expected_revision:
                    return False
            if not acknowledge_semantic_dirty(
                conn, generation, source_id, change_sequence
            ):
                return False
            prune_semantic_documents(conn, generation, source_id, keep_document_ids)
            return True

        return bool(await self._source_semantic_database(finish))
