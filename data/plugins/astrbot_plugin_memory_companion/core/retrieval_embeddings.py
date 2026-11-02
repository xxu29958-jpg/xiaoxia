from __future__ import annotations

import asyncio
import inspect
import math
import time
from datetime import datetime, timezone
from typing import Any

from .memory_lifecycle import evaluate_memory_lifecycle
from .models import (
    MemoryRecord,
    SessionContext,
    clean_text,
    memory_embedding_text,
    memory_embedding_text_hash,
)
from .profile_quality import PROFILE_MEMORY_TYPES
from .time_intent import TimeIntent


class RetrievalEngineEmbeddings:
    """Embeddings; shared runtime resources belong to the facade."""

    async def _embedding_candidate_memories(
        self,
        query: str,
        ctx: SessionContext,
        *,
        include_pending: bool,
        time_intent: TimeIntent | None = None,
    ) -> tuple[list[MemoryRecord], dict[str, float], dict[str, Any]]:
        info: dict[str, Any] = {
            "embedding_enabled": bool(self.embedding_enabled),
            "embedding_provider_id": self.embedding_provider_id,
            "embedding_reason": "disabled",
            "embedding_candidates": 0,
            "embedding_hits": 0,
        }
        if not self.embedding_enabled:
            return [], {}, info
        if self.embedding_provider is None or not self._is_embedding_provider(
            self.embedding_provider
        ):
            info["embedding_reason"] = "no_embedding_provider"
            return [], {}, info
        if not self.embedding_provider_id:
            info["embedding_reason"] = "no_embedding_provider_id"
            return [], {}, info
        query = clean_text(query, 2000)
        if not query:
            info["embedding_reason"] = "empty_query"
            return [], {}, info

        try:
            query_vector = await self._call_embedding_provider(query)
            query_vector = self._normalize_vector(query_vector)
        except Exception as error:
            info["embedding_reason"] = (
                f"embedding_query_error:{clean_text(self._describe_exception(error), 120)}"
            )
            return [], {}, info
        if not query_vector:
            info["embedding_reason"] = "empty_query_vector"
            return [], {}, info

        from .vector_search import VectorSnapshotChanged

        block_profile = self._query_blocks_profile_retrieval(query)
        authorization_key = (
            ctx.scope,
            ctx.session_id,
            ctx.platform,
            ctx.user_id,
            ctx.group_id,
            ctx.bot_id,
            ctx.persona_id,
            ctx.strict_session_only,
            self.policy.allow_self_timeline_everywhere,
            self.policy.allow_group_public_in_private,
            self.policy.hide_pending_review,
            self.policy.include_raw_events,
            self.policy.enable_acl_rules,
            self.policy.admin_read_all,
            self.private_topology_enabled,
            self.group_topology_enabled,
            block_profile,
            (time_intent.start_at, time_intent.end_at)
            if time_intent and time_intent.active
            else None,
        )
        # A data/ACL race retries once with current authorization and the same
        # query vector. No second provider request, no importance-based fallback.
        for attempt in range(2):
            try:
                revision = await self.store.memory_revision()
                acl_state = (
                    await self._acl_state()
                    if self.policy.enable_acl_rules
                    else self._empty_acl_state()
                )
                selection_time = datetime.now(timezone.utc)

                def eligible(memory: MemoryRecord) -> bool:
                    visible, blocked = self._search_visibility_reason(
                        memory, ctx, acl_state
                    )
                    return bool(
                        visible
                        and not blocked
                        and evaluate_memory_lifecycle(
                            memory, ctx, now=selection_time
                        ).eligible
                        and not (
                            block_profile and memory.memory_type in PROFILE_MEMORY_TYPES
                        )
                        and (
                            not time_intent
                            or not time_intent.active
                            or self._memory_overlaps_time_window(memory, time_intent)
                        )
                    )

                (
                    memories,
                    scores,
                    diagnostics,
                ) = await self.store.search_memory_embeddings(
                    provider_id=self.embedding_provider_id,
                    query=query_vector,
                    expected_revision=revision,
                    authorization_key=authorization_key,
                    eligible=eligible,
                    include_pending=include_pending,
                    top_k=self.embedding_top_k,
                    threshold=self.embedding_score_threshold,
                    batch_size=self.embedding_candidate_limit,
                    max_text_chars=self.embedding_max_text_chars,
                    selection_time=selection_time.timestamp(),
                )
                info.update(diagnostics)
                return memories, scores, info
            except VectorSnapshotChanged:
                if attempt == 0:
                    continue
                info["embedding_reason"] = "embedding_snapshot_changed"
            except Exception as error:
                info["embedding_reason"] = (
                    f"embedding_store_error:{clean_text(error, 120)}"
                )
                break
        return [], {}, info

    @staticmethod
    def _is_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    def _cached_query_vector(self, text: str) -> list[float]:
        """A repeated question should not pay for a second embedding call."""
        if self.query_vector_cache_ttl_seconds <= 0 or not text:
            return []
        entry = self._query_vector_cache.get(text)
        if entry is None:
            return []
        stored_at, vector = entry
        if time.monotonic() - stored_at > self.query_vector_cache_ttl_seconds:
            self._query_vector_cache.pop(text, None)
            return []
        return list(vector)

    def _store_query_vector(self, text: str, vector: list[float]) -> None:
        if self.query_vector_cache_ttl_seconds <= 0 or not text or not vector:
            return
        self._query_vector_cache[text] = (time.monotonic(), list(vector))
        if len(self._query_vector_cache) > 64:
            oldest = min(self._query_vector_cache.items(), key=lambda item: item[1][0])[
                0
            ]
            self._query_vector_cache.pop(oldest, None)

    async def _call_embedding_provider(self, text: str) -> list[float]:
        provider = self.embedding_provider
        text = clean_text(text, 2000)
        if provider is None:
            return []
        cached = self._cached_query_vector(text)
        if cached:
            return cached

        async def maybe_wait(value: Any) -> Any:
            if inspect.isawaitable(value):
                if self.embedding_timeout_ms > 0:
                    return await asyncio.wait_for(
                        value, timeout=self.embedding_timeout_ms / 1000.0
                    )
                return await value
            return value

        get_embedding = getattr(provider, "get_embedding", None)
        get_embeddings = getattr(provider, "get_embeddings", None)
        get_embeddings_batch = getattr(provider, "get_embeddings_batch", None)
        started = datetime.now(timezone.utc)
        payload: Any = None
        success = False
        error = ""
        called_provider = False
        try:
            if callable(get_embedding):
                called_provider = True
                payload = await maybe_wait(get_embedding(text))
                success = True
                vector = self._coerce_vector(payload)
                self._store_query_vector(text, vector)
                return vector

            if callable(get_embeddings):
                called_provider = True
                payload = await maybe_wait(get_embeddings([text]))
                success = True
                vector = self._first_vector(payload)
                self._store_query_vector(text, vector)
                return vector

            if callable(get_embeddings_batch):
                called_provider = True
                try:
                    payload = await maybe_wait(
                        get_embeddings_batch(
                            [text], batch_size=1, tasks_limit=1, max_retries=1
                        )
                    )
                except TypeError:
                    payload = await maybe_wait(get_embeddings_batch([text]))
                success = True
                return self._first_vector(payload)
            return []
        except Exception as exc:
            error = self._describe_exception(exc)
            raise
        finally:
            if called_provider:
                elapsed_ms = int(
                    (datetime.now(timezone.utc) - started).total_seconds() * 1000
                )
                self._record_usage(
                    task="memory_embedding_query",
                    provider_id=self.embedding_provider_id or "<auto>",
                    prompt=text,
                    completion="",
                    resp=payload,
                    success=success,
                    elapsed_ms=elapsed_ms,
                    error=error,
                )

    @staticmethod
    def _describe_exception(error: BaseException) -> str:
        message = str(error).strip()
        name = type(error).__name__
        return f"{name}: {message}" if message else name

    @staticmethod
    def _usage_prompt_for_rerank(query: str, documents: list[str]) -> str:
        doc_lines = [
            f"{index + 1}. {clean_text(document, 700)}"
            for index, document in enumerate(documents[:80])
            if clean_text(document, 700)
        ]
        return clean_text(
            f"query: {clean_text(query, 1000)}\n\ndocuments:\n" + "\n".join(doc_lines),
            12000,
        )

    def _record_usage(
        self,
        *,
        task: str,
        provider_id: str,
        prompt: str,
        completion: str,
        resp: Any,
        success: bool,
        elapsed_ms: int,
        error: str = "",
    ) -> None:
        recorder = self.usage_recorder
        if not callable(recorder):
            return
        try:
            recorder(
                task=task,
                provider_id=provider_id,
                prompt=prompt,
                completion=completion,
                resp=resp,
                success=success,
                elapsed_ms=elapsed_ms,
                error=error,
            )
        except Exception:
            pass

    @staticmethod
    def _coerce_vector(value: Any) -> list[float]:
        if value is None:
            return []
        if isinstance(value, dict):
            for key in ("embedding", "vector"):
                if key in value:
                    vector = RetrievalEngineEmbeddings._coerce_vector(value.get(key))
                    if vector:
                        return vector
            for key in ("data", "embeddings", "vectors"):
                if key in value:
                    vector = RetrievalEngineEmbeddings._coerce_vector(value.get(key))
                    if vector:
                        return vector
            return []
        for attr in ("embedding", "vector", "data", "embeddings", "vectors"):
            if hasattr(value, attr):
                vector = RetrievalEngineEmbeddings._coerce_vector(
                    getattr(value, attr, None)
                )
                if vector:
                    return vector
        if not isinstance(value, (list, tuple)):
            return []
        vector: list[float] = []
        for item in value:
            try:
                vector.append(float(item))
            except Exception:
                return (
                    RetrievalEngineEmbeddings._coerce_vector(value[0]) if value else []
                )
        return vector

    def _first_vector(self, payload: Any) -> list[float]:
        return self._coerce_vector(payload)

    @staticmethod
    def _normalize_vector(vector: Any) -> list[float]:
        values = RetrievalEngineEmbeddings._coerce_vector(vector)
        if not values or not all(math.isfinite(value) for value in values):
            return []
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm <= 0:
            return []
        return [value / norm for value in values]

    @staticmethod
    def _cosine_similarity(
        query_vector: list[float], memory_vector: list[float]
    ) -> float:
        """余弦相似度。

        查询向量在调用前已由 ``_normalize_vector`` 归一化；库内向量在
        ``list_embedding_candidate_rows`` 缓存填充时已统一 L2 归一化，
        因此这里直接做点积即可，避免对每个候选重复归一化遍历。
        """
        if (
            not query_vector
            or not memory_vector
            or len(query_vector) != len(memory_vector)
        ):
            return 0.0
        score = sum(a * b for a, b in zip(query_vector, memory_vector))
        return max(-1.0, min(1.0, float(score)))

    def _embedding_document_text(self, memory: MemoryRecord) -> str:
        return memory_embedding_text(memory, max_chars=self.embedding_max_text_chars)

    def _embedding_text_hash(self, memory: MemoryRecord) -> str:
        return memory_embedding_text_hash(
            memory, max_chars=self.embedding_max_text_chars
        )
