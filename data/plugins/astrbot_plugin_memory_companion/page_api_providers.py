from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from .core.models import clean_text


class PluginPageApiProviders:
    """Providers; shared runtime resources belong to the facade."""

    def _provider_options(self) -> list[dict[str, str]]:
        options = [{"id": "", "label": "不使用 LLM 压缩"}]
        context = getattr(self.plugin, "context", None)
        getter = getattr(context, "get_all_providers", None)
        if not callable(getter):
            return options
        try:
            providers = getter()
        except Exception:
            return options
        for provider in providers or []:
            try:
                meta = provider.meta()
            except Exception:
                meta = None
            provider_id = str(getattr(meta, "id", "") or "")
            if not provider_id:
                continue
            provider_type = str(getattr(meta, "type", "") or "").strip()
            model_name = str(
                getattr(meta, "model", "") or getattr(provider, "model_name", "") or ""
            ).strip()
            label = provider_id
            if provider_type:
                label = f"{provider_type} ({provider_id})"
            if model_name and model_name not in label:
                label = f"{label} - {model_name}"
            options.append({"id": provider_id, "label": label})
        return options

    async def _rerank_provider_options(self) -> list[dict[str, str]]:
        options = [{"id": "", "label": "自动探测 / 不指定"}]
        seen = {""}
        context = getattr(self.plugin, "context", None)
        manager = getattr(context, "provider_manager", None)
        for provider_config in self._configured_rerank_providers(manager):
            provider_id = clean_text(provider_config.get("id"), 160)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            provider_type = clean_text(provider_config.get("type"), 80)
            model_name = clean_text(
                provider_config.get("rerank_model")
                or provider_config.get("model")
                or provider_config.get("model_name"),
                160,
            )
            enabled = provider_config.get("enable", True)
            label = provider_id
            if provider_type:
                label = f"{provider_type} ({provider_id})"
            if model_name and model_name not in label:
                label = f"{label} - {model_name}"
            if not enabled:
                label = f"{label} - 未启用"
            options.append({"id": provider_id, "label": label})
        providers: list[Any] = []
        for getter_name in ("get_all_rerank_providers", "get_all_providers"):
            getter = getattr(context, getter_name, None)
            if not callable(getter):
                continue
            try:
                result = getter()
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                continue
            for provider in result or []:
                if hasattr(provider, "rerank"):
                    providers.append(provider)
        for provider in getattr(manager, "rerank_provider_insts", []) or []:
            if hasattr(provider, "rerank"):
                providers.append(provider)
        for provider in (
            getattr(manager, "inst_map", {}).values() if manager is not None else []
        ):
            if hasattr(provider, "rerank"):
                providers.append(provider)
        for provider in providers:
            try:
                meta = provider.meta()
            except Exception:
                meta = None
            provider_id = clean_text(getattr(meta, "id", ""), 160)
            if not provider_id:
                provider_id = clean_text(
                    getattr(provider, "id", "") or getattr(provider, "provider_id", ""),
                    160,
                )
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            provider_type = clean_text(getattr(meta, "type", ""), 80)
            model_name = clean_text(
                getattr(meta, "model", "") or getattr(provider, "model", ""), 160
            )
            label = provider_id
            if provider_type:
                label = f"{provider_type} ({provider_id})"
            if model_name and model_name not in label:
                label = f"{label} - {model_name}"
            options.append({"id": provider_id, "label": label})
        return options

    async def _embedding_provider_options(self) -> list[dict[str, str]]:
        options = [{"id": "", "label": "自动探测 / 不指定"}]
        seen = {""}
        context = getattr(self.plugin, "context", None)
        manager = getattr(context, "provider_manager", None)
        for provider_config in self._configured_embedding_providers(manager):
            provider_id = clean_text(provider_config.get("id"), 160)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            provider_type = clean_text(provider_config.get("type"), 80)
            model_name = clean_text(
                provider_config.get("embedding_model")
                or provider_config.get("model")
                or provider_config.get("model_name"),
                160,
            )
            enabled = provider_config.get("enable", True)
            label = provider_id
            if provider_type:
                label = f"{provider_type} ({provider_id})"
            if model_name and model_name not in label:
                label = f"{label} - {model_name}"
            if not enabled:
                label = f"{label} - 未启用"
            options.append({"id": provider_id, "label": label})

        providers: list[Any] = []
        for getter_name in ("get_all_embedding_providers", "get_all_providers"):
            getter = getattr(context, getter_name, None)
            if not callable(getter):
                continue
            try:
                result = getter()
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                continue
            for provider in result or []:
                if self._is_embedding_provider(provider):
                    providers.append(provider)
        for provider in getattr(manager, "embedding_provider_insts", []) or []:
            if self._is_embedding_provider(provider):
                providers.append(provider)
        for provider in (
            getattr(manager, "inst_map", {}).values() if manager is not None else []
        ):
            if self._is_embedding_provider(provider):
                providers.append(provider)

        for provider in providers:
            provider_id, label = self._provider_option_identity(provider)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            options.append({"id": provider_id, "label": label or provider_id})
        return options

    def _configured_rerank_providers(self, manager: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(provider_config: Any) -> None:
            if not isinstance(provider_config, dict):
                return
            provider_id = clean_text(provider_config.get("id"), 160)
            if not provider_id or provider_id in seen:
                return
            provider_type = clean_text(provider_config.get("type"), 80).lower()
            provider_task = clean_text(provider_config.get("provider_type"), 80).lower()
            if provider_task != "rerank" and not provider_type.endswith("_rerank"):
                return
            seen.add(provider_id)
            rows.append(dict(provider_config))

        for provider_config in getattr(manager, "providers_config", []) or []:
            merged = None
            getter = getattr(manager, "get_merged_provider_config", None)
            if callable(getter):
                try:
                    merged = getter(provider_config)
                except Exception:
                    merged = None
            add(merged or provider_config)

        for provider_config in self._cmd_config_provider_configs():
            add(provider_config)
        return rows

    def _configured_embedding_providers(self, manager: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(provider_config: Any) -> None:
            if not isinstance(provider_config, dict):
                return
            provider_id = clean_text(provider_config.get("id"), 160)
            if not provider_id or provider_id in seen:
                return
            provider_type = clean_text(provider_config.get("type"), 80).lower()
            provider_task = clean_text(provider_config.get("provider_type"), 80).lower()
            if (
                provider_task != "embedding"
                and "embedding" not in provider_type
                and "embed" not in provider_type
            ):
                return
            seen.add(provider_id)
            rows.append(dict(provider_config))

        for provider_config in getattr(manager, "providers_config", []) or []:
            merged = None
            getter = getattr(manager, "get_merged_provider_config", None)
            if callable(getter):
                try:
                    merged = getter(provider_config)
                except Exception:
                    merged = None
            add(merged or provider_config)

        for provider_config in self._cmd_config_provider_configs():
            add(provider_config)
        return rows

    @staticmethod
    def _is_embedding_provider(provider: Any) -> bool:
        return any(
            callable(getattr(provider, name, None))
            for name in ("get_embedding", "get_embeddings", "get_embeddings_batch")
        )

    @staticmethod
    def _provider_option_identity(provider: Any) -> tuple[str, str]:
        try:
            meta = provider.meta()
        except Exception:
            meta = None
        provider_id = clean_text(getattr(meta, "id", ""), 160)
        provider_type = clean_text(getattr(meta, "type", ""), 80)
        model_name = clean_text(
            getattr(meta, "model", "") or getattr(provider, "model", ""), 160
        )
        provider_config = getattr(provider, "provider_config", None)
        if not provider_id and isinstance(provider_config, dict):
            provider_id = clean_text(provider_config.get("id"), 160)
            provider_type = provider_type or clean_text(provider_config.get("type"), 80)
            model_name = model_name or clean_text(
                provider_config.get("embedding_model")
                or provider_config.get("rerank_model")
                or provider_config.get("model")
                or provider_config.get("model_name"),
                160,
            )
        if not provider_id and provider_config is not None:
            provider_id = clean_text(getattr(provider_config, "id", ""), 160)
        if not provider_id:
            provider_id = clean_text(
                getattr(provider, "id", "") or getattr(provider, "provider_id", ""), 160
            )
        label = provider_id
        if provider_type:
            label = f"{provider_type} ({provider_id})"
        if model_name and model_name not in label:
            label = f"{label} - {model_name}"
        return provider_id, label

    def _astrbot_cmd_config_path(self) -> Path:
        data_dir = Path(getattr(self.plugin.service, "data_dir", ""))
        root = (
            data_dir.parent.parent
            if data_dir.parent.name == "plugin_data"
            else data_dir.parent
        )
        return root / "cmd_config.json"
