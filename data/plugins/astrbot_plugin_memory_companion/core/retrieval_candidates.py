from __future__ import annotations

import asyncio
import time
from typing import Any

from .memory_lifecycle import evaluate_memory_lifecycle
from .models import (
    MemoryRecord,
    SearchResult,
    SessionContext,
    clean_text,
)
from .profile_quality import PROFILE_MEMORY_TYPES
from .time_intent import TimeIntent


class RetrievalEngineCandidates:
    """Candidates; shared runtime resources belong to the facade."""

    async def _rank_candidates(
        self,
        query: str,
        ctx: SessionContext,
        *,
        time_intent: TimeIntent | None = None,
    ) -> tuple[list[SearchResult], list[dict[str, str]]]:
        query = clean_text(query, 1000)
        terms = self._terms(query)
        graph_terms = []
        if self.knowledge_graph_enabled and self.knowledge_graph_expansion_limit > 0:
            graph_labels = await self.store.related_knowledge_terms(
                terms,
                scope=ctx.scope,
                session_id=ctx.session_id,
                group_id=ctx.group_id,
                limit=self.knowledge_graph_expansion_limit,
            )
            graph_terms = self._knowledge_graph_anchor_terms(
                graph_labels,
                seed_terms=terms,
                limit=self.knowledge_graph_expansion_limit,
            )
        expanded_terms = self._merge_terms(terms, graph_terms)
        include_pending = not self.policy.hide_pending_review
        keyword_terms = self._keyword_candidate_terms(query, expanded_terms)

        # Candidate sources are read in one bundled pass over the store's
        # dedicated read-only connection instead of four separate gathered
        # store calls that each re-acquired the single write connection lock
        # (optimization_plan.md §6.1/§6.2). The embedding provider wait still
        # overlaps the store reads via gather. Any bundle failure falls back
        # to the legacy per-source reads with identical semantics.
        load_started = time.perf_counter()
        time_window_spec: tuple[str, str, int] | None = None
        if time_intent is not None and time_intent.active:
            time_window_spec = (time_intent.start_at, time_intent.end_at, 1600)
        bundle: dict[str, Any] | None = None
        try:
            bundle, vector_result = await asyncio.gather(
                self.store.list_retrieval_candidate_bundle(
                    materialize_limit=self.DEFAULT_MATERIALIZE_LIMIT,
                    current_window={
                        "scope": ctx.scope,
                        "session_id": ctx.session_id,
                        "user_id": ctx.user_id,
                        "group_id": ctx.group_id,
                        "limit": self.current_window_candidate_limit,
                    },
                    fts_terms=keyword_terms,
                    fts_limit=1200,
                    keyword_terms=keyword_terms,
                    keyword_limit=1200,
                    keyword_fallback_min_fts=self.keyword_fallback_min_fts_candidates,
                    time_window=time_window_spec,
                    include_pending=include_pending,
                ),
                self._embedding_candidate_memories(
                    query,
                    ctx,
                    include_pending=include_pending,
                    time_intent=time_intent,
                ),
            )
        except Exception:
            bundle = None
            vector_result = await self._embedding_candidate_memories(
                query,
                ctx,
                include_pending=include_pending,
                time_intent=time_intent,
            )
        if bundle is not None:
            bundle_timing = bundle.pop("_timing", None) or {}
            ranked_candidates = bundle["ranked_candidates"]
            current_window_candidates = bundle["current_window_candidates"]
            fts_candidates = bundle["fts_candidates"]
            keyword_candidates = bundle["keyword_candidates"]
            time_window_candidates = bundle["time_window_candidates"]
            use_keyword_fallback = bool(bundle["keyword_fallback_used"])
        else:
            bundle_timing = None
            time_window_task = None
            if time_window_spec is not None:
                time_window_task = self.store.list_time_window_candidate_memories(
                    time_window_spec[0],
                    time_window_spec[1],
                    limit=time_window_spec[2],
                    include_pending=include_pending,
                )
            (
                ranked_candidates,
                current_window_candidates,
                fts_candidates,
                time_window_candidates,
            ) = await asyncio.gather(
                self.store.list_candidate_memories(
                    limit=self.DEFAULT_MATERIALIZE_LIMIT,
                    include_pending=include_pending,
                ),
                self.store.list_current_window_candidate_memories(
                    scope=ctx.scope,
                    session_id=ctx.session_id,
                    user_id=ctx.user_id,
                    group_id=ctx.group_id,
                    limit=self.current_window_candidate_limit,
                    include_pending=include_pending,
                ),
                self.store.list_fts_candidate_memories(
                    keyword_terms,
                    limit=1200,
                    include_pending=include_pending,
                ),
                time_window_task
                if time_window_task is not None
                else asyncio.sleep(0, result=[]),
            )
            use_keyword_fallback = (
                len(fts_candidates) < self.keyword_fallback_min_fts_candidates
            )
            keyword_candidates = (
                await self.store.list_keyword_candidate_memories(
                    keyword_terms,
                    limit=1200,
                    include_pending=include_pending,
                )
                if use_keyword_fallback
                else []
            )
        vector_candidates, vector_scores, embedding_info = vector_result
        self._rank_path_info = embedding_info
        self._rank_path_info["candidate_load_ms"] = int(
            (time.perf_counter() - load_started) * 1000
        )
        self._rank_path_info["candidate_bundle"] = bundle is not None
        if bundle_timing:
            self._rank_path_info["bundle_lock_wait_ms"] = bundle_timing.get(
                "lock_wait_ms", 0
            )
            self._rank_path_info["bundle_queries_ms"] = bundle_timing.get(
                "queries_ms", 0
            )
            self._rank_path_info["bundle_parse_ms"] = bundle_timing.get("parse_ms", 0)
            self._rank_path_info["bundle_inner_ms"] = bundle_timing.get(
                "inner_total_ms", 0
            )
        self._rank_path_info.update(
            {
                "current_window_candidates": len(current_window_candidates),
                "fts_candidates": len(fts_candidates),
                "keyword_fallback_used": use_keyword_fallback,
                "keyword_candidates": len(keyword_candidates),
            }
        )
        acl_state = (
            await self._acl_state()
            if self.policy.enable_acl_rules
            else self._empty_acl_state()
        )
        block_profile_retrieval = self._query_blocks_profile_retrieval(query)
        ranking_started = time.perf_counter()
        results, blocked, ranking_info = await asyncio.to_thread(
            self._rank_candidates_sync,
            query,
            ctx,
            expanded_terms,
            graph_terms,
            ranked_candidates,
            current_window_candidates,
            fts_candidates,
            keyword_candidates,
            vector_candidates,
            time_window_candidates,
            vector_scores,
            acl_state,
            time_intent,
            block_profile_retrieval,
        )
        self._rank_path_info.update(ranking_info)
        self._rank_path_info["ranking_cpu_ms"] = int(
            (time.perf_counter() - ranking_started) * 1000
        )
        self._rank_path_info["ranking_offloaded"] = True
        return results, blocked

    def _rank_candidates_sync(
        self,
        query: str,
        ctx: SessionContext,
        expanded_terms: list[str],
        graph_terms: list[str],
        ranked_candidates: list[MemoryRecord],
        current_window_candidates: list[MemoryRecord],
        fts_candidates: list[MemoryRecord],
        keyword_candidates: list[MemoryRecord],
        vector_candidates: list[MemoryRecord],
        time_window_candidates: list[MemoryRecord],
        vector_scores: dict[str, float],
        acl_state: dict[str, object],
        time_intent: TimeIntent | None,
        block_profile_retrieval: bool,
    ) -> tuple[list[SearchResult], list[dict[str, str]], dict[str, int]]:
        """Score a private candidate snapshot without touching the event loop."""
        route_lists = (
            ("fts", fts_candidates),
            ("current_window", current_window_candidates),
            ("keyword", keyword_candidates),
            ("time_window", time_window_candidates),
            ("vector", vector_candidates),
            ("priority", ranked_candidates),
        )
        reciprocal_rank_scores = self._reciprocal_rank_scores(route_lists)
        candidates, candidate_sources = self._merge_candidate_memories(
            ranked_candidates,
            current_window_candidates,
            fts_candidates,
            keyword_candidates,
            vector_candidates,
            time_window_candidates,
        )
        searchable: list[tuple[MemoryRecord, str]] = []
        prefiltered: dict[str, int] = {}
        time_filtered = 0
        for memory in candidates:
            memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
            visibility_reason, prefilter_reason = self._search_visibility_reason(
                memory, ctx, acl_state
            )
            if visibility_reason:
                lifecycle = evaluate_memory_lifecycle(memory, ctx)
                if not lifecycle.eligible:
                    key = clean_text(lifecycle.reason or "lifecycle_ineligible", 180)
                    prefiltered[key] = prefiltered.get(key, 0) + 1
                    continue
                if memory_type in PROFILE_MEMORY_TYPES and block_profile_retrieval:
                    reason = "profile_intent_not_requested"
                    prefiltered[reason] = prefiltered.get(reason, 0) + 1
                    continue
                if (
                    time_intent is not None
                    and time_intent.active
                    and not self._memory_overlaps_time_window(memory, time_intent)
                ):
                    time_filtered += 1
                    continue
                searchable.append((memory, visibility_reason))
            else:
                key = clean_text(prefilter_reason or "not_visible", 180)
                prefiltered[key] = prefiltered.get(key, 0) + 1
        profile = self._query_profile(query, expanded_terms)
        # IDF must describe query-aware routes, not the unrelated priority scan.
        query_aware_ids = {
            memory_id
            for memory_id, sources in candidate_sources.items()
            if sources & self._QUERY_AWARE_ROUTE_SOURCES
        }
        term_pool = [
            memory
            for memory, _reason in searchable
            if clean_text(memory.id, 120) in query_aware_ids
        ]
        if not term_pool:
            term_pool = [memory for memory, _reason in searchable]
        term_stats = self._term_document_stats(term_pool, expanded_terms)
        route_families_by_id = {
            memory_id: self._route_families(sources)
            for memory_id, sources in candidate_sources.items()
        }
        results: list[SearchResult] = []
        blocked: list[dict[str, str]] = []
        if prefiltered:
            blocked.append(
                {
                    "id": "",
                    "reason": self._prefilter_summary_reason(prefiltered),
                    "content": "",
                }
            )
        if time_filtered:
            blocked.append(
                {
                    "id": "",
                    "reason": f"time_window_filtered:{time_filtered};range={time_intent.display_range if time_intent else ''}",
                    "content": "",
                }
            )
        for memory, visibility_reason in searchable:
            memory_id = clean_text(memory.id, 120)
            score, reason = self._score(
                memory,
                expanded_terms,
                ctx,
                profile,
                term_stats,
                vector_score=vector_scores.get(memory_id, 0.0),
                reciprocal_rank_score=reciprocal_rank_scores.get(memory_id, 0.0),
                route_families=route_families_by_id.get(memory_id, frozenset()),
            )
            if score <= 0:
                if len(blocked) < 40:
                    blocked.append(
                        {
                            "id": memory.id,
                            "reason": reason,
                            "content": clean_text(memory.content, 120),
                        }
                    )
                continue
            graph_reason = (
                f";graph_terms={','.join(graph_terms[:6])}" if graph_terms else ""
            )
            source_reason = self._candidate_source_reason(memory.id, candidate_sources)
            results.append(
                SearchResult(
                    memory=memory,
                    score=score,
                    reason=f"{visibility_reason};{source_reason};{reason}{graph_reason}",
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return (
            results,
            blocked,
            {
                "candidate_count": len(candidates),
                "searchable_count": len(searchable),
                "prefiltered_count": sum(prefiltered.values()),
                "time_filtered_count": time_filtered,
            },
        )
