from __future__ import annotations

from typing import Any

from .astrbot_compat import (
    logger,
)
from .identity import (
    maybe_await,
)
from .models import (
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceProviderSelection:
    """Provider selection; state and transaction ownership remain in the facade."""

    async def _resolve_rerank_provider(
        self, ctx: SessionContext, *, mode: str
    ) -> tuple[Any, str]:
        provider_id = clean_text(
            self.config.get("retrieval.rerank_provider_id", ""), 160
        )
        if provider_id:
            provider = await self._rerank_provider_by_id(provider_id)
            if provider is not None:
                return provider, provider_id
            logger.warning(
                "[MemoryCompanion] 记忆重排 Provider 不可用，自动降级本地检索: provider_id=%s session=%s",
                provider_id,
                ctx.session_id,
            )
            return None, provider_id
        if mode != "auto":
            return None, ""
        provider, detected_id = await self._first_rerank_provider()
        return provider, detected_id

    async def _rerank_provider_by_id(self, provider_id: str) -> Any:
        if self.context is None:
            return None
        for getter_name in ("get_rerank_provider_by_id", "get_provider_by_id"):
            getter = getattr(self.context, getter_name, None)
            if not callable(getter):
                continue
            try:
                provider = await maybe_await(getter(provider_id))
            except Exception as error:
                logger.warning(
                    "[MemoryCompanion] 获取记忆重排 Provider 失败: getter=%s provider_id=%s error=%s",
                    getter_name,
                    provider_id,
                    error,
                )
                continue
            if provider is not None and hasattr(provider, "rerank"):
                return provider
        return None

    async def _first_rerank_provider(self) -> tuple[Any, str]:
        if self.context is None:
            return None, ""
        for getter_name in ("get_all_rerank_providers", "get_all_providers"):
            getter = getattr(self.context, getter_name, None)
            if not callable(getter):
                continue
            try:
                providers = await maybe_await(getter())
            except Exception as error:
                logger.warning(
                    "[MemoryCompanion] 扫描记忆重排 Provider 失败: getter=%s error=%s",
                    getter_name,
                    error,
                )
                continue
            for provider in providers or []:
                if hasattr(provider, "rerank"):
                    return provider, self._provider_runtime_id(provider) or "<auto>"
        return None, ""

    async def _resolve_embedding_provider(self, ctx: SessionContext) -> tuple[Any, str]:
        provider_id = clean_text(
            self.config.get("retrieval.embedding_provider_id", ""), 160
        )
        if provider_id:
            provider = await self._embedding_provider_by_id(provider_id)
            if provider is not None:
                return provider, provider_id
            warn_key = f"configured:{provider_id}"
            if warn_key not in self._embedding_provider_warned:
                self._embedding_provider_warned.add(warn_key)
                logger.warning(
                    "[MemoryCompanion] 记忆嵌入 Provider 不可用，向量召回暂时关闭: provider_id=%s session=%s",
                    provider_id,
                    ctx.session_id,
                )
            return None, provider_id

        provider, detected_id = await self._first_embedding_provider()
        if provider is None:
            warn_key = "auto:none"
            if warn_key not in self._embedding_provider_warned:
                self._embedding_provider_warned.add(warn_key)
                logger.warning(
                    "[MemoryCompanion] 未发现可用 Embedding Provider，向量召回暂时关闭"
                )
        return provider, detected_id

    async def _embedding_provider_by_id(self, provider_id: str) -> Any:
        if self.context is None:
            return None
        for getter_name in ("get_embedding_provider_by_id", "get_provider_by_id"):
            getter = getattr(self.context, getter_name, None)
            if not callable(getter):
                continue
            try:
                provider = await maybe_await(getter(provider_id))
            except Exception as error:
                logger.warning(
                    "[MemoryCompanion] 获取记忆嵌入 Provider 失败: getter=%s provider_id=%s error=%s",
                    getter_name,
                    provider_id,
                    error,
                )
                continue
            if provider is not None and self._is_embedding_provider(provider):
                return provider
        return None

    async def _first_embedding_provider(self) -> tuple[Any, str]:
        if self.context is None:
            return None, ""
        for getter_name in ("get_all_embedding_providers", "get_all_providers"):
            getter = getattr(self.context, getter_name, None)
            if not callable(getter):
                continue
            try:
                providers = await maybe_await(getter())
            except Exception as error:
                logger.warning(
                    "[MemoryCompanion] 扫描记忆嵌入 Provider 失败: getter=%s error=%s",
                    getter_name,
                    error,
                )
                continue
            for provider in providers or []:
                if self._is_embedding_provider(provider):
                    return provider, self._provider_runtime_id(provider) or "<auto>"
        return None, ""

    @staticmethod
    def _is_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    def _provider_runtime_id(self, provider: Any, *, fallback_type: bool = True) -> str:
        try:
            meta = (
                provider.meta() if callable(getattr(provider, "meta", None)) else None
            )
        except Exception:
            meta = None
        if meta is not None:
            provider_id = clean_text(
                meta.get("id") if isinstance(meta, dict) else getattr(meta, "id", ""),
                160,
            )
            if provider_id:
                return provider_id
        provider_config = getattr(provider, "provider_config", None)
        if isinstance(provider_config, dict):
            provider_id = clean_text(provider_config.get("id"), 160)
            if provider_id:
                return provider_id
        elif provider_config is not None:
            provider_id = clean_text(getattr(provider_config, "id", ""), 160)
            if provider_id:
                return provider_id
        for attr in ("id", "provider_id", "name"):
            value = clean_text(getattr(provider, attr, ""), 160)
            if value:
                return value
        return clean_text(type(provider).__name__, 160) if fallback_type else ""

    async def _summary_provider_attempts(
        self, ctx: SessionContext
    ) -> list[dict[str, Any]]:
        """Resolve summary providers with private/group-specific overrides.

        The original ``provider_id``/``fallback_provider_id`` pair remains a
        shared compatibility fallback.  A scene-specific pair takes
        precedence when configured, while the generic pair is still tried
        before falling back to the provider currently active for the session.
        This keeps existing installations working and lets private and group
        timelines use independent models.
        """
        scope = clean_text(getattr(ctx, "scope", ""), 40).lower()
        scene = "group" if scope == "group" else "private" if scope == "private" else ""
        if not scene:
            return await self._provider_attempts(
                ctx,
                prefix="memory_summary",
                provider_key="provider_id",
                fallback_provider_key="fallback_provider_id",
                include_current=True,
            )

        # Resolve scene-specific providers first, then preserve the legacy
        # generic pair as a second chance. The helper de-duplicates provider
        # IDs/objects so equal scene and generic settings are only called once.
        return await self._provider_attempts(
            ctx,
            prefix="memory_summary",
            provider_key=f"{scene}_provider_id",
            fallback_provider_key=f"{scene}_fallback_provider_id",
            include_current=True,
            compatibility_provider_key="provider_id",
            compatibility_fallback_provider_key="fallback_provider_id",
        )

    async def _provider_attempts(
        self,
        ctx: SessionContext,
        *,
        prefix: str,
        provider_key: str,
        fallback_provider_key: str,
        include_current: bool,
        compatibility_provider_key: str = "",
        compatibility_fallback_provider_key: str = "",
    ) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_provider_objects: set[int] = set()
        configured: list[tuple[str, str]] = []
        primary_id = clean_text(self.config.get(f"{prefix}.{provider_key}", ""), 120)
        fallback_id = clean_text(
            self.config.get(f"{prefix}.{fallback_provider_key}", ""), 120
        )
        compatibility_primary_id = (
            clean_text(
                self.config.get(f"{prefix}.{compatibility_provider_key}", ""),
                120,
            )
            if compatibility_provider_key
            else ""
        )
        compatibility_fallback_id = (
            clean_text(
                self.config.get(f"{prefix}.{compatibility_fallback_provider_key}", ""),
                120,
            )
            if compatibility_fallback_provider_key
            else ""
        )
        # Keep each scene's primary/fallback together, then use the legacy
        # generic pair. This makes a dedicated fallback meaningful while
        # preserving old configurations during gradual migration.
        if primary_id:
            configured.append(
                (
                    "primary" if not compatibility_provider_key else "scene_primary",
                    primary_id,
                )
            )
        if fallback_id:
            configured.append(
                (
                    "fallback" if not compatibility_provider_key else "scene_fallback",
                    fallback_id,
                )
            )
        if compatibility_primary_id:
            configured.append(("primary", compatibility_primary_id))
        if compatibility_fallback_id:
            configured.append(("fallback", compatibility_fallback_id))
        for source, provider_id in configured:
            if not provider_id:
                continue
            provider = await self._provider_by_id(provider_id, ctx, source)
            if provider is None:
                continue
            runtime_id = self._provider_runtime_id(provider, fallback_type=False)
            identity_ids = {item for item in (provider_id, runtime_id) if item}
            if id(provider) in seen_provider_objects or identity_ids.intersection(seen):
                continue
            seen.update(identity_ids)
            seen_provider_objects.add(id(provider))
            attempts.append(
                {
                    "source": source,
                    "provider_id": provider_id,
                    "provider": provider,
                }
            )

        if not include_current:
            return attempts
        current = await self._current_provider(ctx)
        if current is not None:
            current_id = self._provider_runtime_id(current, fallback_type=False)
            if id(current) not in seen_provider_objects and (
                not current_id or current_id not in seen
            ):
                attempts.append(
                    {
                        "source": "current_session",
                        "provider_id": "",
                        "provider": current,
                    }
                )
        return attempts

    async def _summary_provider(self, ctx: SessionContext) -> Any:
        attempts = await self._summary_provider_attempts(ctx)
        return attempts[0]["provider"] if attempts else None

    async def _provider_by_id(
        self, provider_id: str, ctx: SessionContext, source: str
    ) -> Any:
        provider_id = clean_text(provider_id, 120)
        if not provider_id or self.context is None:
            return None
        getter = getattr(self.context, "get_provider_by_id", None)
        if not callable(getter):
            return None
        try:
            provider = await maybe_await(getter(provider_id))
            if provider is not None:
                return provider
            logger.warning(
                "[MemoryCompanion] 总结模型提供商不可用: source=%s provider_id=%s session=%s",
                source,
                provider_id,
                ctx.session_id,
            )
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 获取总结模型提供商失败: source=%s provider_id=%s error=%s",
                source,
                provider_id,
                exc,
                exc_info=True,
            )
        return None

    async def _current_provider(self, ctx: SessionContext) -> Any:
        if self.context is None:
            return None
        provider_getter = getattr(self.context, "get_using_provider", None)
        if not callable(provider_getter):
            return None
        try:
            return await maybe_await(provider_getter(ctx.session_id))
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 获取当前会话 Provider 失败: session=%s error=%s",
                ctx.session_id,
                exc,
            )
            return None
