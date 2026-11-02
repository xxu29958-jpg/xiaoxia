from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from .memory_lifecycle import apply_lifecycle_score, evaluate_memory_lifecycle
from .models import (
    MemoryRecord,
    SessionContext,
    clean_text,
)


class RetrievalEngineScoring:
    """Scoring; shared runtime resources belong to the facade."""

    def _term_document_stats(
        self, memories: list[MemoryRecord], terms: list[str]
    ) -> dict[str, float]:
        if not terms:
            return {}
        document_count = max(1, len(memories))
        dfs = dict.fromkeys(terms, 0)
        for memory in memories:
            haystack = self._haystack(memory)
            for term in terms:
                if term in haystack:
                    dfs[term] += 1
        return {
            term: math.log(1 + (document_count - df + 0.5) / (df + 0.5))
            for term, df in dfs.items()
            if df > 0
        }

    def _cached_haystack(self, cache_key: tuple[str, str]) -> str | None:
        shared = self._shared_haystack_cache
        if shared is not None:
            return shared.get(cache_key)
        with self._haystack_cache_lock:
            return self._haystack_cache.get(cache_key)

    def _cache_haystack(self, cache_key: tuple[str, str], haystack: str) -> None:
        shared = self._shared_haystack_cache
        if shared is not None:
            shared.put(cache_key, haystack)
            return
        with self._haystack_cache_lock:
            if len(self._haystack_cache) >= 4096:
                self._haystack_cache.clear()
            self._haystack_cache[cache_key] = haystack

    def _haystack(self, memory: MemoryRecord) -> str:
        cache_key = (memory.id, memory.updated_at)
        cached = self._cached_haystack(cache_key)
        if cached is not None:
            return cached
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        metadata_text_parts = [
            metadata.get("canonical_summary", ""),
            metadata.get("persona_summary", ""),
            metadata.get("profile_dimension", ""),
            metadata.get("profile_value", ""),
            metadata.get("normalized_value", ""),
            " ".join(str(item) for item in metadata.get("key_facts", []) if item)
            if isinstance(metadata.get("key_facts"), list)
            else "",
            " ".join(str(item) for item in metadata.get("topics", []) if item)
            if isinstance(metadata.get("topics"), list)
            else "",
            " ".join(str(item) for item in metadata.get("participants", []) if item)
            if isinstance(metadata.get("participants"), list)
            else "",
        ]
        haystack = " ".join(
            [
                memory.content,
                memory.evidence,
                " ".join(memory.tags),
                " ".join(
                    clean_text(part, 1000)
                    for part in metadata_text_parts
                    if clean_text(part, 1000)
                ),
                memory.subject.name,
                memory.subject.id,
                memory.object.name,
                memory.object.id,
                memory.group_id,
            ]
        ).lower()
        self._cache_haystack(cache_key, haystack)
        return haystack

    @staticmethod
    def _route_families(sources: set[str]) -> frozenset[str]:
        families: set[str] = set()
        if sources & {"fts", "keyword"}:
            families.add("lexical")
        if "vector" in sources:
            families.add("vector")
        if "time_window" in sources:
            families.add("time")
        return frozenset(families)

    @staticmethod
    def _route_agreement_bonus(route_families: frozenset[str]) -> float:
        # Agreement between independent recall channels (lexical + vector,
        # optionally time) is stronger evidence than any single channel.
        if len(route_families) < 2:
            return 0.0
        return min(0.12, 0.08 * (len(route_families) - 1))

    def _score(
        self,
        memory: MemoryRecord,
        terms: list[str],
        ctx: SessionContext,
        profile: dict[str, object],
        term_stats: dict[str, float],
        *,
        vector_score: float = 0.0,
        reciprocal_rank_score: float = 0.0,
        route_families: frozenset[str] = frozenset(),
    ) -> tuple[float, str]:
        haystack = self._haystack(memory)
        compact_haystack = re.sub(r"\s+", "", haystack)
        term_hits = sum(1 for term in terms if term and term in haystack)
        exact_phrases = [
            str(item) for item in profile.get("exact_phrases", []) if str(item)
        ]
        exact_hit = any(phrase in compact_haystack for phrase in exact_phrases)
        profile_hit = self._profile_query_matches_memory(
            str(profile.get("query_text") or " ".join(terms)),
            memory,
        )
        min_hits = int(profile.get("min_hits", 1) or 1)
        vector_relevant = (
            vector_score >= self.embedding_score_threshold
            if self.embedding_enabled
            else False
        )
        if (
            terms
            and not exact_hit
            and not profile_hit
            and term_hits < min_hits
            and not vector_relevant
        ):
            return 0.0, f"keyword_hit_too_weak hits={term_hits}/{min_hits}"
        graph_guard_reason = self._livingmemory_graph_relevance_guard(
            memory, term_hits, exact_hit, min_hits
        )
        if graph_guard_reason and vector_score < min(
            0.98, self.embedding_score_threshold + 0.12
        ):
            return 0.0, graph_guard_reason
        bm25 = 0.0
        if terms:
            for term in terms:
                if not term:
                    continue
                freq = haystack.count(term)
                if freq <= 0:
                    continue
                idf = term_stats.get(term, 0.0)
                bm25 += idf * ((freq * 2.2) / (freq + 1.2))
            lexical = (0.42 if exact_hit or profile_hit else 0.24) + min(
                0.72, bm25 * 0.18
            )
        else:
            lexical = 0.0

        scope_bonus = 0.0
        if memory.session_id and memory.session_id == ctx.session_id:
            scope_bonus += 0.25
        if ctx.user_id and ctx.user_id in {memory.subject.id, memory.object.id}:
            scope_bonus += 0.15
        if ctx.group_id and ctx.group_id == memory.group_id:
            scope_bonus += 0.15
        if memory.visibility == "bot_self":
            scope_bonus += 0.08

        age_bonus = self._recency_bonus(memory.occurred_at or memory.created_at)
        persona_bonus = self._persona_relevance_bonus(memory, profile)
        dynamics_bonus = self._persona_dynamics_bonus(memory, profile)
        route_bonus = self._route_agreement_bonus(route_families)
        vector_bonus = 0.0
        if vector_relevant:
            if self.embedding_score_threshold < 1.0:
                normalized_vector = (vector_score - self.embedding_score_threshold) / (
                    1.0 - self.embedding_score_threshold
                )
            else:
                normalized_vector = vector_score
            vector_bonus = self.embedding_weight * max(0.0, min(1.0, normalized_vector))
        rrf_bonus = min(0.55, max(0.0, float(reciprocal_rank_score)) * 12.0)
        # Importance should not let unrelated high-priority rows crowd out
        # query-supported memories.  Strong lexical/profile evidence keeps
        # the historical weight; weak single-channel candidates are damped.
        strong_query_evidence = (
            exact_hit or profile_hit or (vector_relevant and term_hits >= min_hits)
        )
        importance_weight = 0.55 if strong_query_evidence else 0.28
        score = (
            lexical
            + scope_bonus
            + memory.importance * importance_weight
            + memory.confidence * 0.25
            + age_bonus
            + vector_bonus
            + persona_bonus
            + dynamics_bonus
            + route_bonus
            + rrf_bonus
        )
        if not terms:
            score = (
                scope_bonus
                + memory.importance * 0.8
                + age_bonus
                + vector_bonus
                + persona_bonus
                + dynamics_bonus
                + route_bonus
                + rrf_bonus
            )
        lifecycle = evaluate_memory_lifecycle(memory, ctx)
        score = apply_lifecycle_score(score, lifecycle)
        # relax：proactive_message（主动消息原文）类记忆评分降权（默认 1.0 不降权，行为与历史版本一致）。
        if (
            self.proactive_message_score_penalty < 1.0
            and clean_text(memory.memory_type, 120).lower() == "proactive_message"
        ):
            score = score * self.proactive_message_score_penalty
        return score, (
            f"hits={term_hits};exact={int(exact_hit)};profile={int(profile_hit)};bm25={bm25:.2f};"
            f"vector={vector_score:.3f};importance={memory.importance:.2f};"
            f"rrf={reciprocal_rank_score:.4f};persona={persona_bonus:.2f};"
            f"dynamics={dynamics_bonus:.2f};recency={age_bonus:.2f};"
            f"decay={lifecycle.decay_factor:.3f};salience={lifecycle.salience:.2f};"
            f"reinforcement={lifecycle.reinforcement:.2f};{lifecycle.reason}"
            f"routes={'+'.join(sorted(route_families)) or '-'};route_bonus={route_bonus:.2f}"
        )

    @staticmethod
    def _persona_relevance_bonus(
        memory: MemoryRecord, profile: dict[str, object]
    ) -> float:
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}

        def weight(key: str) -> float:
            try:
                return max(0.0, min(1.0, float(metadata.get(key) or 0.0)))
            except Exception:
                return 0.0

        persona = weight("persona_importance")
        if persona <= 0:
            return 0.0
        bonus = min(0.10, persona * 0.08)
        contextual = bool(profile.get("contextual_recall"))
        temporal = bool(profile.get("temporal_aggregate"))
        if contextual:
            bonus += min(
                0.10,
                max(
                    weight("open_loop_weight"),
                    weight("promise_weight"),
                    weight("emotional_debt_weight"),
                )
                * 0.10,
            )
            bonus += min(0.06, weight("relationship_weight") * 0.06)
        if temporal:
            bonus += min(
                0.06,
                max(weight("emotional_weight"), weight("self_continuity_weight"))
                * 0.06,
            )
        return min(0.22, bonus)

    @staticmethod
    def _persona_dynamics_bonus(
        memory: MemoryRecord, profile: dict[str, object]
    ) -> float:
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}

        def weight(key: str) -> float:
            try:
                return max(0.0, min(1.0, float(metadata.get(key) or 0.0)))
            except Exception:
                return 0.0

        freshness = weight("freshness_weight")
        scar = weight("scar_weight")
        open_loop = weight("open_loop_weight")
        promise = weight("promise_weight")
        emotional_debt = weight("emotional_debt_weight")
        creative = weight("creative_weight")
        relationship = weight("relationship_weight")
        emotional = weight("emotional_weight")
        decay_mode = clean_text(metadata.get("decay_mode"), 60)
        phase = clean_text(metadata.get("relationship_phase"), 60)

        bonus = min(0.055, freshness * 0.055)
        durable = max(scar, promise, open_loop, emotional_debt, creative)
        if decay_mode in {"no_decay", "scar_slow_decay", "creative_milestone"}:
            bonus += min(0.075, durable * 0.075)
        elif decay_mode == "slow_decay":
            bonus += min(0.045, max(relationship, emotional) * 0.045)
        if phase in {"conflict", "repair", "comfort"}:
            bonus += min(0.045, max(scar, emotional, relationship) * 0.045)

        contextual = bool(profile.get("contextual_recall"))
        temporal = bool(profile.get("temporal_aggregate"))
        open_loop_followup = bool(profile.get("open_loop_followup"))
        if contextual:
            bonus += min(0.055, max(open_loop, promise, emotional_debt, scar) * 0.055)
        if open_loop_followup:
            bonus += min(0.11, max(open_loop, promise, emotional_debt, scar) * 0.11)
        if temporal:
            bonus += min(0.040, max(freshness, emotional) * 0.040)
        return min(0.18, bonus)

    def _livingmemory_graph_relevance_guard(
        self,
        memory: MemoryRecord,
        term_hits: int,
        exact_hit: bool,
        min_hits: int,
    ) -> str:
        if not self._is_livingmemory_graph_memory(memory) or exact_hit:
            return ""
        required_hits = max(3, min_hits + 1)
        age_days = self._age_days(memory.occurred_at or memory.created_at)
        if age_days is not None and age_days >= 30:
            required_hits = max(required_hits, 4)
        if term_hits < required_hits:
            return (
                f"livingmemory_graph_hit_too_weak hits={term_hits}/{required_hits};age_days={age_days:.1f}"
                if age_days is not None
                else f"livingmemory_graph_hit_too_weak hits={term_hits}/{required_hits}"
            )
        return ""

    def _is_livingmemory_graph_memory(self, memory: MemoryRecord) -> bool:
        return clean_text(
            memory.source_plugin, 80
        ).lower() == "livingmemory" and clean_text(
            memory.memory_type, 120
        ).lower().startswith("livingmemory_graph:")

    def _recency_bonus(self, iso_text: str) -> float:
        days = self._age_days(iso_text)
        if days is None:
            return 0.0
        return 0.2 * math.exp(-days / 14.0)

    def _age_days(self, iso_text: str) -> float | None:
        if not iso_text:
            return None
        try:
            dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
