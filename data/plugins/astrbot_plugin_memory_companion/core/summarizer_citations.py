from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from .assertions import (
    MAX_ASSERTIONS_PER_BATCH,
    normalize_assertion,
)
from .models import clean_text
from .summarizer_support import (
    _CONTRADICTION_COVERAGE,
    _CONTRADICTION_MAX_CHARS,
    _CONTRADICTION_MIN_CHARS,
    _NEGATION_CHARS,
)


class MemorySummarizerCitations:
    """Citations; shared runtime resources belong to the facade."""

    @classmethod
    def _trace_evidence_supported(
        cls, trace: dict[str, Any], rows: list[dict[str, Any]]
    ) -> bool:
        """Compatibility alias for older audit callers."""
        return cls.trace_supported_by_rows(trace, rows)

    @classmethod
    def _normalize_assertions(
        cls,
        value: Any,
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Keep the assertions whose cited messages actually support them.

        A claim the window cannot back is dropped and reported, never used to
        fail the batch: losing one assertion must not cost the conversation its
        memory, which is the failure this layer exists to prevent.
        """
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return [], []
        row_by_id = {
            clean_text(row.get("id"), 160): row
            for row in rows
            if clean_text(row.get("id"), 160)
        }
        kept: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen: set[tuple[str, str, str, str]] = set()
        dropped = 0
        overflow = 0
        # Validate everything before applying the cap: breaking out at the cap
        # left the remainder unchecked, so a fabricated claim beyond the limit
        # was never even reported.
        for item in value[: MAX_ASSERTIONS_PER_BATCH * 3]:
            assertion = normalize_assertion(item)
            if not assertion:
                dropped += 1
                continue
            cited = [row_by_id[ref] for ref in assertion["refs"] if ref in row_by_id]
            if not cited:
                dropped += 1
                continue
            if (
                any(ref not in row_by_id for ref in assertion["refs"])
                or cls.citation_check(assertion["value"], cited)[0] != "supported"
            ):
                dropped += 1
                continue
            key = (
                assertion["subject"],
                assertion["dimension"],
                assertion["polarity"],
                assertion["normalized_value"],
            )
            if key in seen:
                continue
            seen.add(key)
            assertion["refs"] = [ref for ref in assertion["refs"] if ref in row_by_id][
                :4
            ]
            if len(kept) < MAX_ASSERTIONS_PER_BATCH:
                kept.append(assertion)
            else:
                overflow += 1
        if dropped:
            warnings.append(f"{dropped} 条断言缺少原文支持或结构不完整，已从本批剔除")
        if overflow:
            warnings.append(f"另有 {overflow} 条有效断言超出每批上限，未写入本批")
        return kept, warnings

    @classmethod
    def _infer_fact_refs(
        cls, fact: str, row_by_id: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Attribute an unreferenced claim to the messages that actually support it."""
        refs: list[str] = []
        for row_id, row in row_by_id.items():
            if cls.citation_check(fact, [row])[0] != "supported":
                continue
            refs.append(row_id)
            if len(refs) >= 2:
                break
        return refs

    @staticmethod
    def _quote_occurs_in_row(quote: Any, row: dict[str, Any]) -> bool:
        compact_quote = re.sub(r"\s+", "", clean_text(quote, 240)).casefold()
        compact_source = re.sub(
            r"\s+", "", clean_text(row.get("content"), 4000)
        ).casefold()
        return bool(compact_quote and compact_quote in compact_source)

    @classmethod
    def trace_supported_by_rows(
        cls, trace: dict[str, Any], rows: list[dict[str, Any]]
    ) -> bool:
        fact = clean_text(trace.get("fact"), 300)
        refs = trace.get("refs") if isinstance(trace.get("refs"), list) else []
        row_by_id = {clean_text(row.get("id"), 160): row for row in rows}
        evidence = trace.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            # Existing stored summaries are readable; new traces carry quotes.
            return cls.fact_supported_by_rows(fact, rows)
        evidence_rows: list[dict[str, Any]] = []
        evidence_refs: set[str] = set()
        for item in evidence:
            if not isinstance(item, dict):
                return False
            ref = clean_text(item.get("ref") or item.get("event_id"), 160)
            quote = clean_text(item.get("quote"), 240)
            row = row_by_id.get(ref)
            if (
                ref not in refs
                or row is None
                or not cls._quote_occurs_in_row(quote, row)
            ):
                return False
            evidence_refs.add(ref)
            evidence_rows.append({**row, "content": quote})
        if evidence_refs != {
            clean_text(ref, 160) for ref in refs if clean_text(ref, 160)
        }:
            return False
        return cls.fact_supported_by_rows(fact, evidence_rows)

    @classmethod
    def fact_supported_by_rows(cls, fact: Any, rows: list[dict[str, Any]]) -> bool:
        """Strict check: only a claim with no conflict at all is supported.

        Kept strict for the audit path, where an unauditable conflict must not
        protect a memory from being archived.
        """
        return cls.citation_check(fact, rows)[0] == "supported"

    @classmethod
    def citation_check(cls, fact: Any, rows: list[dict[str, Any]]) -> tuple[str, str]:
        """Validate a claim against its cited text and message timestamps.

        A polarity conflict remains reviewable; unsupported assertions cannot
        become facts. Diagnostics also feed the provider's repair prompt.
        """
        reason = cls._support_failure_reason(fact, rows)
        if not reason:
            return "supported", "grounded"
        if "否定状态" in reason:
            return "conflicted", reason
        return "unsupported", reason

    @classmethod
    def _negation_windows(cls, text: str) -> set[str]:
        """Return, for every negation marker in ``text``, the span it covers."""
        windows: set[str] = set()
        for marker in cls.NEGATION_MARKERS:
            start = text.find(marker)
            while start != -1:
                windows.add(
                    text[
                        start + len(marker) : start + len(marker) + cls.POLARITY_WINDOW
                    ]
                )
                start = text.find(marker, start + 1)
        return {window.strip() for window in windows if window.strip()}

    @classmethod
    def _span_is_negated(cls, text: str, span: str) -> bool:
        """True when at least one occurrence of ``span`` in ``text`` sits under a negation."""
        start = text.find(span)
        while start != -1:
            head = text[max(0, start - 2) : start]
            if any(marker in head for marker in cls.NEGATION_MARKERS):
                return True
            start = text.find(span, start + 1)
        return False

    @classmethod
    def _polarity_conflict(cls, compact_fact: str, source: str) -> bool:
        """Compare polarity only when the claim closely matches a source phrase."""
        stripped_fact, fact_index_map = cls._strip_negations(compact_fact)
        if len(stripped_fact) < _CONTRADICTION_MIN_CHARS:
            return False
        stripped_source, index_map = cls._strip_negations(source)
        if not stripped_source:
            return False
        matches: list[tuple[int, int, int]] = []
        position = stripped_source.find(stripped_fact)
        if position >= 0:
            matches = [(0, position, len(stripped_fact))]
        elif len(stripped_fact) <= _CONTRADICTION_MAX_CHARS:
            blocks = [
                block
                for block in SequenceMatcher(
                    None, stripped_fact, stripped_source, autojunk=False
                ).get_matching_blocks()
                if block.size
            ]
            if (
                not blocks
                or sum(block.size for block in blocks) / len(stripped_fact)
                < _CONTRADICTION_COVERAGE
            ):
                return False
            matches = [(block.a, block.b, block.size) for block in blocks]
        else:
            return False
        for fact_start_index, source_start_index, size in matches:
            if size < _CONTRADICTION_MIN_CHARS:
                continue
            fact_start = fact_index_map[fact_start_index]
            fact_end = fact_index_map[fact_start_index + size - 1] + 1
            source_start = index_map[source_start_index]
            source_end = index_map[source_start_index + size - 1] + 1
            # A following negation can qualify the next phrase ("喜欢冰美式，
            # 不加糖"). Include negations inside or before this aligned span,
            # without attaching the next phrase's polarity to it.
            fact_window = compact_fact[max(0, fact_start - 1) : fact_end]
            source_window = source[max(0, source_start - 1) : source_end]
            if any(char in fact_window for char in _NEGATION_CHARS) != any(
                char in source_window for char in _NEGATION_CHARS
            ):
                return True
        return False
