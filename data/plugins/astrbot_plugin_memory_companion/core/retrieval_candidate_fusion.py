from __future__ import annotations

from .models import (
    MemoryRecord,
    clean_text,
)


class RetrievalEngineCandidateFusion:
    """Candidate fusion; shared runtime resources belong to the facade."""

    @staticmethod
    def _reciprocal_rank_scores(
        route_lists: tuple[tuple[str, list[MemoryRecord]], ...],
        *,
        rank_constant: int = 60,
    ) -> dict[str, float]:
        scores: dict[str, float] = {}
        for _route, memories in route_lists:
            seen: set[str] = set()
            for rank, memory in enumerate(memories or [], start=1):
                memory_id = clean_text(getattr(memory, "id", ""), 120)
                if not memory_id or memory_id in seen:
                    continue
                seen.add(memory_id)
                scores[memory_id] = scores.get(memory_id, 0.0) + (
                    1.0 / (rank_constant + rank)
                )
        return scores

    @staticmethod
    def _merge_candidate_memories(
        ranked_candidates: list[MemoryRecord],
        current_window_candidates: list[MemoryRecord],
        fts_candidates: list[MemoryRecord],
        keyword_candidates: list[MemoryRecord],
        vector_candidates: list[MemoryRecord] | None = None,
        time_window_candidates: list[MemoryRecord] | None = None,
    ) -> tuple[list[MemoryRecord], dict[str, set[str]]]:
        merged: list[MemoryRecord] = []
        sources: dict[str, set[str]] = {}
        seen_ids: set[str] = set()

        def add(memory: MemoryRecord, source: str) -> None:
            memory_id = clean_text(memory.id, 120)
            if memory_id:
                sources.setdefault(memory_id, set()).add(source)
            if not memory_id or memory_id not in seen_ids:
                merged.append(memory)
                if memory_id:
                    seen_ids.add(memory_id)

        for memory in fts_candidates:
            add(memory, "fts")
        for memory in current_window_candidates:
            add(memory, "current_window")
        for memory in keyword_candidates:
            add(memory, "keyword")
        for memory in time_window_candidates or []:
            add(memory, "time_window")
        for memory in vector_candidates or []:
            add(memory, "vector")
        for memory in ranked_candidates:
            add(memory, "priority")
        return merged, sources

    @staticmethod
    def _candidate_source_reason(memory_id: str, sources: dict[str, set[str]]) -> str:
        item_sources = sorted(sources.get(clean_text(memory_id, 120), set()))
        if not item_sources:
            item_sources = ["unknown"]
        return "candidate_route=" + "+".join(item_sources)

    @staticmethod
    def _merge_terms(
        primary: list[str], extra: list[str], *, limit: int = 40
    ) -> list[str]:
        merged: list[str] = []
        for term in [*primary, *extra]:
            text = clean_text(term, 80).lower()
            if text and text not in merged:
                merged.append(text)
            if len(merged) >= limit:
                break
        return merged
