from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .index import (
    EmbeddingAdapter,
    faiss_is_available,
)
from .models import (
    utc_now,
)
from .storage import (
    load_metadata,
)


class SemanticTaskManagerProviders:
    """Providers; shared runtime resources belong to the facade."""

    def _configured_concurrency(self) -> int:
        try:
            value = int(self.config.get("concurrency", 1) or 1)
        except (TypeError, ValueError):
            value = 1
        return max(1, min(16, value))

    @staticmethod
    def _elapsed_seconds(started_at: Any, finished_at: Any = None) -> int:
        try:
            start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            end = (
                datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
                if finished_at
                else datetime.now(timezone.utc)
            )
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            return max(0, int((end - start).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 0

    @staticmethod
    def _normalize_token_usage(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            value = {}
        result = {}
        for key in ("input", "output", "total", "calls"):
            try:
                result[key] = max(0, int(value.get(key, 0) or 0))
            except (TypeError, ValueError):
                result[key] = 0
        if result["total"] <= 0:
            result["total"] = result["input"] + result["output"]
        return result

    def _record_vision_usage(self, pack_id: str, usage: Any) -> None:
        """把本次视觉调用的 token 累加到任务状态，供页面实时展示。"""
        state = self._load_state(pack_id)
        total = self._normalize_token_usage(state.get("token_usage"))
        current = self._normalize_token_usage(usage)
        for key in ("input", "output", "total"):
            total[key] += current[key]
        total["calls"] += max(1, current["calls"])
        state["token_usage"] = total
        state["vision_calls"] = total["calls"]
        state["updated_at"] = utc_now()
        self._save_state(pack_id, state)

    def _vision_provider_details(self) -> dict[str, str | bool]:
        provider_id = str(self.config.get("vision_provider_id") or "").strip()
        details: dict[str, str | bool] = {
            "id": provider_id,
            "model": "",
            "ready": False,
        }
        if not provider_id or self.context is None:
            return details
        resolver = getattr(self.context, "get_provider_by_id", None)
        if not callable(resolver) or not callable(
            getattr(self.context, "llm_generate", None)
        ):
            return details
        try:
            provider = resolver(provider_id)
        except Exception:
            provider = None
        if provider is None:
            return details
        provider_config = getattr(provider, "provider_config", {})
        modalities = (
            provider_config.get("modalities")
            if isinstance(provider_config, dict)
            else None
        )
        if isinstance(modalities, list) and modalities:
            if "image" not in {
                str(value or "").strip().lower() for value in modalities
            }:
                return details
        model = ""
        meta = getattr(provider, "meta", None)
        if callable(meta):
            try:
                model = str(getattr(meta(), "model", "") or "")
            except Exception:
                model = ""
        if not model:
            for key in ("model", "model_name", "chat_model"):
                if isinstance(provider_config, dict) and provider_config.get(key):
                    model = str(provider_config[key])
                    break
        details.update({"model": model, "ready": True})
        return details

    def _vision_provider_ready(self) -> bool:
        return bool(self._vision_provider_details()["ready"])

    def capabilities(
        self, pack_id: str, *, embedding_provider: Any = None
    ) -> dict[str, Any]:
        pack_dir = self._pack_dir(pack_id)
        metadata = load_metadata(pack_dir, scan_legacy=False)
        state = self._load_state(pack_id)
        provider = (
            embedding_provider
            if embedding_provider is not None
            else self._resolve_embedding_provider(pack_id)
        )
        embedding = EmbeddingAdapter(provider)
        return {
            "vision_provider_ready": self._vision_provider_ready(),
            "embedding_provider_ready": embedding.ready,
            "faiss_ready": faiss_is_available(),
            "semantic_metadata_ready": bool(metadata.get("images")),
            "task_status": str(state.get("task_status") or "idle"),
        }

    def _persist_provider_selection(
        self, pack_id: str, embedding: EmbeddingAdapter, selection_mode: str
    ) -> dict[str, Any]:
        if not pack_id or not embedding.ready:
            return {}
        previous = self._load_selection(pack_id)
        same_signature = (
            str(previous.get("effective_provider_id") or "") == embedding.provider_id
            and str(previous.get("embedding_model") or "") == embedding.model_name
            and int(previous.get("configured_dimension", 0) or 0) == embedding.dimension
        )
        payload = {
            "schema_version": "1.0",
            "selection_mode": selection_mode,
            "configured_provider_id": str(
                self.config.get("embedding_provider_id") or ""
            ).strip(),
            "effective_provider_id": embedding.provider_id,
            "embedding_model": embedding.model_name,
            "configured_dimension": embedding.dimension,
            "dimension_verified": bool(
                previous.get("dimension_verified") if same_signature else False
            ),
            "verified_dimension": int(
                previous.get("verified_dimension", 0) if same_signature else 0
            ),
            "verification_error": str(
                previous.get("verification_error") or "" if same_signature else ""
            ),
            "verified_at": str(
                previous.get("verified_at") or "" if same_signature else ""
            ),
            "updated_at": utc_now(),
        }
        self._save_json_atomic(
            self._selection_path(pack_id), payload, ".provider_selection."
        )
        return payload

    def _resolve_embedding_provider(self, pack_id: str = "") -> Any:
        if self.config.get("embedding_provider") is not None:
            provider = self.config.get("embedding_provider")
            adapter = EmbeddingAdapter(provider)
            if pack_id and adapter.ready:
                self._persist_provider_selection(pack_id, adapter, "configured")
            return provider
        context = self.context
        if context is None:
            return None
        provider_id = str(self.config.get("embedding_provider_id") or "").strip()
        resolver = getattr(context, "get_provider_by_id", None)
        if provider_id:
            if not callable(resolver):
                return None
            try:
                provider = resolver(provider_id)
            except Exception:
                return None
            adapter = EmbeddingAdapter(provider, provider_id)
            if pack_id and adapter.ready:
                self._persist_provider_selection(pack_id, adapter, "configured")
            return provider

        # 留空时自动选择，但每个资源包会记住实际选择结果，避免查询时悄悄换模型。
        saved_id = str(
            self._load_selection(pack_id).get("effective_provider_id") or ""
        ).strip()
        if saved_id and callable(resolver):
            try:
                saved_provider = resolver(saved_id)
            except Exception:
                saved_provider = None
            saved_adapter = EmbeddingAdapter(saved_provider, saved_id)
            if saved_adapter.ready:
                self._persist_provider_selection(pack_id, saved_adapter, "automatic")
                return saved_provider

        list_providers = getattr(context, "get_all_embedding_providers", None)
        if not callable(list_providers):
            return None
        try:
            providers = list_providers() or []
        except Exception:
            providers = []
        for provider in providers:
            adapter = EmbeddingAdapter(provider)
            if not adapter.ready:
                continue
            if pack_id:
                self._persist_provider_selection(pack_id, adapter, "automatic")
            return provider
        return None

    def _embedding_adapter(self, pack_id: str = "") -> EmbeddingAdapter:
        configured_id = str(self.config.get("embedding_provider_id") or "").strip()
        return EmbeddingAdapter(
            self._resolve_embedding_provider(pack_id), configured_id
        )

    def _require_embedding_provider(
        self, pack_id: str, action: str
    ) -> EmbeddingAdapter:
        embedding = self._embedding_adapter(pack_id)
        if not embedding.ready:
            configured_id = str(self.config.get("embedding_provider_id") or "").strip()
            if configured_id:
                detail = "配置的 Embedding Provider 不可用"
            else:
                detail = "没有可自动选择的已启用 Embedding Provider"
            raise RuntimeError(
                f"{detail}，无法{action}。请先在 AstrBot 的模型提供商中"
                "启用一个 Embedding 类型模型；也可以在插件配置中明确选择。"
            )
        return embedding

    async def _verify_embedding_dimension(
        self,
        pack_id: str,
        embedding: EmbeddingAdapter,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        selection = self._persist_provider_selection(
            pack_id,
            embedding,
            "configured"
            if str(self.config.get("embedding_provider_id") or "").strip()
            else "automatic",
        )
        if (
            not force
            and selection.get("dimension_verified") is True
            and int(selection.get("verified_dimension", 0) or 0) == embedding.dimension
        ):
            return selection
        try:
            vector = await embedding.embed(
                "AstrBot 表情包向量维度校验", use_cache=False
            )
            selection.update(
                {
                    "dimension_verified": True,
                    "verified_dimension": len(vector),
                    "verification_error": "",
                    "verified_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
        except Exception as exc:
            selection.update(
                {
                    "dimension_verified": False,
                    "verified_dimension": 0,
                    "verification_error": self._safe_error(exc, pack_id),
                    "verified_at": utc_now(),
                    "updated_at": utc_now(),
                }
            )
            self._save_json_atomic(
                self._selection_path(pack_id), selection, ".provider_selection."
            )
            raise RuntimeError(
                "Embedding 向量维度校验失败，任务未创建："
                f"{self._safe_error(exc, pack_id)}"
            ) from exc
        self._save_json_atomic(
            self._selection_path(pack_id), selection, ".provider_selection."
        )
        return selection
