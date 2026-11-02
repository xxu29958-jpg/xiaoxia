from __future__ import annotations

from .models import (
    MemoryRecord,
    SearchResult,
    SessionContext,
    clean_text,
    is_memory_placeholder,
)
from .profile_quality import PROFILE_MEMORY_TYPES
from .time_intent import TimeIntent
from .visibility import _platform_family


class RetrievalEnginePipeline:
    """Pipeline; shared runtime resources belong to the facade."""

    async def search(
        self,
        query: str,
        ctx: SessionContext,
        top_k: int = 6,
        *,
        time_intent: TimeIntent | None = None,
    ) -> list[SearchResult]:
        results, _blocked = await self.search_with_diagnostics(
            query, ctx, top_k, time_intent=time_intent
        )
        return results

    async def revalidate_cached_results(
        self,
        results: list[SearchResult],
        ctx: SessionContext,
    ) -> list[SearchResult]:
        """Refresh cached rows and re-run current visibility and ACL checks."""
        memory_ids = [
            clean_text(getattr(getattr(item, "memory", None), "id", ""), 120)
            for item in results or []
        ]
        current = await self.store.get_memories_by_ids(
            [memory_id for memory_id in memory_ids if memory_id]
        )
        acl_state = (
            await self._acl_state()
            if self.policy.enable_acl_rules
            else self._empty_acl_state()
        )
        validated: list[SearchResult] = []
        for item, memory_id in zip(results or [], memory_ids):
            memory = current.get(memory_id)
            if memory is None:
                continue
            visible_reason, blocked_reason = self._search_visibility_reason(
                memory, ctx, acl_state
            )
            if not visible_reason or blocked_reason:
                continue
            item.memory = memory
            validated.append(item)
        return validated

    async def revalidate_cached_diagnostics(
        self,
        blocked: list[dict[str, str]],
        ctx: SessionContext,
    ) -> list[dict[str, str]]:
        """Drop cached diagnostic snippets that are no longer readable."""
        memory_ids = [
            clean_text(item.get("id"), 120)
            for item in blocked
            if isinstance(item, dict)
        ]
        current = await self.store.get_memories_by_ids(
            [memory_id for memory_id in memory_ids if memory_id]
        )
        acl_state = (
            await self._acl_state()
            if self.policy.enable_acl_rules
            else self._empty_acl_state()
        )
        validated: list[dict[str, str]] = []
        for item in blocked or []:
            if not isinstance(item, dict):
                continue
            memory_id = clean_text(item.get("id"), 120)
            if not memory_id:
                validated.append({**item, "content": ""})
                continue
            memory = current.get(memory_id)
            if memory is None:
                continue
            visible_reason, blocked_reason = self._search_visibility_reason(
                memory, ctx, acl_state
            )
            if not visible_reason or blocked_reason:
                continue
            validated.append({**item, "content": clean_text(memory.content, 120)})
        return validated

    async def filter_visible_candidates(
        self,
        candidates: list[MemoryRecord],
        ctx: SessionContext,
        *,
        reason: str = "navigation_candidate",
        include_core_memory: bool = False,
    ) -> tuple[list[SearchResult], list[dict[str, str]]]:
        """Apply the current visibility and ACL state to externally selected rows.

        Graph navigation and direct-ID lookups must pass through the same policy
        as ordinary retrieval. This helper intentionally returns no blocked
        content so callers cannot turn visibility diagnostics into a side
        channel.
        """
        acl_state = (
            await self._acl_state()
            if self.policy.enable_acl_rules
            else self._empty_acl_state()
        )
        visible: list[SearchResult] = []
        blocked: list[dict[str, str]] = []
        seen: set[str] = set()
        route_reason = clean_text(reason, 240) or "navigation_candidate"
        for memory in candidates or []:
            memory_id = clean_text(getattr(memory, "id", ""), 120)
            if not memory_id or memory_id in seen:
                continue
            seen.add(memory_id)
            if is_memory_placeholder(memory):
                blocked.append(
                    {"id": memory_id, "reason": "placeholder_memory", "content": ""}
                )
                continue
            memory_platform = _platform_family(getattr(memory, "platform", ""))
            current_platform = _platform_family(getattr(ctx, "platform", ""))
            if (
                memory_platform
                and current_platform
                and memory_platform != current_platform
            ):
                blocked.append(
                    {"id": memory_id, "reason": "other_platform", "content": ""}
                )
                continue
            owner_visible, owner_reason = self.policy._bot_owner_visible(memory, ctx)
            if not owner_visible:
                blocked.append(
                    {
                        "id": memory_id,
                        "reason": clean_text(owner_reason or "other_bot_owner", 180),
                        "content": "",
                    }
                )
                continue
            visibility_reason, blocked_reason = self._search_visibility_reason(
                memory,
                ctx,
                acl_state,
                include_core_memory=include_core_memory,
            )
            if not visibility_reason:
                blocked.append(
                    {
                        "id": memory_id,
                        "reason": clean_text(blocked_reason or "not_visible", 180),
                        "content": "",
                    }
                )
                continue
            score = max(
                0.01,
                min(
                    1.0,
                    (float(getattr(memory, "confidence", 0.0) or 0.0) * 0.6)
                    + (float(getattr(memory, "importance", 0.0) or 0.0) * 0.4),
                ),
            )
            visible.append(
                SearchResult(
                    memory=memory,
                    score=score,
                    reason=f"{visibility_reason};candidate_route={route_reason}",
                )
            )
        return visible, blocked

    async def search_with_diagnostics(
        self,
        query: str,
        ctx: SessionContext,
        top_k: int = 6,
        *,
        time_intent: TimeIntent | None = None,
    ) -> tuple[list[SearchResult], list[dict[str, str]]]:
        self.start_latency_budget()
        results, blocked = await self._rank_candidates(
            query, ctx, time_intent=time_intent
        )
        results = await self._maybe_rerank_results(
            query, results, max(1, int(top_k or 1))
        )
        # relax：内容级去重（规范化文本 difflib ratio >= 阈值时只留评分最高者）。
        # 默认 0.0 关闭，行为与历史版本完全一致；MMR 多样化仍然保留。
        if self.dedupe_content_ratio > 0:
            results = self._dedupe_by_content(results, self.dedupe_content_ratio)
        results = await self._mmr_diversify_async(results, max(1, int(top_k or 1)))
        selected = results[: max(1, int(top_k or 1))]
        selected, mutable_blocked = self._collapse_mutable_fact_results(
            query, ctx, selected, time_intent=time_intent
        )
        blocked.extend(mutable_blocked)
        selected, source_blocked = self._collapse_redundant_source_results(selected)
        blocked.extend(source_blocked)
        return selected, blocked

    async def search_by_slots(
        self,
        query: str,
        ctx: SessionContext,
        *,
        slot_limits: dict[str, int],
        total_limit: int = 6,
        time_intent: TimeIntent | None = None,
        capped_slots: set[str] | None = None,
    ) -> tuple[list[SearchResult], list[dict[str, str]], dict[str, list[SearchResult]]]:
        self.start_latency_budget()
        ranked, blocked = await self._rank_candidates(
            query, ctx, time_intent=time_intent
        )
        total = max(1, int(total_limit or 1))
        ranked = await self._maybe_rerank_results(query, ranked, total)
        ranked = await self._mmr_diversify_async(
            ranked, min(len(ranked), max(total * 6, 24))
        )
        allow_profile_fallback = self._query_allows_profile_fallback(query)
        block_profile_retrieval = self._query_blocks_profile_retrieval(query)

        def selectable_for_slot(item: SearchResult, slot: str) -> bool:
            if slot != "user_profile":
                return True
            memory_type = clean_text(
                getattr(item.memory, "memory_type", ""), 80
            ).lower()
            if memory_type in PROFILE_MEMORY_TYPES and block_profile_retrieval:
                return False
            if allow_profile_fallback:
                return True
            return (
                memory_type not in PROFILE_MEMORY_TYPES
                or self._profile_result_has_relevance(item)
            )

        enforced_caps = {
            clean_text(slot, 60)
            for slot in (capped_slots or set())
            if clean_text(slot, 60)
        }
        slot_order = [
            "open_loop",
            "self_timeline",
            "user_profile",
            "current_window",
            "conversation_summary",
            "stable_memory",
        ]
        selected: list[SearchResult] = []
        selected_ids: set[str] = set()
        slot_map: dict[str, list[SearchResult]] = {slot: [] for slot in slot_order}
        # ``open_loop`` is usually derived from the conversation itself and
        # remains part of query evidence.  Only Bot-owned/non-dialogue records
        # in ``self_timeline`` consume this separate budget.
        secondary_slots = {"self_timeline"}

        def secondary_allowed(slot: str) -> bool:
            if slot not in secondary_slots or slot not in enforced_caps:
                return True
            used = sum(len(slot_map.get(name, [])) for name in secondary_slots)
            return used < self.non_conversation_budget

        # Give every available slot one chance before any slot spends the rest
        # of its normal budget. This keeps later summary and stable-memory
        # slots visible when several earlier slots also have candidates.
        for slot in slot_order:
            limit = max(0, int(slot_limits.get(slot, 0) or 0))
            if len(selected) >= total:
                break
            if limit <= 0:
                continue
            item = next(
                (
                    candidate
                    for candidate in ranked
                    if candidate.memory.id not in selected_ids
                    and self._slot_for_memory(candidate.memory, ctx) == slot
                    and selectable_for_slot(candidate, slot)
                    and secondary_allowed(slot)
                ),
                None,
            )
            if item is None:
                continue
            item.reason = self._with_slot_reason(item.reason, slot)
            slot_map[slot].append(item)
            selected.append(item)
            selected_ids.add(item.memory.id)

        for slot in slot_order:
            limit = max(0, int(slot_limits.get(slot, 0) or 0))
            if limit <= 0:
                continue
            for item in ranked:
                if len(selected) >= total or len(slot_map[slot]) >= limit:
                    break
                if item.memory.id in selected_ids:
                    continue
                if self._slot_for_memory(item.memory, ctx) != slot:
                    continue
                if not selectable_for_slot(item, slot):
                    continue
                if not secondary_allowed(slot):
                    continue
                item.reason = self._with_slot_reason(item.reason, slot)
                slot_map[slot].append(item)
                selected.append(item)
                selected_ids.add(item.memory.id)

        # Slot limits are diversity budgets rather than global hard limits.
        # Only explicitly capped slots stay inside their budget during ranked
        # fallback; other highly relevant memory types may still fill top_k.
        if len(selected) < total:
            for item in ranked:
                if len(selected) >= total:
                    break
                if item.memory.id in selected_ids:
                    continue
                slot = self._slot_for_memory(item.memory, ctx)
                if not selectable_for_slot(item, slot):
                    continue
                slot_limit = max(0, int(slot_limits.get(slot, 0) or 0))
                if slot_limit <= 0:
                    continue
                if slot in enforced_caps and len(slot_map.get(slot, [])) >= slot_limit:
                    continue
                if not secondary_allowed(slot):
                    continue
                item.reason = self._with_slot_reason(item.reason, slot)
                slot_map.setdefault(slot, []).append(item)
                selected.append(item)
                selected_ids.add(item.memory.id)

        selected, mutable_blocked, slot_map = self._collapse_mutable_fact_slots(
            query, ctx, selected, slot_map, time_intent=time_intent
        )
        blocked.extend(mutable_blocked)
        selected, source_blocked, slot_map = self._collapse_redundant_source_slots(
            selected, slot_map
        )
        blocked.extend(source_blocked)
        return (
            selected,
            blocked,
            {slot: items for slot, items in slot_map.items() if items},
        )
