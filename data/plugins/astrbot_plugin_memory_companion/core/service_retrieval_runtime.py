from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .emotion_event_contract import normalize_emotion_event
from .emotion_targeting import memory_emotion_refs
from .models import (
    SessionContext,
    clean_text,
    json_dumps,
)
from .retrieval import RetrievalEngine
from .time_intent import TimeIntent, parse_time_intent


class MemoryCompanionServiceRetrievalRuntime:
    """service retrieval runtime; the facade owns all shared resources."""

    async def _retrieval_cache_key(
        self,
        kind: str,
        query: str,
        ctx: SessionContext,
        top_k: int,
        *,
        admin_read_all: bool = False,
        time_intent: TimeIntent | None = None,
        slot_limits: dict[str, int] | None = None,
        capped_slots: set[str] | None = None,
    ) -> str:
        try:
            revision = await self.store.memory_revision()
        except Exception:
            revision = ""
        if not revision:
            return ""
        time_payload = {}
        if time_intent is not None:
            time_payload = {
                "active": bool(time_intent.active),
                "label": clean_text(time_intent.label, 80),
                "start_at": clean_text(time_intent.start_at, 80),
                "end_at": clean_text(time_intent.end_at, 80),
                "summary_like": bool(time_intent.summary_like),
                "source": clean_text(time_intent.source, 80),
            }
        config_payload = {
            "retrieval_mode": clean_text(self.config.get("retrieval.mode", "auto"), 40),
            "rerank_provider_id": clean_text(
                self.config.get("retrieval.rerank_provider_id", ""), 160
            ),
            "rerank_candidate_multiplier": self.config.int(
                "retrieval.rerank_candidate_multiplier", 5
            ),
            "rerank_candidate_limit": self.config.int(
                "retrieval.rerank_candidate_limit", 32
            ),
            "rerank_timeout_ms": self.config.int("retrieval.rerank_timeout_ms", 1200),
            "latency_budget_ms": self.config.int(
                "retrieval_advanced.recall_latency_budget_ms", 0
            ),
            "query_vector_cache_ttl_seconds": self.config.float(
                "retrieval_advanced.query_vector_cache_ttl_seconds", 0.0
            ),
            "embedding_enabled": self.config.bool("retrieval.embedding_enabled", False),
            "embedding_provider_id": clean_text(
                self.config.get("retrieval.embedding_provider_id", ""), 160
            ),
            "embedding_candidate_limit": self.config.int(
                "retrieval.embedding_candidate_limit", 1200
            ),
            "embedding_top_k": self.config.int("retrieval.embedding_top_k", 32),
            "embedding_score_threshold": self.config.float(
                "retrieval.embedding_score_threshold", 0.34
            ),
            "embedding_weight": self.config.float("retrieval.embedding_weight", 0.55),
            "non_conversation_budget": self.config.int(
                "retrieval_advanced.non_conversation_budget", 2
            ),
            "current_window_candidate_limit": self.config.int(
                "retrieval.current_window_candidate_limit", 600
            ),
            "keyword_fallback_min_fts_candidates": self.config.int(
                "retrieval.keyword_fallback_min_fts_candidates", 80
            ),
            "knowledge_graph_enabled": self.config.bool(
                "knowledge_graph.retrieval_expansion_enabled", True
            ),
            "knowledge_graph_expansion_limit": self.config.int(
                "knowledge_graph.expansion_limit", 12
            ),
            "context_orchestration_enabled": self.config.bool(
                "context_orchestration.enabled", True
            ),
            "allow_self_timeline_everywhere": self.config.bool(
                "visibility.allow_self_timeline_everywhere", True
            ),
            "allow_group_public_in_private": self.config.bool(
                "visibility.allow_group_public_in_private", False
            ),
            "enable_acl_rules": self.config.bool("visibility.enable_acl_rules", True),
            "hide_pending_review": self.config.bool(
                "visibility.hide_pending_review", True
            ),
            "include_raw_events": self.config.bool(
                "memory_injection.include_raw_events", False
            ),
            "private_topology_enabled": self._scope_feature_enabled(
                "private", "topology"
            ),
            "group_topology_enabled": self._scope_feature_enabled("group", "topology"),
        }
        payload = {
            "kind": clean_text(kind, 40),
            "query": clean_text(query, 1400).lower(),
            "top_k": max(1, int(top_k or 1)),
            "admin_read_all": bool(admin_read_all),
            "ctx": {
                "session_id": clean_text(ctx.session_id, 160),
                "scope": clean_text(ctx.scope, 40),
                "platform": clean_text(ctx.platform, 80),
                "user_id": clean_text(ctx.user_id, 120),
                "group_id": clean_text(ctx.group_id, 120),
                "bot_id": clean_text(ctx.bot_id, 120),
                "persona_id": clean_text(ctx.persona_id, 96),
            },
            "time": time_payload,
            "slots": slot_limits or {},
            "capped_slots": sorted(
                clean_text(slot, 60)
                for slot in (capped_slots or set())
                if clean_text(slot, 60)
            ),
            "config": config_payload,
            "revision": revision,
        }
        raw = json_dumps(payload)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _get_retrieval_cache(self, cache_key: str) -> dict[str, Any] | None:
        if not cache_key:
            return None
        cache = self._retrieval_result_cache
        item = cache.get(cache_key)
        now = time.monotonic()
        if not isinstance(item, dict):
            self._retrieval_result_cache_stats["misses"] = (
                self._retrieval_result_cache_stats.get("misses", 0) + 1
            )
            return None
        if now - float(item.get("ts") or 0.0) > self._RETRIEVAL_RESULT_CACHE_TTL:
            cache.pop(cache_key, None)
            self._retrieval_result_cache_stats["misses"] = (
                self._retrieval_result_cache_stats.get("misses", 0) + 1
            )
            return None
        item["last_hit"] = now
        item["hits"] = int(item.get("hits") or 0) + 1
        self._retrieval_result_cache_stats["hits"] = (
            self._retrieval_result_cache_stats.get("hits", 0) + 1
        )
        return deepcopy(item.get("payload") or {})

    def _set_retrieval_cache(self, cache_key: str, payload: dict[str, Any]) -> None:
        if not cache_key or not isinstance(payload, dict):
            return
        cache = self._retrieval_result_cache
        now = time.monotonic()
        cache[cache_key] = {
            "ts": now,
            "last_hit": now,
            "hits": 0,
            "payload": deepcopy(payload),
        }
        if len(cache) <= self._RETRIEVAL_RESULT_CACHE_MAX:
            return
        stale = sorted(
            cache.items(),
            key=lambda pair: (
                int((pair[1] if isinstance(pair[1], dict) else {}).get("hits") or 0),
                float(
                    (pair[1] if isinstance(pair[1], dict) else {}).get("last_hit")
                    or 0.0
                ),
            ),
        )
        remove_count = max(1, len(cache) - self._RETRIEVAL_RESULT_CACHE_MAX)
        for key, _item in stale[:remove_count]:
            cache.pop(key, None)
        self._retrieval_result_cache_stats["evictions"] = (
            self._retrieval_result_cache_stats.get("evictions", 0) + remove_count
        )

    async def _mark_injected_memories(self, memory_ids: list[str]) -> None:
        """Reinforce only records that were present in the final request context."""
        unique_ids: list[str] = []
        for value in memory_ids or []:
            memory_id = clean_text(value, 120)
            if memory_id and memory_id not in unique_ids:
                unique_ids.append(memory_id)
        marker = getattr(self.store, "mark_injected", None)
        if unique_ids and callable(marker):
            await marker(unique_ids)

    async def search(
        self,
        query: str,
        ctx: SessionContext,
        top_k: int = 6,
        *,
        admin_read_all: bool = False,
        time_intent: TimeIntent | None = None,
    ):
        ctx = self._normalized_session_context(ctx)
        if not admin_read_all and not self._scope_feature_enabled(ctx, "recall"):
            self._last_retrieval_path_info = {
                "mode": "disabled",
                "path": "skipped",
                "reason": "scope_recall_disabled",
                "scope": clean_text(ctx.scope, 40),
            }
            return []
        cache_key = await self._retrieval_cache_key(
            "search",
            query,
            ctx,
            top_k,
            admin_read_all=admin_read_all,
            time_intent=time_intent,
        )
        cached = self._get_retrieval_cache(cache_key)
        if isinstance(cached, dict):
            results = deepcopy(cached.get("results") or [])
            engine = self._retrieval_validation_engine(admin_read_all=admin_read_all)
            results = await engine.revalidate_cached_results(results, ctx)
            self._last_retrieval_path_info = dict(cached.get("path_info") or {})
            self._last_retrieval_path_info["cache"] = "hit"
            return results
        engine = await self._retrieval_engine(ctx, admin_read_all=admin_read_all)
        results = await engine.search(query, ctx, top_k, time_intent=time_intent)
        self._last_retrieval_path_info = dict(engine.last_path_info or {})
        self._last_retrieval_path_info["cache"] = "miss"
        self._set_retrieval_cache(
            cache_key,
            {
                "results": deepcopy(results),
                "path_info": dict(self._last_retrieval_path_info),
            },
        )
        return results

    async def search_with_diagnostics(
        self,
        query: str,
        ctx: SessionContext,
        top_k: int = 6,
        *,
        admin_read_all: bool = False,
        time_intent: TimeIntent | None = None,
    ):
        ctx = self._normalized_session_context(ctx)
        if not admin_read_all and not self._scope_feature_enabled(ctx, "recall"):
            self._last_retrieval_path_info = {
                "mode": "disabled",
                "path": "skipped",
                "reason": "scope_recall_disabled",
                "scope": clean_text(ctx.scope, 40),
            }
            return [], []
        cache_key = await self._retrieval_cache_key(
            "diagnostics",
            query,
            ctx,
            top_k,
            admin_read_all=admin_read_all,
            time_intent=time_intent,
        )
        cached = self._get_retrieval_cache(cache_key)
        if isinstance(cached, dict):
            results = deepcopy(cached.get("results") or [])
            engine = self._retrieval_validation_engine(admin_read_all=admin_read_all)
            results = await engine.revalidate_cached_results(results, ctx)
            blocked = deepcopy(cached.get("blocked") or [])
            blocked = await engine.revalidate_cached_diagnostics(blocked, ctx)
            self._last_retrieval_path_info = dict(cached.get("path_info") or {})
            self._last_retrieval_path_info["cache"] = "hit"
            return results, blocked
        engine = await self._retrieval_engine(ctx, admin_read_all=admin_read_all)
        results, blocked = await engine.search_with_diagnostics(
            query, ctx, top_k, time_intent=time_intent
        )
        self._last_retrieval_path_info = dict(engine.last_path_info or {})
        self._last_retrieval_path_info["cache"] = "miss"
        self._set_retrieval_cache(
            cache_key,
            {
                "results": deepcopy(results),
                "blocked": deepcopy(blocked),
                "path_info": dict(self._last_retrieval_path_info),
            },
        )
        return results, blocked

    async def search_context_slots(
        self,
        query: str,
        ctx: SessionContext,
        top_k: int = 6,
        *,
        admin_read_all: bool = False,
        time_intent: TimeIntent | None = None,
    ):
        ctx = self._normalized_session_context(ctx)
        if not admin_read_all and not self._scope_feature_enabled(ctx, "recall"):
            self._last_retrieval_path_info = {
                "mode": "disabled",
                "path": "skipped",
                "reason": "scope_recall_disabled",
                "scope": clean_text(ctx.scope, 40),
            }
            return [], [], {}
        slot_query = clean_text(query, 1000) or clean_text(ctx.message_text, 1000)
        slot_limits = self._slot_limits(
            top_k, query=slot_query, time_intent=time_intent
        )
        capped_slots = self._slot_capped_slots(
            slot_query,
            time_intent=time_intent,
            bot_id=ctx.bot_id,
        )
        cache_key = await self._retrieval_cache_key(
            "slots",
            query,
            ctx,
            top_k,
            admin_read_all=admin_read_all,
            time_intent=time_intent,
            slot_limits=slot_limits,
            capped_slots=capped_slots,
        )
        cached = self._get_retrieval_cache(cache_key)
        if isinstance(cached, dict):
            results = deepcopy(cached.get("results") or [])
            engine = self._retrieval_validation_engine(admin_read_all=admin_read_all)
            results = await engine.revalidate_cached_results(results, ctx)
            blocked = deepcopy(cached.get("blocked") or [])
            blocked = await engine.revalidate_cached_diagnostics(blocked, ctx)
            slot_map = deepcopy(cached.get("slot_map") or {})
            valid_by_id = {
                clean_text(getattr(item.memory, "id", ""), 120): item
                for item in results
                if clean_text(getattr(item.memory, "id", ""), 120)
            }
            slot_map = {
                slot: [
                    valid_by_id[memory_id]
                    for cached_item in items
                    if (
                        memory_id := clean_text(
                            getattr(getattr(cached_item, "memory", None), "id", ""), 120
                        )
                    )
                    in valid_by_id
                ]
                for slot, items in slot_map.items()
                if isinstance(items, list)
            }
            slot_map = {slot: items for slot, items in slot_map.items() if items}
            self._last_retrieval_path_info = dict(cached.get("path_info") or {})
            self._last_retrieval_path_info["cache"] = "hit"
            return results, blocked, slot_map
        engine = await self._retrieval_engine(ctx, admin_read_all=admin_read_all)
        if not self.config.bool("context_orchestration.enabled", True):
            results, blocked = await engine.search_with_diagnostics(
                query, ctx, top_k, time_intent=time_intent
            )
            self._last_retrieval_path_info = dict(engine.last_path_info or {})
            self._last_retrieval_path_info["cache"] = "miss"
            slot_map = {"stable_memory": results}
            self._set_retrieval_cache(
                cache_key,
                {
                    "results": deepcopy(results),
                    "blocked": deepcopy(blocked),
                    "slot_map": deepcopy(slot_map),
                    "path_info": dict(self._last_retrieval_path_info),
                },
            )
            return results, blocked, slot_map
        results, blocked, slot_map = await engine.search_by_slots(
            query,
            ctx,
            slot_limits=slot_limits,
            total_limit=top_k,
            time_intent=time_intent,
            capped_slots=capped_slots,
        )
        self._last_retrieval_path_info = dict(engine.last_path_info or {})
        self._last_retrieval_path_info["cache"] = "miss"
        self._set_retrieval_cache(
            cache_key,
            {
                "results": deepcopy(results),
                "blocked": deepcopy(blocked),
                "slot_map": deepcopy(slot_map),
                "path_info": dict(self._last_retrieval_path_info),
            },
        )
        return results, blocked, slot_map

    def _retrieval_validation_engine(
        self, *, admin_read_all: bool = False
    ) -> RetrievalEngine:
        return RetrievalEngine(
            self.store,
            self.visibility_policy(admin_read_all=admin_read_all),
            retrieval_mode="basic",
            embedding_enabled=False,
            knowledge_graph_enabled=False,
            private_topology_enabled=self._scope_feature_enabled("private", "topology"),
            group_topology_enabled=self._scope_feature_enabled("group", "topology"),
        )

    async def _retrieval_engine(
        self, ctx: SessionContext, *, admin_read_all: bool = False
    ) -> RetrievalEngine:
        mode = clean_text(self.config.get("retrieval.mode", "auto"), 40).lower()
        if mode not in {"auto", "basic", "rerank"}:
            mode = "auto"
        provider = None
        provider_id = ""
        if mode != "basic":
            provider, provider_id = await self._resolve_rerank_provider(ctx, mode=mode)
        embedding_enabled = self.config.bool("retrieval.embedding_enabled", False)
        embedding_provider = None
        embedding_provider_id = clean_text(
            self.config.get("retrieval.embedding_provider_id", ""), 160
        )
        if embedding_enabled:
            (
                embedding_provider,
                embedding_provider_id,
            ) = await self._resolve_embedding_provider(ctx)
            if embedding_provider is not None and embedding_provider_id:
                self._schedule_embedding_backfill(
                    embedding_provider, embedding_provider_id
                )
        return RetrievalEngine(
            self.store,
            self.visibility_policy(admin_read_all=admin_read_all),
            retrieval_mode=mode,
            rerank_provider=provider,
            rerank_provider_id=provider_id,
            rerank_candidate_multiplier=self.config.int(
                "retrieval.rerank_candidate_multiplier", 5
            ),
            rerank_candidate_limit=self.config.int(
                "retrieval.rerank_candidate_limit", 32
            ),
            rerank_timeout_ms=self.config.int("retrieval.rerank_timeout_ms", 1200),
            latency_budget_ms=self.config.int(
                "retrieval_advanced.recall_latency_budget_ms", 0
            ),
            query_vector_cache_ttl_seconds=self.config.float(
                "retrieval_advanced.query_vector_cache_ttl_seconds", 0.0
            ),
            embedding_provider=embedding_provider,
            embedding_provider_id=embedding_provider_id,
            embedding_enabled=embedding_enabled,
            embedding_candidate_limit=self.config.int(
                "retrieval.embedding_candidate_limit", 1200
            ),
            embedding_top_k=self.config.int("retrieval.embedding_top_k", 32),
            embedding_score_threshold=self.config.float(
                "retrieval.embedding_score_threshold", 0.34
            ),
            embedding_weight=self.config.float("retrieval.embedding_weight", 0.55),
            embedding_timeout_ms=self._embedding_timeout_ms(),
            embedding_max_text_chars=self.config.int(
                "retrieval.embedding_max_text_chars", 1200
            ),
            current_window_candidate_limit=self.config.int(
                "retrieval.current_window_candidate_limit", 600
            ),
            keyword_fallback_min_fts_candidates=self.config.int(
                "retrieval.keyword_fallback_min_fts_candidates", 80
            ),
            knowledge_graph_enabled=self.config.bool(
                "knowledge_graph.retrieval_expansion_enabled", True
            ),
            knowledge_graph_expansion_limit=self.config.int(
                "knowledge_graph.expansion_limit", 12
            ),
            private_topology_enabled=self._scope_feature_enabled("private", "topology"),
            group_topology_enabled=self._scope_feature_enabled("group", "topology"),
            usage_recorder=self._record_token_usage,
            relax_keyword_min_hits=self.config.bool(
                "retrieval_advanced.relax_keyword_min_hits", False
            ),
            proactive_message_score_penalty=self.config.float(
                "retrieval_advanced.proactive_message_score_penalty", 1.0
            ),
            dedupe_content_ratio=self.config.float(
                "retrieval_advanced.dedupe_content_ratio", 0.0
            ),
            non_conversation_budget=self.config.int(
                "retrieval_advanced.non_conversation_budget", 2
            ),
        )

    async def search_for_event(self, event: Any, query: str, top_k: int = 6):
        ctx = await self.identity.resolve_event_context(event)
        time_intent = parse_time_intent(query or ctx.message_text)
        retrieval_query = self._query_for_time_intent(query, time_intent)
        return await self.search(
            retrieval_query,
            ctx,
            top_k,
            time_intent=time_intent if time_intent.active else None,
        )

    async def explain_for_event(self, event: Any, query: str, top_k: int = 6):
        ctx = await self.identity.resolve_event_context(event)
        time_intent = parse_time_intent(query or ctx.message_text)
        retrieval_query = self._query_for_time_intent(query, time_intent)
        return await self.search_with_diagnostics(
            retrieval_query,
            ctx,
            top_k,
            time_intent=time_intent if time_intent.active else None,
        )

    async def explain_context_for_event(self, event: Any, query: str, top_k: int = 6):
        ctx = await self.identity.resolve_event_context(event)
        time_intent = parse_time_intent(query or ctx.message_text)
        intent = self.intent_builder.build(
            ctx,
            event=event,
            explicit_query=query,
            query_mode=str(
                self.config.get("context_orchestration.query_mode", "") or ""
            ),
        )
        retrieval_query = self._query_for_time_intent(intent.query, time_intent)
        selected, blocked, slot_map = await self.search_context_slots(
            retrieval_query,
            ctx,
            top_k,
            time_intent=time_intent if time_intent.active else None,
        )
        await self._add_time_window_timeline_slot(ctx, slot_map, time_intent)
        if time_intent.active:
            selected = self._flatten_slot_map(slot_map)
        return intent, selected, blocked, slot_map

    async def _detect_and_queue_emotional_events(
        self,
        ctx: SessionContext,
        results: list[Any],
        *,
        companion_bot_mood: str = "",
        companion_bot_energy: float = 0.0,
        emotional_tone: str = "neutral",
    ) -> None:
        """Detect emotional signals from recalled memories and queue drift events for the companion plugin."""
        if not results:
            return
        now = time.time()
        events: list[dict[str, Any]] = []
        for item in results:
            memory = getattr(item, "memory", None)
            if memory is None:
                continue
            metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
            try:
                scar_w = float(metadata.get("scar_weight") or 0.0)
                emotional_w = float(metadata.get("emotional_weight") or 0.0)
                relationship_w = float(metadata.get("relationship_weight") or 0.0)
                vulnerability_w = float(metadata.get("vulnerability_weight") or 0.0)
            except Exception:
                scar_w = emotional_w = relationship_w = vulnerability_w = 0.0
            event_type = ""
            energy_delta = 0.0
            mood_hint = ""
            if scar_w >= 0.55:
                event_type = "scar_touched"
                energy_delta = -min(8.0, scar_w * 10.0)
                mood_hint = "低落"
            elif emotional_w >= 0.50 and relationship_w >= 0.40:
                event_type = "warm_memory"
                energy_delta = min(5.0, emotional_w * 6.0)
                mood_hint = "微暖"
            elif vulnerability_w >= 0.50 and emotional_tone in (
                "vulnerable",
                "warm",
                "nostalgic",
            ):
                event_type = "vulnerable_resonance"
                energy_delta = -min(4.0, vulnerability_w * 5.0)
                mood_hint = "柔软"
            if not event_type:
                continue
            refs = memory_emotion_refs(memory, ctx)
            if refs is None:
                continue
            # Mood resonance check: if Bot is already in a contrasting mood, dampen the drift
            if companion_bot_mood:
                mood_lower = companion_bot_mood.strip().lower()
                if event_type == "warm_memory" and any(
                    kw in mood_lower for kw in ("难过", "伤心", "累", "疲惫")
                ):
                    energy_delta *= 0.5
                if event_type == "scar_touched" and any(
                    kw in mood_lower for kw in ("开心", "愉快", "兴奋")
                ):
                    energy_delta *= 0.6
            event_key = clean_text(ctx.message_id, 160) or str(int(now))
            event_id = hashlib.sha1(
                f"{ctx.session_id}|{event_key}|{memory.id}|{event_type}".encode(
                    "utf-8", errors="ignore"
                )
            ).hexdigest()[:20]
            event = normalize_emotion_event(
                {
                    "event_id": f"emo_{event_id}",
                    "trace_id": f"etr_{event_id}",
                    "producer_plugin": "memory_companion",
                    "origin_kind": "memory_recall",
                    "platform": ctx.platform,
                    "bot_id": ctx.bot_id,
                    "scope": ctx.scope,
                    "session_id": ctx.session_id,
                    **refs,
                    "event_type": event_type,
                    "intensity": max(scar_w, emotional_w, vulnerability_w) * 100.0,
                    "confidence": max(0.5, min(1.0, getattr(item, "score", 0.5))),
                    "valence_hint": 0.35
                    if event_type == "warm_memory"
                    else -0.45
                    if event_type == "scar_touched"
                    else -0.1,
                    "arousal_hint": min(1.0, max(scar_w, emotional_w)),
                    "vulnerability_hint": max(
                        vulnerability_w,
                        0.6 if event_type == "vulnerable_resonance" else 0.0,
                    ),
                    "source_rule": event_type,
                    "occurred_at": datetime.fromtimestamp(
                        now, tz=timezone.utc
                    ).isoformat(timespec="seconds"),
                    "dedupe_key": f"{ctx.session_id}|{event_key}|{memory.id}|{event_type}",
                    "payload_hash": hashlib.sha256(
                        memory.id.encode("utf-8", errors="ignore")
                    ).hexdigest(),
                    "privacy_level": "redacted",
                    "applied_energy_delta": round(energy_delta, 2),
                    "status": "observed",
                    "reason_codes": [event_type, "memory_recall_resonance"],
                },
                producer_plugin="memory_companion",
            )
            event.update(
                {
                    "id": event["event_id"],
                    "ts": now,
                    "memory_id": memory.id,
                    "energy_delta": round(energy_delta, 2),
                    "mood_hint": mood_hint,
                    "scar_weight": round(scar_w, 3),
                    "emotional_weight": round(emotional_w, 3),
                }
            )
            events.append(event)
        if not events:
            return
        self._cleanup_emotional_event_queue(now=now)
        queue = self._emotional_event_queue.setdefault(ctx.session_id, [])
        existing_ids = {
            clean_text(item.get("id"), 80) for item in queue if isinstance(item, dict)
        }
        fresh_events = [
            event
            for event in events
            if clean_text(event.get("id"), 80) not in existing_ids
        ]
        queue.extend(fresh_events)
        for event in fresh_events:
            await self.store.upsert_emotion_event(event)
        # Trim old events
        queue[:] = [
            e for e in queue if (now - e.get("ts", 0)) < self._EMOTIONAL_EVENT_TTL
        ]
        if len(queue) > self._EMOTIONAL_EVENT_MAX_PER_SESSION:
            queue[:] = queue[-self._EMOTIONAL_EVENT_MAX_PER_SESSION :]

    def _cleanup_emotional_event_queue(self, *, now: float | None = None) -> None:
        """Drop expired session buckets so long-lived processes do not retain old keys."""

        current = time.time() if now is None else float(now)
        if now is None and current - self._emotional_event_last_cleanup < 60.0:
            return
        self._emotional_event_last_cleanup = current
        for session_id, queue in list(self._emotional_event_queue.items()):
            if not isinstance(queue, list):
                self._emotional_event_queue.pop(session_id, None)
                continue
            fresh: list[dict[str, Any]] = []
            for item in queue:
                if not isinstance(item, dict):
                    continue
                try:
                    age = current - float(item.get("ts") or 0.0)
                except (TypeError, ValueError):
                    continue
                if 0.0 <= age < self._EMOTIONAL_EVENT_TTL:
                    fresh.append(item)
            if fresh:
                self._emotional_event_queue[session_id] = fresh[
                    -self._EMOTIONAL_EVENT_MAX_PER_SESSION :
                ]
            elif session_id in self._emotional_event_queue:
                self._emotional_event_queue.pop(session_id, None)

    @staticmethod
    def _same_private_emotion_domain(event: Any, ctx: SessionContext) -> bool:
        if not isinstance(event, dict):
            return False
        actor = (
            event.get("actor_ref") if isinstance(event.get("actor_ref"), dict) else {}
        )
        target = (
            event.get("target_ref") if isinstance(event.get("target_ref"), dict) else {}
        )
        bot_id = clean_text(ctx.bot_id, 160)
        platform = clean_text(ctx.platform, 80)
        user_id = clean_text(ctx.user_id, 160)
        return (
            bool(bot_id and platform and user_id)
            and clean_text(ctx.scope, 24).lower() == "private"
            and clean_text(event.get("bot_id"), 160) == bot_id
            and clean_text(event.get("scope"), 24).lower() == "private"
            and clean_text(event.get("platform"), 80) == platform
            and clean_text(actor.get("kind"), 24) == "user"
            and clean_text(actor.get("id"), 160) == user_id
            and clean_text(target.get("kind"), 24) == "bot"
            and clean_text(target.get("id"), 160) == bot_id
        )

    def _get_cross_window_emotional_hint(self, ctx: SessionContext) -> str:
        """Generate a subtle hint about emotional residue from other chat windows.

        This creates cross-window emotional continuity: if the bot recently touched
        scar or warm memories in another session, a faint residue influences the
        current session's atmosphere without leaking private content.
        """
        if not self.config.bool(
            "private_companion_bridge.cross_window_emotional_continuity_enabled", False
        ):
            return ""
        now = time.time()
        current_session = clean_text(ctx.session_id, 220)
        if not current_session:
            return ""
        # Collect recent events from OTHER sessions (within last 30 minutes)
        recent_window = 1800.0  # 30 minutes
        other_events: list[dict[str, Any]] = []
        for session_id, queue in self._emotional_event_queue.items():
            if session_id == current_session:
                continue
            for event in queue:
                try:
                    age = now - float(event.get("ts") or 0.0)
                except (TypeError, ValueError):
                    continue
                if 0.0 <= age < recent_window and self._same_private_emotion_domain(
                    event, ctx
                ):
                    other_events.append(event)
        if not other_events:
            return ""
        # Categorize
        scar_count = sum(
            1 for e in other_events if e.get("event_type") == "scar_touched"
        )
        warm_count = sum(
            1 for e in other_events if e.get("event_type") == "warm_memory"
        )
        vulnerable_count = sum(
            1 for e in other_events if e.get("event_type") == "vulnerable_resonance"
        )
        # Build hint without leaking content
        hints: list[str] = []
        if scar_count >= 2:
            hints.append(
                "你刚刚在别处想起了一些沉重的事，此刻心里还有点闷；这不会影响你对当前对话的回应，但语气可能会不自觉柔和一些。"
            )
        elif scar_count == 1:
            hints.append(
                "你刚才在另一个对话里触碰到了一点旧事，心里还有一丝余波；自然回应就好，不需要刻意掩饰。"
            )
        if warm_count >= 2:
            hints.append(
                "你最近在别的对话里感受到了不少温暖，此刻心情底色偏暖；可以更自然地用这份温度回应当前的人。"
            )
        elif warm_count == 1:
            hints.append(
                "你刚才在别处想起了一些温暖的事，心里还带着一点柔软；这会让你的回应更自然地带着温度。"
            )
        if vulnerable_count >= 1:
            hints.append(
                "你刚刚在另一个窗口感受到了一些脆弱共鸣，此刻对对方的情绪可能更敏感一些。"
            )
        if not hints:
            return ""
        return " ".join(hints)

    def _get_cross_window_emotional_state(
        self,
        *,
        exclude_session_id: str = "",
        window_seconds: float = 1800.0,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Deprecated unscoped aggregate; callers must use a delivery context."""
        _ = (exclude_session_id, window_seconds, limit)
        return {
            "enabled": False,
            "state": "migration_required",
            "error_code": "delivery_context_required",
            "total": 0,
            "scar_count": 0,
            "warm_count": 0,
            "vulnerable_count": 0,
        }
