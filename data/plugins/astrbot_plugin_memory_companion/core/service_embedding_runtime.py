from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Mapping
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    MemoryRecord,
    SessionContext,
    clean_text,
    memory_embedding_text,
    memory_embedding_text_hash,
)


class MemoryCompanionServiceEmbeddingRuntime:
    """Embedding runtime; state and transaction ownership remain in the facade."""

    async def _background_embed_memory(
        self, memory_id: str, record: MemoryRecord | None = None
    ) -> None:
        try:
            ctx = (
                self._context_from_memory_record(record)
                if record is not None
                else SessionContext()
            )
            provider, provider_id = await self._resolve_embedding_provider(ctx)
            if provider is None or not provider_id:
                return
            current = await self.store.get_memory(memory_id)
            if current is None:
                return
            await self._embed_memory_record(provider, provider_id, current)
        finally:
            self._embedding_memory_inflight.discard(clean_text(memory_id, 120))

    async def _background_backfill_embeddings(
        self, provider: Any, provider_id: str
    ) -> None:
        provider_id = clean_text(provider_id, 160)
        indexed = 0
        try:
            batch_size = max(
                1,
                min(
                    200, self.config.int("retrieval.embedding_backfill_batch_size", 50)
                ),
            )
            offset = self._embedding_backfill_offsets.get(provider_id, 0)
            (
                records,
                next_offset,
                exhausted,
            ) = await self.store.scan_memories_missing_embeddings(
                provider_id=provider_id,
                limit=batch_size,
                include_pending=self.config.bool(
                    "retrieval.embedding_index_pending", False
                ),
                embedding_max_text_chars=self.config.int(
                    "retrieval.embedding_max_text_chars", 1200
                ),
                offset=offset,
            )
            self._embedding_backfill_offsets[provider_id] = (
                0 if exhausted else next_offset
            )
            for record in records:
                if await self._embed_memory_record(provider, provider_id, record):
                    indexed += 1
            if indexed:
                logger.info(
                    "[MemoryCompanion] 已补齐记忆向量索引: provider=%s count=%s",
                    provider_id,
                    indexed,
                )
        finally:
            self._embedding_backfill_inflight.discard(provider_id)

    async def _embed_memory_record(
        self, provider: Any, provider_id: str, record: MemoryRecord
    ) -> bool:
        if record.lifecycle == "archived":
            return False
        if record.review_status == "pending" and not self.config.bool(
            "retrieval.embedding_index_pending", False
        ):
            return False
        text = self._memory_embedding_text(record)
        text_hash = self._memory_embedding_text_hash(record)
        if not text or not text_hash:
            return False
        await self._acquire_source_semantic_background_slot()
        try:
            try:
                vector = await self._embed_text_with_provider(
                    provider, text, provider_id=provider_id
                )
                vector = self._normalize_embedding_vector(vector)
                if not vector:
                    return False
                current = await self.store.get_memory(record.id)
                if (
                    current is None
                    or self._memory_embedding_text_hash(current) != text_hash
                ):
                    return False
                await self.store.upsert_memory_embedding(
                    memory_id=record.id,
                    provider_id=provider_id,
                    text_hash=text_hash,
                    vector=vector,
                )
                return True
            except Exception as error:
                logger.warning(
                    "[MemoryCompanion] 记忆向量索引失败: provider=%s memory=%s error=%s",
                    provider_id,
                    record.id,
                    self._describe_exception(error),
                )
                return False
        finally:
            self._embedding_background_semaphore.release()

    async def _embed_text_with_provider(
        self,
        provider: Any,
        text: str,
        *,
        provider_id: str = "",
        usage_task: str = "memory_embedding",
        strict_input: bool = False,
        input_limit: int = 0,
    ) -> list[float]:
        if strict_input:
            if (
                not isinstance(text, str)
                or not text
                or "\x00" in text
                or len(text) > max(1, int(input_limit or 1))
            ):
                raise ValueError("semantic_input_limit_or_shape_invalid")
        else:
            text = clean_text(
                text,
                max(200, self.config.int("retrieval.embedding_max_text_chars", 1200)),
            )

        async def wait_result(value: Any) -> Any:
            if inspect.isawaitable(value):
                timeout_ms = self._embedding_timeout_ms()
                if timeout_ms > 0:
                    return await asyncio.wait_for(value, timeout=timeout_ms / 1000.0)
                return await value
            return value

        get_embedding = getattr(provider, "get_embedding", None)
        get_embeddings = getattr(provider, "get_embeddings", None)
        get_embeddings_batch = getattr(provider, "get_embeddings_batch", None)
        started = time.monotonic()
        payload: Any = None
        success = False
        error = ""
        called_provider = False
        try:
            if callable(get_embedding):
                called_provider = True
                payload = await wait_result(get_embedding(text))
                success = True
                return (
                    self._coerce_single_embedding_response(payload)
                    if strict_input
                    else self._coerce_embedding_vector(payload)
                )

            if callable(get_embeddings):
                called_provider = True
                payload = await wait_result(get_embeddings([text]))
                success = True
                return (
                    self._coerce_single_embedding_response(payload)
                    if strict_input
                    else self._first_embedding_vector(payload)
                )

            if callable(get_embeddings_batch):
                called_provider = True
                if strict_input:
                    payload = await wait_result(get_embeddings_batch([text]))
                else:
                    try:
                        payload = await wait_result(
                            get_embeddings_batch(
                                [text], batch_size=1, tasks_limit=1, max_retries=1
                            )
                        )
                    except TypeError:
                        payload = await wait_result(get_embeddings_batch([text]))
                success = True
                return (
                    self._coerce_single_embedding_response(payload)
                    if strict_input
                    else self._first_embedding_vector(payload)
                )
            return []
        except asyncio.CancelledError as exc:
            error = self._describe_exception(exc)
            raise
        except Exception as exc:
            error = self._describe_exception(exc)
            raise
        finally:
            if called_provider:
                self._record_token_usage(
                    task=usage_task,
                    provider_id=provider_id
                    or self._provider_runtime_id(provider)
                    or "<auto>",
                    prompt=text,
                    completion="",
                    resp=payload,
                    success=success,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    error=error,
                )

    def _embedding_timeout_ms(self) -> int:
        timeout_ms = self.config.int("retrieval.embedding_timeout_ms", 5000)
        if timeout_ms <= 0:
            return 0
        # 1.4.1 的默认 1500ms 对不少 OpenAI 兼容 embedding 公益站过紧，API 最终成功时插件已先超时。
        if timeout_ms == 1500:
            return 5000
        return timeout_ms

    @staticmethod
    def _describe_exception(error: BaseException) -> str:
        message = str(error).strip()
        name = type(error).__name__
        return f"{name}: {message}" if message else name

    @staticmethod
    def _coerce_embedding_vector(value: Any) -> list[float]:
        if value is None:
            return []
        if isinstance(value, dict):
            for key in ("embedding", "vector"):
                if key in value:
                    vector = (
                        MemoryCompanionServiceEmbeddingRuntime._coerce_embedding_vector(
                            value.get(key)
                        )
                    )
                    if vector:
                        return vector
            for key in ("data", "embeddings", "vectors"):
                if key in value:
                    vector = (
                        MemoryCompanionServiceEmbeddingRuntime._coerce_embedding_vector(
                            value.get(key)
                        )
                    )
                    if vector:
                        return vector
            return []
        for attr in ("embedding", "vector", "data", "embeddings", "vectors"):
            if hasattr(value, attr):
                vector = (
                    MemoryCompanionServiceEmbeddingRuntime._coerce_embedding_vector(
                        getattr(value, attr, None)
                    )
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
                    MemoryCompanionServiceEmbeddingRuntime._coerce_embedding_vector(
                        value[0]
                    )
                    if value
                    else []
                )
        return vector

    def _first_embedding_vector(self, payload: Any) -> list[float]:
        return self._coerce_embedding_vector(payload)

    @classmethod
    def _coerce_single_embedding_response(cls, payload: Any) -> list[float]:
        if isinstance(payload, Mapping):
            for key in ("embedding", "vector"):
                if key in payload:
                    return cls._coerce_embedding_vector(payload.get(key))
            for key in ("data", "embeddings", "vectors"):
                if key in payload:
                    return cls._coerce_single_embedding_response(payload.get(key))
        for attr in ("embedding", "vector"):
            if hasattr(payload, attr):
                return cls._coerce_embedding_vector(getattr(payload, attr, None))
        for attr in ("data", "embeddings", "vectors"):
            if hasattr(payload, attr):
                return cls._coerce_single_embedding_response(
                    getattr(payload, attr, None)
                )
        if isinstance(payload, (list, tuple)):
            if payload and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in payload
            ):
                return cls._coerce_embedding_vector(payload)
            if len(payload) != 1:
                raise ValueError("semantic_embedding_response_count_mismatch")
            return cls._coerce_single_embedding_response(payload[0])
        return cls._coerce_embedding_vector(payload)

    @staticmethod
    def _normalize_embedding_vector(vector: Any) -> list[float]:
        values = MemoryCompanionServiceEmbeddingRuntime._coerce_embedding_vector(vector)
        if not values:
            return []
        norm = sum(value * value for value in values) ** 0.5
        if norm <= 0:
            return []
        return [value / norm for value in values]

    def _memory_embedding_text(self, record: MemoryRecord) -> str:
        return memory_embedding_text(
            record,
            max_chars=max(
                200, self.config.int("retrieval.embedding_max_text_chars", 1200)
            ),
        )

    def _memory_embedding_text_hash(self, record: MemoryRecord) -> str:
        return memory_embedding_text_hash(
            record,
            max_chars=max(
                200, self.config.int("retrieval.embedding_max_text_chars", 1200)
            ),
        )
