from __future__ import annotations

from .core.models import clean_text
from .page_api_support import (
    DEFAULT_THEME_NAME,
    THEME_NAME_TO_KEY,
)


class PluginPageApiConfiguration:
    """Configuration; shared runtime resources belong to the facade."""

    async def context_config(self):
        config = self.plugin.service.config
        theme_name = str(config.get("appearance.theme", DEFAULT_THEME_NAME))
        return self._ok(
            {
                "appearance": {
                    "theme": theme_name,
                    "theme_key": self._theme_key(theme_name),
                    "available_themes": list(THEME_NAME_TO_KEY.keys()),
                },
                "conversation_memory": {
                    "enabled": config.bool("conversation_memory.enabled", True),
                    "capture_group_messages": config.bool(
                        "conversation_memory.capture_group_messages", True
                    ),
                    "idle_gap_minutes": config.int(
                        "conversation_memory.idle_gap_minutes", 20
                    ),
                    "recent_events_for_followup": config.int(
                        "conversation_memory.recent_events_for_followup", 12
                    ),
                    "time_window_timeline_limit": config.int(
                        "conversation_memory.time_window_timeline_limit", 12
                    ),
                    "recent_fact_guard_enabled": config.bool(
                        "conversation_memory.recent_fact_guard_enabled", True
                    ),
                    "recent_fact_guard_hours": config.int(
                        "conversation_memory.recent_fact_guard_hours", 3
                    ),
                    "recent_fact_guard_event_limit": config.int(
                        "conversation_memory.recent_fact_guard_event_limit", 24
                    ),
                    "recent_fact_guard_max_items": config.int(
                        "conversation_memory.recent_fact_guard_max_items", 4
                    ),
                    "low_information_guard_enabled": config.bool(
                        "conversation_memory.low_information_guard_enabled", True
                    ),
                    "low_information_gap_minutes": config.int(
                        "conversation_memory.low_information_gap_minutes", 20
                    ),
                    "suppress_memory_on_low_information": config.bool(
                        "conversation_memory.suppress_memory_on_low_information", True
                    ),
                    "topic_shift_guard_enabled": config.bool(
                        "conversation_memory.topic_shift_guard_enabled", True
                    ),
                    "suppress_memory_on_topic_shift": config.bool(
                        "conversation_memory.suppress_memory_on_topic_shift", True
                    ),
                    "topic_shift_guard_recent_events": config.int(
                        "conversation_memory.topic_shift_guard_recent_events", 6
                    ),
                    "group_actor_relevance_guard_enabled": config.bool(
                        "conversation_memory.group_actor_relevance_guard_enabled", True
                    ),
                },
                "scope_control": {
                    "private_capture_enabled": config.bool(
                        "scope_control.private_capture_enabled", True
                    ),
                    "group_capture_enabled": config.bool(
                        "scope_control.group_capture_enabled", True
                    ),
                    "private_recall_enabled": config.bool(
                        "scope_control.private_recall_enabled", True
                    ),
                    "group_recall_enabled": config.bool(
                        "scope_control.group_recall_enabled", True
                    ),
                    "private_topology_enabled": config.bool(
                        "scope_control.private_topology_enabled", True
                    ),
                    "group_topology_enabled": config.bool(
                        "scope_control.group_topology_enabled", True
                    ),
                },
                "provider_options": self._provider_options(),
                "rerank_provider_options": await self._rerank_provider_options(),
                "embedding_provider_options": await self._embedding_provider_options(),
                "retrieval": {
                    "mode": str(config.get("retrieval.mode", "auto") or "auto"),
                    "rerank_provider_id": str(
                        config.get("retrieval.rerank_provider_id", "") or ""
                    ),
                    "rerank_candidate_multiplier": config.int(
                        "retrieval.rerank_candidate_multiplier", 5
                    ),
                    "rerank_candidate_limit": config.int(
                        "retrieval.rerank_candidate_limit", 32
                    ),
                    "rerank_timeout_ms": config.int(
                        "retrieval.rerank_timeout_ms", 1200
                    ),
                    "embedding_enabled": config.bool(
                        "retrieval.embedding_enabled", False
                    ),
                    "embedding_provider_id": str(
                        config.get("retrieval.embedding_provider_id", "") or ""
                    ),
                    "embedding_candidate_limit": config.int(
                        "retrieval.embedding_candidate_limit", 1200
                    ),
                    "embedding_top_k": config.int("retrieval.embedding_top_k", 32),
                    "embedding_score_threshold": config.float(
                        "retrieval.embedding_score_threshold", 0.34
                    ),
                    "embedding_weight": config.float(
                        "retrieval.embedding_weight", 0.55
                    ),
                    "embedding_timeout_ms": config.int(
                        "retrieval.embedding_timeout_ms", 5000
                    ),
                    "embedding_max_text_chars": config.int(
                        "retrieval.embedding_max_text_chars", 1200
                    ),
                    "embedding_backfill_enabled": config.bool(
                        "retrieval.embedding_backfill_enabled", True
                    ),
                    "embedding_backfill_batch_size": config.int(
                        "retrieval.embedding_backfill_batch_size", 50
                    ),
                    "embedding_backfill_interval_seconds": config.int(
                        "retrieval.embedding_backfill_interval_seconds", 300
                    ),
                    "embedding_background_concurrency": config.int(
                        "retrieval.embedding_background_concurrency", 2
                    ),
                    "current_window_candidate_limit": config.int(
                        "retrieval.current_window_candidate_limit", 600
                    ),
                    "keyword_fallback_min_fts_candidates": config.int(
                        "retrieval.keyword_fallback_min_fts_candidates", 80
                    ),
                },
                "knowledge_graph": {
                    "enabled": config.bool("knowledge_graph.enabled", True),
                    "retrieval_expansion_enabled": config.bool(
                        "knowledge_graph.retrieval_expansion_enabled",
                        True,
                    ),
                    "expansion_limit": config.int(
                        "knowledge_graph.expansion_limit", 12
                    ),
                    "backfill_limit": config.int("knowledge_graph.backfill_limit", 300),
                },
                "memory_injection": {
                    "enabled": config.bool("memory_injection.enabled", True),
                    "features_removed": False,
                    "top_k": config.int("memory_injection.top_k", 10),
                    "max_chars": config.int("memory_injection.max_chars", 4000),
                    "temporal_aggregate_max_chars": config.int(
                        "memory_injection.temporal_aggregate_max_chars",
                        3600,
                    ),
                    "include_raw_events": config.bool(
                        "memory_injection.include_raw_events", False
                    ),
                    "enable_injection_logs": config.bool(
                        "memory_injection.enable_injection_logs", True
                    ),
                    "debug_log_injection_enabled": config.bool(
                        "memory_injection.debug_log_injection_enabled",
                        False,
                    ),
                    "debug_log_max_chars": config.int(
                        "memory_injection.debug_log_max_chars", 12000
                    ),
                },
                "context_orchestration": {
                    "enabled": config.bool("context_orchestration.enabled", True),
                    "features_removed": False,
                    "query_mode": str(
                        config.get(
                            "context_orchestration.query_mode", "current_message"
                        )
                        or "current_message"
                    ),
                    "include_intent_context": config.bool(
                        "context_orchestration.include_intent_context", True
                    ),
                    "intent_max_chars": config.int(
                        "context_orchestration.intent_max_chars", 520
                    ),
                    "self_timeline_limit": config.int(
                        "context_orchestration.self_timeline_limit", 2
                    ),
                    "user_profile_limit": config.int(
                        "context_orchestration.user_profile_limit", 2
                    ),
                    "current_window_limit": config.int(
                        "context_orchestration.current_window_limit", 3
                    ),
                    "conversation_summary_limit": config.int(
                        "context_orchestration.conversation_summary_limit", 2
                    ),
                    "stable_memory_limit": config.int(
                        "context_orchestration.stable_memory_limit", 3
                    ),
                },
                "memory_summary": {
                    "enabled": config.bool("memory_summary.enabled", True),
                    "provider_id": str(
                        config.get("memory_summary.provider_id", "") or ""
                    ),
                    "fallback_provider_id": str(
                        config.get("memory_summary.fallback_provider_id", "") or ""
                    ),
                    "private_provider_id": str(
                        config.get("memory_summary.private_provider_id", "") or ""
                    ),
                    "private_fallback_provider_id": str(
                        config.get("memory_summary.private_fallback_provider_id", "")
                        or ""
                    ),
                    "group_provider_id": str(
                        config.get("memory_summary.group_provider_id", "") or ""
                    ),
                    "group_fallback_provider_id": str(
                        config.get("memory_summary.group_fallback_provider_id", "")
                        or ""
                    ),
                    "min_events": config.int("memory_summary.min_events", 20),
                    "trigger_event_count": config.int(
                        "memory_summary.trigger_event_count", 20
                    ),
                    "trigger_interval_minutes": config.int(
                        "memory_summary.trigger_interval_minutes", 60
                    ),
                    "max_events_per_summary": config.int(
                        "memory_summary.max_events_per_summary", 40
                    ),
                    "max_retries": config.int("memory_summary.max_retries", 3),
                    "retry_backoff_seconds": config.int(
                        "memory_summary.retry_backoff_seconds", 60
                    ),
                    "transient_retry_cooldown_minutes": config.int(
                        "memory_summary.transient_retry_cooldown_minutes", 10
                    ),
                },
                "memory_tools": {
                    "enable_recall_tool": config.bool(
                        "memory_tools.enable_recall_tool", True
                    ),
                    "enable_remember_tool": config.bool(
                        "memory_tools.enable_remember_tool", True
                    ),
                    "enable_note_tools": config.bool(
                        "memory_tools.enable_note_tools", True
                    ),
                },
                "private_companion_bridge": {
                    "enabled": config.bool("private_companion_bridge.enabled", True),
                    "accept_external_records": config.bool(
                        "private_companion_bridge.accept_external_records", True
                    ),
                    "cross_window_emotional_continuity_enabled": config.bool(
                        "private_companion_bridge.cross_window_emotional_continuity_enabled",
                        False,
                    ),
                    "dedupe_prompt_context": config.bool(
                        "private_companion_bridge.dedupe_prompt_context", True
                    ),
                    "prefer_memory_companion_memory": config.bool(
                        "private_companion_bridge.prefer_memory_companion_memory",
                        True,
                    ),
                    "clean_proactive_history": config.bool(
                        "private_companion_bridge.clean_proactive_history", True
                    ),
                    "suppress_self_timeline_when_companion_seen": config.bool(
                        "private_companion_bridge.suppress_self_timeline_when_companion_seen",
                        True,
                    ),
                    "suppress_user_context_when_companion_seen": config.bool(
                        "private_companion_bridge.suppress_user_context_when_companion_seen",
                        True,
                    ),
                    "context_features_removed": False,
                },
                "visibility": {
                    "allow_self_timeline_everywhere": config.bool(
                        "visibility.allow_self_timeline_everywhere", True
                    ),
                    "allow_group_public_in_private": config.bool(
                        "visibility.allow_group_public_in_private", False
                    ),
                    "enable_acl_rules": config.bool(
                        "visibility.enable_acl_rules", True
                    ),
                },
                "maintenance": {
                    "retention_raw_event_days": config.int(
                        "maintenance.retention_raw_event_days", 30
                    ),
                    "retention_raw_event_limit": config.int(
                        "maintenance.retention_raw_event_limit", 1000
                    ),
                    "retention_summarized_timeline_days": config.int(
                        "maintenance.retention_summarized_timeline_days", 30
                    ),
                    "retention_injection_log_days": config.int(
                        "maintenance.retention_injection_log_days", 14
                    ),
                    "retention_cleanup_limit": config.int(
                        "maintenance.retention_cleanup_limit", 2000
                    ),
                    "memory_decay_enabled": config.bool(
                        "maintenance.memory_decay_enabled", True
                    ),
                    "memory_decay_after_days": config.int(
                        "maintenance.memory_decay_after_days", 180
                    ),
                    "memory_decay_idle_days": config.int(
                        "maintenance.memory_decay_idle_days", 90
                    ),
                    "memory_decay_max_importance_percent": config.int(
                        "maintenance.memory_decay_max_importance_percent",
                        74,
                    ),
                    "memory_decay_score_threshold_percent": config.int(
                        "maintenance.memory_decay_score_threshold_percent",
                        75,
                    ),
                    "memory_decay_max_candidates": config.int(
                        "maintenance.memory_decay_max_candidates", 120
                    ),
                    "memory_decay_max_groups": config.int(
                        "maintenance.memory_decay_max_groups", 8
                    ),
                    "memory_decay_min_items_per_summary": config.int(
                        "maintenance.memory_decay_min_items_per_summary",
                        4,
                    ),
                    "memory_decay_max_items_per_summary": config.int(
                        "maintenance.memory_decay_max_items_per_summary",
                        24,
                    ),
                },
                "sleep_maintenance": self.plugin.service.sleep_status(),
            }
        )

    async def retrieval_config_update(self):
        payload = await self._json()
        raw = self.plugin.service.config.raw
        if not isinstance(raw, dict):
            return self._err("runtime config is not writable", 500)
        mode = clean_text(payload.get("mode"), 40).lower()
        if mode not in {"auto", "basic", "rerank"}:
            mode = "auto"
        core_values = {
            "mode": mode,
            "rerank_provider_id": clean_text(payload.get("rerank_provider_id"), 160),
            "rerank_candidate_multiplier": max(
                1, self._int(payload.get("rerank_candidate_multiplier"), 5)
            ),
            "rerank_candidate_limit": max(
                1, self._int(payload.get("rerank_candidate_limit"), 32)
            ),
            "embedding_enabled": bool(payload.get("embedding_enabled")),
            "embedding_provider_id": clean_text(
                payload.get("embedding_provider_id"), 160
            ),
        }
        advanced_values = {
            "rerank_timeout_ms": max(
                0, self._int(payload.get("rerank_timeout_ms"), 1200)
            ),
            "embedding_candidate_limit": max(
                1, self._int(payload.get("embedding_candidate_limit"), 1200)
            ),
            "embedding_top_k": max(1, self._int(payload.get("embedding_top_k"), 32)),
            "embedding_score_threshold": max(
                0.0,
                min(1.0, self._float(payload.get("embedding_score_threshold"), 0.34)),
            ),
            "embedding_weight": max(
                0.0, min(2.0, self._float(payload.get("embedding_weight"), 0.55))
            ),
            "embedding_timeout_ms": max(
                0, self._int(payload.get("embedding_timeout_ms"), 5000)
            ),
            "embedding_max_text_chars": max(
                200, self._int(payload.get("embedding_max_text_chars"), 1200)
            ),
            "embedding_backfill_enabled": bool(
                payload.get("embedding_backfill_enabled", True)
            ),
            "embedding_backfill_batch_size": max(
                1, self._int(payload.get("embedding_backfill_batch_size"), 50)
            ),
        }
        raw.setdefault("retrieval", {})
        if not isinstance(raw["retrieval"], dict):
            raw["retrieval"] = {}
        raw["retrieval"].update(core_values)
        raw.setdefault("retrieval_advanced", {})
        if not isinstance(raw["retrieval_advanced"], dict):
            raw["retrieval_advanced"] = {}
        raw["retrieval_advanced"].update(advanced_values)
        self._write_plugin_config(raw)
        return self._ok(
            {
                "retrieval": {
                    "mode": str(
                        self.plugin.service.config.get(
                            "retrieval.mode", core_values["mode"]
                        )
                        or core_values["mode"]
                    ),
                    "rerank_provider_id": str(
                        self.plugin.service.config.get(
                            "retrieval.rerank_provider_id",
                            core_values["rerank_provider_id"],
                        )
                        or core_values["rerank_provider_id"]
                    ),
                    "rerank_candidate_multiplier": self.plugin.service.config.int(
                        "retrieval.rerank_candidate_multiplier",
                        core_values["rerank_candidate_multiplier"],
                    ),
                    "rerank_candidate_limit": self.plugin.service.config.int(
                        "retrieval.rerank_candidate_limit",
                        core_values["rerank_candidate_limit"],
                    ),
                    "rerank_timeout_ms": self.plugin.service.config.int(
                        "retrieval.rerank_timeout_ms",
                        advanced_values["rerank_timeout_ms"],
                    ),
                    "embedding_enabled": self.plugin.service.config.bool(
                        "retrieval.embedding_enabled",
                        core_values["embedding_enabled"],
                    ),
                    "embedding_provider_id": str(
                        self.plugin.service.config.get(
                            "retrieval.embedding_provider_id",
                            core_values["embedding_provider_id"],
                        )
                        or core_values["embedding_provider_id"]
                    ),
                    "embedding_candidate_limit": self.plugin.service.config.int(
                        "retrieval.embedding_candidate_limit",
                        advanced_values["embedding_candidate_limit"],
                    ),
                    "embedding_top_k": self.plugin.service.config.int(
                        "retrieval.embedding_top_k",
                        advanced_values["embedding_top_k"],
                    ),
                    "embedding_score_threshold": self.plugin.service.config.float(
                        "retrieval.embedding_score_threshold",
                        advanced_values["embedding_score_threshold"],
                    ),
                    "embedding_weight": self.plugin.service.config.float(
                        "retrieval.embedding_weight",
                        advanced_values["embedding_weight"],
                    ),
                    "embedding_timeout_ms": self.plugin.service.config.int(
                        "retrieval.embedding_timeout_ms",
                        advanced_values["embedding_timeout_ms"],
                    ),
                    "embedding_max_text_chars": self.plugin.service.config.int(
                        "retrieval.embedding_max_text_chars",
                        advanced_values["embedding_max_text_chars"],
                    ),
                    "embedding_backfill_enabled": self.plugin.service.config.bool(
                        "retrieval.embedding_backfill_enabled",
                        advanced_values["embedding_backfill_enabled"],
                    ),
                    "embedding_backfill_batch_size": self.plugin.service.config.int(
                        "retrieval.embedding_backfill_batch_size",
                        advanced_values["embedding_backfill_batch_size"],
                    ),
                },
                "rerank_provider_options": await self._rerank_provider_options(),
                "embedding_provider_options": await self._embedding_provider_options(),
            }
        )

    async def config_schema(self):
        try:
            schema = self._load_config_schema()
            return self._ok(
                {
                    "schema": schema,
                    "values": self._schema_config_values(schema),
                    "provider_options": self._provider_options(),
                    "rerank_provider_options": await self._rerank_provider_options(),
                    "embedding_provider_options": await self._embedding_provider_options(),
                }
            )
        except Exception as exc:
            return self._err(f"配置 schema 读取失败: {exc}", 500)

    async def config_module_update(self):
        payload = await self._json()
        module = clean_text(payload.get("module"), 80)
        values = payload.get("values")
        if not isinstance(values, dict):
            return self._err("values must be an object", 400)
        raw = self.plugin.service.config.raw
        if not isinstance(raw, dict):
            return self._err("runtime config is not writable", 500)
        schema = self._load_config_schema()
        module_schema = schema.get(module)
        if not isinstance(module_schema, dict):
            return self._err("unknown config module", 400)
        items = module_schema.get("items")
        if not isinstance(items, dict):
            return self._err("invalid config module", 400)

        target = raw.setdefault(module, {})
        if not isinstance(target, dict):
            target = {}
            raw[module] = target
        for key, item_schema in items.items():
            if key not in values or not isinstance(item_schema, dict):
                continue
            target[key] = self._coerce_config_value(values.get(key), item_schema)
        self._write_plugin_config(raw)
        return self._ok(
            {
                "module": module,
                "values": self._schema_config_values(schema).get(module, {}),
            }
        )
