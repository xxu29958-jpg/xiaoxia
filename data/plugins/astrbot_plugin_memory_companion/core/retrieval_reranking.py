from __future__ import annotations

import asyncio
import inspect
import math
import re
import time
from datetime import datetime, timezone
from typing import Any

from .models import (
    MemoryRecord,
    SearchResult,
    clean_text,
)


class RetrievalEngineReranking:
    """Reranking; shared runtime resources belong to the facade."""

    def start_latency_budget(self) -> None:
        """Begin one recall so optional model calls can be dropped once it is spent."""
        self._latency_started_at = (
            time.perf_counter() if self.latency_budget_ms > 0 else None
        )

    def latency_budget_spent(self) -> bool:
        if self._latency_started_at is None or self.latency_budget_ms <= 0:
            return False
        elapsed_ms = (time.perf_counter() - self._latency_started_at) * 1000.0
        return elapsed_ms >= float(self.latency_budget_ms)

    async def _maybe_rerank_results(
        self,
        query: str,
        ranked: list[SearchResult],
        final_limit: int,
    ) -> list[SearchResult]:
        if not ranked:
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": "basic",
                "provider_id": self.rerank_provider_id,
                "reason": "no_candidates",
                "candidate_count": 0,
                **self._rank_path_info,
            }
            return ranked
        if self.retrieval_mode == "basic":
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": "basic",
                "provider_id": self.rerank_provider_id,
                "reason": "mode_basic",
                "candidate_count": len(ranked),
                **self._rank_path_info,
            }
            return ranked
        if self.latency_budget_spent():
            # Dropping the rerank call costs ordering quality, not coverage: the
            # same candidates still reach the model.
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": "fallback_basic",
                "provider_id": self.rerank_provider_id,
                "reason": "latency_budget_spent",
                "candidate_count": len(ranked),
                **self._rank_path_info,
            }
            return ranked
        if self.rerank_provider is None or not hasattr(self.rerank_provider, "rerank"):
            path = "fallback_basic" if self.retrieval_mode == "rerank" else "basic"
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": path,
                "provider_id": self.rerank_provider_id,
                "reason": "no_rerank_provider",
                "candidate_count": len(ranked),
                **self._rank_path_info,
            }
            return ranked

        query_text = clean_text(query, 1000)
        if not query_text:
            path = "fallback_basic" if self.retrieval_mode == "rerank" else "basic"
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": path,
                "provider_id": self.rerank_provider_id,
                "reason": "rerank_skipped_empty_query",
                "candidate_count": len(ranked),
                **self._rank_path_info,
            }
            return ranked

        pool_size = min(
            len(ranked),
            max(
                max(1, int(final_limit or 1)) * self.rerank_candidate_multiplier,
                max(1, int(final_limit or 1)),
            ),
            self.rerank_candidate_limit,
        )
        pool = ranked[:pool_size]
        rerank_pool: list[SearchResult] = []
        documents: list[str] = []
        for item in pool:
            document = clean_text(self._rerank_document_text(item), 1200)
            if not document:
                continue
            rerank_pool.append(item)
            documents.append(document)
        filtered_count = pool_size - len(rerank_pool)
        request_shape = self._rerank_request_shape(query_text, documents)
        if not documents:
            path = "fallback_basic" if self.retrieval_mode == "rerank" else "basic"
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": path,
                "provider_id": self.rerank_provider_id,
                "reason": "rerank_skipped_no_valid_documents",
                "candidate_count": len(ranked),
                "rerank_pool": 0,
                "rerank_filtered": filtered_count,
                **request_shape,
                **self._rank_path_info,
            }
            return ranked
        try:
            rerank_resp = await self._call_rerank_provider(query_text, documents)
            reranked = self._apply_rerank_response(rerank_resp, rerank_pool)
        except Exception as error:
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": "fallback_basic",
                "provider_id": self.rerank_provider_id,
                "reason": f"rerank_error:{type(error).__name__}:{clean_text(error, 160)}",
                "rerank_error_type": type(error).__name__,
                "rerank_timeout_ms": self.rerank_timeout_ms,
                "candidate_count": len(ranked),
                "rerank_pool": len(rerank_pool),
                "rerank_filtered": filtered_count,
                **request_shape,
                **self._rank_path_info,
            }
            return ranked

        if not reranked:
            self.last_path_info = {
                "mode": self.retrieval_mode,
                "path": "fallback_basic",
                "provider_id": self.rerank_provider_id,
                "reason": "rerank_empty_response",
                "candidate_count": len(ranked),
                "rerank_pool": len(rerank_pool),
                "rerank_filtered": filtered_count,
                **request_shape,
                **self._rank_path_info,
            }
            return ranked

        anchors = self._rerank_anchor_results(query_text, pool, final_limit)
        merged: list[SearchResult] = []
        seen: set[str] = set()
        for item in [*reranked, *pool]:
            memory_id = item.memory.id
            if memory_id and memory_id in seen:
                continue
            if memory_id:
                seen.add(memory_id)
            merged.append(item)
        rest = ranked[pool_size:]
        anchor_floor_applied = self._apply_anchor_floor(
            merged, anchors, reranked, max(1, int(final_limit or 1))
        )
        self.last_path_info = {
            "mode": self.retrieval_mode,
            "path": "rerank",
            "provider_id": self.rerank_provider_id or "<auto>",
            "reason": "rerank_applied",
            "candidate_count": len(ranked),
            "rerank_pool": len(rerank_pool),
            "rerank_filtered": filtered_count,
            "reranked_count": len(reranked),
            "lexical_anchors": len(anchors),
            "anchor_floor_applied": anchor_floor_applied,
            **request_shape,
            **self._rank_path_info,
        }
        return merged + rest

    def _apply_anchor_floor(
        self,
        merged: list[SearchResult],
        anchors: list[SearchResult],
        reranked: list[SearchResult],
        final_limit: int,
    ) -> int:
        """Keep strong lexical matches inside the top-k window without letting
        them outrank the rerank model.

        Anchors used to be prepended, which locked the final order to lexical
        matching whenever an anchor existed. Now they only act as a floor: an
        anchor that the model pushed out of the window is swapped back into the
        weakest slot, evicting the least supported non-anchor row.
        """

        if not anchors or len(merged) <= final_limit:
            return 0
        anchor_ids = {item.memory.id for item in anchors if item.memory.id}
        top = merged[:final_limit]
        tail = merged[final_limit:]
        top_ids = {item.memory.id for item in top if item.memory.id}
        missing = [
            item for item in anchors if item.memory.id and item.memory.id not in top_ids
        ]
        if not missing:
            return 0
        reranked_ids = {item.memory.id for item in reranked if item.memory.id}
        evictable = [
            item
            for item in top
            if not item.memory.id or item.memory.id not in anchor_ids
        ]
        # Evict rows the model did not return first (no rerank support at all),
        # then the lowest combined score.
        evictable.sort(
            key=lambda item: (
                1 if (item.memory.id and item.memory.id in reranked_ids) else 0,
                item.score,
            )
        )
        evicted: list[SearchResult] = []
        applied = 0
        for anchor, victim in zip(missing, evictable):
            index = next(
                position for position, item in enumerate(top) if item is victim
            )
            top[index] = anchor
            evicted.append(victim)
            applied += 1
        # The swapped-in anchor may still sit later in the tail; rebuild once
        # so the window, the evicted rows, and the tail stay duplicate free.
        deduped: list[SearchResult] = []
        seen_ids: set[str] = set()
        for item in [*top, *evicted, *tail]:
            memory_id = item.memory.id
            if memory_id and memory_id in seen_ids:
                continue
            if memory_id:
                seen_ids.add(memory_id)
            deduped.append(item)
        merged[:] = deduped
        return applied

    def _rerank_anchor_results(
        self,
        query: str,
        pool: list[SearchResult],
        final_limit: int,
    ) -> list[SearchResult]:
        if not pool:
            return []
        anchors = [
            item for item in pool if self._strong_lexical_match(query, item.memory)
        ]
        if not anchors:
            return []
        anchors.sort(key=lambda item: item.score, reverse=True)
        return anchors[: max(1, min(len(anchors), int(final_limit or 1)))]

    async def _mmr_diversify_async(
        self,
        ranked: list[SearchResult],
        selection_limit: int,
    ) -> list[SearchResult]:
        """Run deterministic MMR off the event loop without changing its output."""
        if len(ranked) < 2 or selection_limit <= 1:
            self._rank_path_info["mmr_wall_ms"] = 0
            self._rank_path_info["mmr_offloaded"] = False
            self.last_path_info.update(self._rank_path_info)
            return ranked
        started = time.perf_counter()
        # The worker receives a private list; MMR only reorders that list and
        # mutates no store, request, or shared service state.
        result = await asyncio.to_thread(
            self._mmr_diversify,
            list(ranked),
            selection_limit,
        )
        self._rank_path_info["mmr_wall_ms"] = int(
            (time.perf_counter() - started) * 1000
        )
        self._rank_path_info["mmr_offloaded"] = True
        self.last_path_info.update(self._rank_path_info)
        return result

    def _mmr_diversify(
        self, ranked: list[SearchResult], selection_limit: int
    ) -> list[SearchResult]:
        """Reorder the head with MMR while preserving every candidate."""
        if len(ranked) < 2 or selection_limit <= 1:
            return ranked
        head_size = min(len(ranked), max(int(selection_limit or 1) * 4, 16))
        pool = list(ranked[:head_size])
        scores = [float(item.score) for item in pool]
        minimum = min(scores)
        maximum = max(scores)

        def relevance(item: SearchResult) -> float:
            if maximum <= minimum:
                return 1.0
            return (float(item.score) - minimum) / (maximum - minimum)

        def features(item: SearchResult) -> set[str]:
            memory = item.memory
            text = re.sub(r"\s+", "", self._haystack(memory))
            cjk = frozenset(
                text[index : index + 2]
                for index in range(max(0, len(text) - 1))
                if text[index : index + 2]
            )
            # Avoid sorting the common case while preserving the historical
            # deterministic 180-feature cap for unusually long memories.
            if len(cjk) > 180:
                cjk_features = set(sorted(cjk)[:180])
            else:
                cjk_features = set(cjk)
            latin = set(re.findall(r"[a-z0-9_]{2,}", text))
            tags = {
                f"tag:{clean_text(tag, 40).lower()}"
                for tag in (memory.tags or [])
                if clean_text(tag, 40)
            }
            source = clean_text(memory.source_plugin, 60).lower()
            return (
                cjk_features
                | latin
                | tags
                | ({f"source:{source}"} if source else set())
            )

        feature_map: dict[int, set[str]] = {}

        def feature_for(item: SearchResult) -> set[str]:
            item_id = id(item)
            cached = feature_map.get(item_id)
            if cached is None:
                cached = features(item)
                feature_map[item_id] = cached
            return cached

        def similarity(left: SearchResult, right: SearchResult) -> float:
            left_features = feature_for(left)
            right_features = feature_for(right)
            union = left_features | right_features
            return (len(left_features & right_features) / len(union)) if union else 0.0

        selected: list[SearchResult] = [pool.pop(0)]
        target = min(len(ranked), max(1, int(selection_limit or 1)))
        while pool and len(selected) < target:
            best_index = 0
            best_value = -math.inf
            for index, item in enumerate(pool):
                # Redundancy is non-negative, so this is an exact upper bound
                # for the candidate's MMR value. Candidates that cannot beat
                # the current winner need no expensive feature/intersection work.
                upper_bound = 0.84 * relevance(item)
                if upper_bound <= best_value:
                    continue
                redundancy = max(similarity(item, chosen) for chosen in selected)
                value = upper_bound - (0.16 * redundancy)
                if value > best_value:
                    best_value = value
                    best_index = index
            selected.append(pool.pop(best_index))
        selected_ids = {id(item) for item in selected}
        tail = [item for item in ranked if id(item) not in selected_ids]
        return [*selected, *tail]

    def _dedupe_by_content(
        self, ranked: list[SearchResult], ratio_threshold: float
    ) -> list[SearchResult]:
        """Collapse near-identical memory contents, keeping the highest-scoring one.

        Compares normalized (whitespace-stripped) content with difflib ratio.
        Enabled only when dedupe_content_ratio > 0 (default off, historical behavior).
        """
        if len(ranked) < 2 or ratio_threshold <= 0:
            return ranked
        import difflib

        def norm(text: str) -> str:
            return re.sub(r"\s+", "", text or "")

        kept: list[SearchResult] = []
        for item in ranked:
            content = norm(item.memory.content)
            if not content:
                kept.append(item)
                continue
            duplicate_of = None
            for other in kept:
                other_content = norm(other.memory.content)
                if not other_content:
                    continue
                try:
                    ratio = difflib.SequenceMatcher(
                        None, content, other_content
                    ).ratio()
                except Exception:
                    ratio = 0.0
                if ratio >= ratio_threshold:
                    duplicate_of = other
                    break
            if duplicate_of is None or (item.score or 0.0) > (
                duplicate_of.score or 0.0
            ):
                if duplicate_of is not None:
                    kept.remove(duplicate_of)
                kept.append(item)
            # else: drop this item (duplicate with lower/equal score)
        return kept

    def _strong_lexical_match(self, query: str, memory: MemoryRecord) -> bool:
        terms = self._terms(query)
        if not terms:
            return False
        haystack = self._haystack(memory)
        compact_haystack = re.sub(r"\s+", "", haystack)
        profile = self._query_profile(query, terms)
        exact_phrases = [
            str(item) for item in profile.get("exact_phrases", []) if str(item)
        ]
        if any(phrase in compact_haystack for phrase in exact_phrases):
            return True
        hits = sum(1 for term in terms if term and term in haystack)
        contextual_recall = self._looks_like_contextual_recall_query(query)
        if contextual_recall and hits >= 1:
            return True
        return hits >= max(2, int(profile.get("min_hits", 1) or 1))

    async def _call_rerank_provider(self, query: str, documents: list[str]) -> Any:
        method = self.rerank_provider.rerank
        query_text = clean_text(query, 1000)
        document_texts = [clean_text(document, 1200) for document in documents]
        if not query_text:
            raise ValueError("rerank query must not be empty")
        if not document_texts or any(not document for document in document_texts):
            raise ValueError("rerank documents must not contain empty text")
        kwargs: dict[str, Any] = {"query": query_text, "documents": document_texts}
        try:
            signature = inspect.signature(method)
            params = signature.parameters
            accepts_top_n = "top_n" in params or any(
                param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
            )
        except Exception:
            accepts_top_n = True
        if accepts_top_n:
            kwargs["top_n"] = len(documents)
        prompt = self._usage_prompt_for_rerank(query_text, document_texts)
        started = datetime.now(timezone.utc)
        resp: Any = None
        success = False
        error = ""
        try:
            result = method(**kwargs)
            if inspect.isawaitable(result):
                if self.rerank_timeout_ms > 0:
                    resp = await asyncio.wait_for(
                        result, timeout=self.rerank_timeout_ms / 1000.0
                    )
                else:
                    resp = await result
            else:
                resp = result
            success = True
            return resp
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            elapsed_ms = int(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000
            )
            self._record_usage(
                task="memory_rerank",
                provider_id=self.rerank_provider_id or "<auto>",
                prompt=prompt,
                completion="",
                resp=resp,
                success=success,
                elapsed_ms=elapsed_ms,
                error=error,
            )

    def _rerank_request_shape(self, query: str, documents: list[str]) -> dict[str, Any]:
        lengths = [len(clean_text(document, 1200)) for document in documents]
        model = clean_text(getattr(self.rerank_provider, "model", ""), 240)
        if not model:
            provider_config = getattr(self.rerank_provider, "provider_config", None)
            if isinstance(provider_config, dict):
                model = clean_text(
                    provider_config.get("rerank_model") or provider_config.get("model"),
                    240,
                )
        return {
            "rerank_query_chars": len(clean_text(query, 1000)),
            "rerank_document_count": len(lengths),
            "rerank_document_min_chars": min(lengths) if lengths else 0,
            "rerank_document_max_chars": max(lengths) if lengths else 0,
            "rerank_model_chars": len(model),
        }

    def _apply_rerank_response(
        self,
        rerank_resp: Any,
        pool: list[SearchResult],
    ) -> list[SearchResult]:
        items = self._extract_rerank_items(rerank_resp)
        if not items:
            return []
        by_id = {item.memory.id: item for item in pool if item.memory.id}
        by_text = {self._rerank_document_text(item): item for item in pool}
        scored: list[tuple[int, SearchResult, float]] = []
        used_ids: set[str] = set()
        for index, item in enumerate(items):
            result = self._rerank_item_to_result(item, index, pool, by_id, by_text)
            if result is None:
                continue
            result_item, score = result
            memory_id = result_item.memory.id
            if memory_id and memory_id in used_ids:
                continue
            if memory_id:
                used_ids.add(memory_id)
            scored.append((index, result_item, score))
        if not scored:
            return []
        max_score = max(score for _index, _item, score in scored)
        min_score = min(score for _index, _item, score in scored)
        local_min = min((item.score for item in pool), default=0.0)
        local_max = max((item.score for item in pool), default=0.0)
        # Convex combination instead of additive stacking: the local score
        # already contains vector similarity and importance, so adding the
        # rerank score on top counted semantic relevance twice. Equal weights
        # let the model reorder weak lexical leaders while anchored matches are
        # still protected by the anchor floor.
        combined_rows: list[tuple[float, int, SearchResult, float]] = []
        for rank_index, item, score in scored:
            if max_score > min_score:
                rerank_norm = (score - min_score) / (max_score - min_score)
            else:
                rerank_norm = (
                    1.0 - (rank_index / max(1, len(scored) - 1))
                    if len(scored) > 1
                    else 1.0
                )
            if local_max > local_min:
                local_norm = (item.score - local_min) / (local_max - local_min)
            else:
                local_norm = 0.5
            combined = (0.5 * local_norm) + (0.5 * max(0.0, min(1.0, rerank_norm)))
            combined_rows.append((combined, rank_index, item, score))
        combined_rows.sort(key=lambda row: (-row[0], row[1]))
        return [
            SearchResult(
                memory=item.memory,
                score=combined,
                reason=f"{item.reason};retrieval_path=rerank;rerank_score={raw_score:.4g};combined={combined:.4g}",
            )
            for combined, _rank_index, item, raw_score in combined_rows
        ]

    def _extract_rerank_items(self, rerank_resp: Any) -> list[Any]:
        if rerank_resp is None:
            return []
        if isinstance(rerank_resp, dict):
            if rerank_resp.get("code") not in (None, 0, 200, "0", "200"):
                return []
            items = rerank_resp.get("results")
            if items is None:
                data = rerank_resp.get("data")
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("results") or data.get("items")
            if isinstance(items, list):
                return items
            return []
        if isinstance(rerank_resp, list):
            return rerank_resp
        results = getattr(rerank_resp, "results", None)
        return results if isinstance(results, list) else []

    def _rerank_item_to_result(
        self,
        item: Any,
        fallback_index: int,
        pool: list[SearchResult],
        by_id: dict[str, SearchResult],
        by_text: dict[str, SearchResult],
    ) -> tuple[SearchResult, float] | None:
        if isinstance(item, (int, float)):
            if 0 <= fallback_index < len(pool):
                return pool[fallback_index], float(item)
            return None
        if not isinstance(item, dict):
            item = {
                "id": getattr(item, "id", None),
                "index": getattr(item, "index", None),
                "score": getattr(item, "score", None),
                "relevance_score": getattr(item, "relevance_score", None),
                "rerank_score": getattr(item, "rerank_score", None),
                "text": getattr(item, "text", None),
                "document": getattr(item, "document", None),
            }
        result_item: SearchResult | None = None
        raw_index = item.get("index")
        if isinstance(raw_index, int) and 0 <= raw_index < len(pool):
            result_item = pool[raw_index]
        if result_item is None:
            item_id = clean_text(
                item.get("id")
                or item.get("document_id")
                or item.get("memory_id")
                or item.get("item_id"),
                200,
            )
            if item_id:
                result_item = by_id.get(item_id)
        if result_item is None:
            text = clean_text(
                item.get("text") or item.get("document") or item.get("content"), 4000
            )
            if text:
                result_item = by_text.get(text)
        if result_item is None and 0 <= fallback_index < len(pool):
            result_item = pool[fallback_index]
        if result_item is None:
            return None
        raw_score = item.get("score")
        if raw_score is None:
            raw_score = item.get("relevance_score")
        if raw_score is None:
            raw_score = item.get("rerank_score")
        try:
            score = float(raw_score)
        except Exception:
            score = float(len(pool) - fallback_index)
        return result_item, score

    def _rerank_document_text(self, item: SearchResult) -> str:
        memory = item.memory
        content = clean_text(memory.content, 1000)
        evidence = clean_text(memory.evidence, 600)
        tags = [
            clean_text(tag, 80) for tag in (memory.tags or []) if clean_text(tag, 80)
        ]
        if not content and not evidence and not tags:
            return ""
        parts = [
            f"类型: {memory.memory_type}",
            f"范围: {memory.scope}/{memory.visibility}",
        ]
        if tags:
            parts.append(f"标签: {' '.join(tags)}")
        if content:
            parts.append(f"内容: {content}")
        # Summaries carry their factual core in metadata; the reranker sees at
        # least as much as the embedding channel does.
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        canonical = clean_text(metadata.get("canonical_summary"), 400)
        if canonical:
            parts.append(f"要点: {canonical}")
        key_facts = metadata.get("key_facts")
        if isinstance(key_facts, list):
            facts_text = " ".join(
                clean_text(fact, 160) for fact in key_facts if clean_text(fact, 160)
            )
            if facts_text:
                parts.append(f"事实: {facts_text[:400]}")
        if evidence:
            parts.append(f"证据: {evidence}")
        return clean_text("\n".join(parts), 1200)
