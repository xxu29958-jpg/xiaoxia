from __future__ import annotations

from typing import Any

from .injection_expression import InjectionComposerExpression
from .models import SearchResult, clean_text
from .profile_quality import PROFILE_MEMORY_TYPES, profile_quality_decision
from .turn_signal import message_terms


class InjectionComposerRelevance:
    """Relevance; shared runtime resources belong to the facade."""

    def _select_key_facts_for_query(
        self,
        key_facts: list[Any],
        *,
        query_text: str,
        max_chars: int,
    ) -> str:
        facts = [
            self._redact_sensitive_text(clean_text(value, 120))
            for value in key_facts
            if isinstance(value, str) and clean_text(value, 120)
        ]
        if not facts:
            return ""

        query_terms = set(message_terms(query_text, limit=80))
        ranked = list(enumerate(facts))
        if query_terms:
            ranked.sort(
                key=lambda pair: (
                    -len(query_terms & set(message_terms(pair[1], limit=80)))
                    / len(query_terms),
                    pair[0],
                )
            )

        selected: list[str] = []
        used_chars = 0
        budget = max(1, int(max_chars or 1))
        for _index, fact in ranked:
            added_chars = len(fact) + (1 if selected else 0)
            if used_chars + added_chars > budget:
                continue
            selected.append(fact)
            used_chars += added_chars
        return "；".join(selected)

    def _filter_injection_results(
        self,
        results: list[SearchResult],
        slot_sections: list[tuple[str, list[SearchResult]]] | None,
    ) -> tuple[list[SearchResult], list[tuple[str, list[SearchResult]]] | None]:
        """Apply the final expression and profile-quality guard.

        Retrieval is not the only producer of injection candidates. This last
        synchronous guard also covers fast paths and direct composer callers.
        """
        self.last_omission_diagnostics = []
        self.last_omission_reasons = []
        self.last_included_memory_ids = []
        decision_cache: dict[str, tuple[bool, str, str]] = {}
        diagnostic_keys: set[tuple[str, str]] = set()
        included_ids: set[str] = set()

        def item_key(item: SearchResult) -> str:
            memory_id = clean_text(
                getattr(getattr(item, "memory", None), "id", ""), 160
            )
            return memory_id or f"object:{id(item)}"

        def record(
            item: SearchResult, reason: str, *, expression: str, slot: str = ""
        ) -> None:
            memory_id = clean_text(
                getattr(getattr(item, "memory", None), "id", ""), 160
            )
            key = (memory_id or item_key(item), reason)
            if key in diagnostic_keys:
                return
            diagnostic_keys.add(key)
            diagnostic = {
                "id": memory_id,
                "reason": reason,
                "content": "",
                "expression": expression,
            }
            if slot:
                diagnostic["slot"] = clean_text(slot, 60)
            self.last_omission_diagnostics.append(diagnostic)
            self.last_omission_reasons.append(reason)

        def decision(item: SearchResult) -> tuple[bool, str, str]:
            key = item_key(item)
            cached = decision_cache.get(key)
            if cached is not None:
                return cached
            memory = getattr(item, "memory", None)
            expression = self._expression_value(item)
            allowed, reason = self._profile_injection_decision(memory)
            if not allowed:
                result = (False, f"injection_omitted:{reason}", expression)
            elif expression == "uncertain":
                result = (False, "injection_omitted:uncertain", expression)
            elif expression == "candidate" and self._is_rule_profile(memory):
                result = (False, "injection_omitted:candidate", expression)
            else:
                result = (True, "", expression)
            decision_cache[key] = result
            return result

        def keep(item: SearchResult, *, slot: str = "") -> bool:
            allowed, reason, expression = decision(item)
            if not allowed:
                record(item, reason, expression=expression, slot=slot)
                return False
            memory_id = clean_text(
                getattr(getattr(item, "memory", None), "id", ""), 160
            )
            if memory_id and memory_id not in included_ids:
                included_ids.add(memory_id)
                self.last_included_memory_ids.append(memory_id)
            if expression == "tone":
                record(
                    item,
                    "injection_content_abstracted:tone",
                    expression=expression,
                    slot=slot,
                )
            return True

        filtered_results = [item for item in (results or []) if keep(item)]
        if slot_sections is None:
            return filtered_results, None
        filtered_sections: list[tuple[str, list[SearchResult]]] = []
        for slot, items in slot_sections:
            kept = [item for item in (items or []) if keep(item, slot=slot)]
            if kept:
                filtered_sections.append((slot, kept))
        result_keys = {item_key(item) for item in filtered_results}
        for _slot, items in filtered_sections:
            for item in items:
                key = item_key(item)
                if key not in result_keys:
                    result_keys.add(key)
                    filtered_results.append(item)
        return filtered_results, filtered_sections

    @staticmethod
    def _profile_injection_decision(memory: Any) -> tuple[bool, str]:
        if memory is None:
            return False, "missing_memory"
        memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
        metadata = InjectionComposerExpression._metadata_dict(memory)
        profile_state = clean_text(metadata.get("profile_state"), 40).lower()
        review_status = clean_text(getattr(memory, "review_status", ""), 40).lower()
        lifecycle = clean_text(getattr(memory, "lifecycle", ""), 40).lower()
        is_profile = memory_type in PROFILE_MEMORY_TYPES or bool(profile_state)
        if is_profile and review_status in {"pending", "rejected"}:
            return False, f"profile_review_{review_status}"
        if is_profile and lifecycle == "archived":
            return False, "profile_state_archived"
        if profile_state in {
            "candidate",
            "pending",
            "rejected",
            "superseded",
            "archived",
        }:
            return False, f"profile_state_{profile_state}"
        return profile_quality_decision(memory, require_active=True)

    @staticmethod
    def _is_rule_profile(memory: Any) -> bool:
        if memory is None:
            return False
        memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
        metadata = InjectionComposerExpression._metadata_dict(memory)
        extractor = clean_text(metadata.get("extractor"), 80).lower()
        producer_kind = clean_text(metadata.get("producer_kind"), 80).lower()
        return memory_type in PROFILE_MEMORY_TYPES and (
            extractor.startswith("rule_") or producer_kind.startswith("rule_")
        )
