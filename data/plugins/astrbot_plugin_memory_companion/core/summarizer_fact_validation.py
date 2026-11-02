from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from .models import clean_text
from .summarizer_support import (
    _DATE_PATTERNS,
    _HOUR_PERIODS,
    _NEGATION_CHARS,
    _RELATIVE_TIME_HINTS,
    _TIME_CLAIM_RE,
    _WEEKDAY_RE,
)
from .turn_signal import message_terms


class MemorySummarizerFactValidation:
    """Fact validation; shared runtime resources belong to the facade."""

    @classmethod
    def _row_time_evidence(cls, row: dict[str, Any]) -> str:
        """把一条消息自身的时间戳转成可参与校验的本地时间事实文本。

        这是绝对时间的唯一证据来源：正文里没有日期串，但提示词要求断言写绝对时间。
        """
        dt = cls._parse_local_datetime(row.get("occurred_at") or row.get("created_at"))
        if dt is None:
            return ""
        weekday = "一二三四五六日"[dt.weekday()]
        parts = [
            dt.strftime("%Y-%m-%d"),
            f"{dt.month}月{dt.day}日",
            f"周{weekday}",
            f"星期{weekday}",
            *cls._hour_periods(dt.hour),
            f"{dt.hour}点",
            f"{dt.hour}点{dt.minute:02d}分",
        ]
        return "".join(parts)

    @staticmethod
    def _hour_periods(hour: int) -> tuple[str, ...]:
        return _HOUR_PERIODS.get(hour, ())

    @classmethod
    def _rows_time_evidence(cls, rows: list[dict[str, Any]]) -> str:
        return "".join(cls._row_time_evidence(row) for row in rows)

    @staticmethod
    def _strip_negations(text: str) -> tuple[str, list[int]]:
        """去掉否定词，并保留去掉后每个字符在原文中的下标（用于回看原始片段）。"""
        stripped: list[str] = []
        index_map: list[int] = []
        for index, char in enumerate(text):
            if char in _NEGATION_CHARS:
                continue
            stripped.append(char)
            index_map.append(index)
        return "".join(stripped), index_map

    @classmethod
    def _date_tokens(cls, text: str) -> set[tuple[int, int, int]]:
        """抽出文本里的完整日期，归一化成 (年, 月, 日)；无年份写法年记 0。"""
        found: set[tuple[int, int, int]] = set()
        for pattern, has_year in _DATE_PATTERNS:
            for match in pattern.finditer(text):
                groups = match.groups()
                try:
                    if has_year:
                        year, month, day = (
                            int(groups[0]),
                            int(groups[1]),
                            int(groups[2]),
                        )
                    else:
                        year, month, day = 0, int(groups[0]), int(groups[1])
                except (TypeError, ValueError):
                    continue
                if 1 <= month <= 12 and 1 <= day <= 31:
                    found.add((year, month, day))
        return found

    @staticmethod
    def _format_date_claim(value: tuple[int, int, int]) -> str:
        year, month, day = value
        return f"{year:04d}-{month:02d}-{day:02d}" if year else f"{month:02d}-{day:02d}"

    @staticmethod
    def _date_supported(
        claim: tuple[int, int, int], evidence: set[tuple[int, int, int]]
    ) -> bool:
        """月日一致即可（任一侧缺年份时无法比年）；两侧都有年份则必须一致。"""
        claim_year, claim_month, claim_day = claim
        for year, month, day in evidence:
            if (month, day) != (claim_month, claim_day):
                continue
            if claim_year and year and claim_year != year:
                continue
            return True
        return False

    @classmethod
    def _time_claim_mismatch(
        cls,
        compact_fact: str,
        text: str,
        time_evidence: str,
        rows: list[dict[str, Any]],
    ) -> str:
        """只拒绝**有明确矛盾**的时间断言，返回矛盾描述；"" = 不拒绝。

        规则（对齐 M-02「错误断言不能仅凭词语重合通过证据验证」，同时不误杀合法换算）：
        - 只校验**完整日期**（YYYY-MM-DD / M/D / M月D日）与**周几**——正文里被讨论的
          日期、消息自身时间戳、以及「明天/明年/月底」这类相对说法的换算结果都算有依据；
        - 无从判定的写法（只有月、只有日）不参与拒绝；
        - 日期紧接时段的时间前缀须有正文或时间戳支持；独立时段/钟点不作硬校验，
          避免把所描述事件的时间强行等同于消息发送时间。
        """
        if not _TIME_CLAIM_RE.search(compact_fact):
            return ""
        claim_dates = cls._date_tokens(compact_fact)
        claim_weekdays = set(_WEEKDAY_RE.findall(compact_fact))
        if not claim_dates and not claim_weekdays:
            return ""
        # 正文含相对时间说法 → 断言里的绝对日期/周几可能是换算出来的，无法证伪
        if any(hint in text for hint in _RELATIVE_TIME_HINTS):
            return ""
        evidence_dates = cls._date_tokens(text) | cls._date_tokens(time_evidence)
        evidence_weekdays = set(_WEEKDAY_RE.findall(text)) | set(
            _WEEKDAY_RE.findall(time_evidence)
        )
        missing = [
            cls._format_date_claim(claim)
            for claim in sorted(claim_dates)
            if not cls._date_supported(claim, evidence_dates)
        ]
        missing.extend(
            f"周{weekday}"
            for weekday in sorted(claim_weekdays)
            if weekday not in evidence_weekdays
        )
        if time_evidence:
            for pattern, _ in _DATE_PATTERNS:
                dated_period = re.compile(
                    pattern.pattern
                    + r"(?P<period>上午|下午|早上|晚上|凌晨|中午|傍晚|深夜)"
                )
                for match in dated_period.finditer(compact_fact):
                    period = match.group("period")
                    if period not in text + time_evidence:
                        missing.append(period)
        return "、".join(dict.fromkeys(missing))

    @classmethod
    def _support_failure_reason(cls, fact: Any, rows: list[dict[str, Any]]) -> str:
        """断言是否被所引用消息支持："" = 支持，否则返回可读的失败原因。

        证据 = 消息正文（词语重合、极性、正文里提到的日期）+ 消息自身的时间戳
        （日期/周几/时段/钟点，按 Asia/Shanghai）。返回原因而不是布尔值，是为了让
        「自动纠正一次」拿到可执行的诊断。
        """
        raw_text = "；".join(clean_text(row.get("content"), 1000) for row in rows)
        text = re.sub(r"\s+", "", raw_text).casefold()
        if not text:
            return "所引用消息没有正文"
        compact_fact = re.sub(r"\s+", "", clean_text(fact, 300)).casefold()
        if len(compact_fact) < 2:
            return "断言内容过短"
        time_evidence = cls._rows_time_evidence(rows)
        source = text + time_evidence
        if cls._polarity_conflict(compact_fact, text):
            return "与所引用原文的否定状态不一致"
        if len(compact_fact) >= 4 and compact_fact in source:
            return ""
        mismatch = cls._time_claim_mismatch(compact_fact, text, time_evidence, rows)
        if mismatch:
            return f"提到的 {mismatch} 在所引用消息中找不到依据"
        # A generic word before a predicate is not a reliable Chinese subject:
        # the old matcher treated spans such as "过一次就够了" as names and
        # rejected otherwise grounded summaries. Compare only explicit common
        # name forms, then let the quote and claim terms establish support.
        subject_pattern = re.compile(r"(?:小|老|阿)[\u4e00-\u9fff]")
        fact_subjects = set(subject_pattern.findall(compact_fact))
        source_subjects = set(subject_pattern.findall(raw_text))
        if (
            fact_subjects
            and source_subjects
            and not fact_subjects.intersection(source_subjects)
        ):
            return "断言主体与引用消息不一致"
        generic_terms = {
            "事情",
            "内容",
            "消息",
            "聊天",
            "对话",
            "表示",
            "提到",
            "认为",
            "觉得",
            "用户",
            "对方",
            "某人",
            "某个",
            "相关",
            "已经",
            "还是",
            "然后",
            "这个",
            "那个",
        }
        terms = [
            term
            for term in message_terms(clean_text(fact, 300), limit=80)
            if term not in generic_terms
        ]
        matched = {term for term in terms if term in text}
        if len(matched) >= 2:
            return ""
        return "在所引用原文中找不到依据"

    def _normalize_key_facts_with_validation(
        self,
        value: Any,
        rows: list[dict[str, Any]],
        *,
        drop_reasons: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """Accept only evidence-backed fact objects with valid source event IDs.

        Returns the traced facts, soft warnings about what was dropped or
        downgraded, and contract failures the batch cannot be saved from.
        Inventing an event id is one of those: the model cited something that
        is not in the window, which no amount of rewording fixes.  A fact whose
        wording is merely unsupported is the opposite case -- it is dropped with
        a warning and the batch survives.
        """
        if isinstance(value, (str, dict)):
            value = [value]
        if not isinstance(value, list):
            return [], [], []

        row_by_id = {
            clean_text(row.get("id"), 160): row
            for row in rows
            if clean_text(row.get("id"), 160)
        }
        facts: list[str] = []
        traced: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        seen_facts: set[str] = set()
        for index, item in enumerate(value):
            if isinstance(item, dict):
                raw_fact = item.get("fact") or item.get("text") or item.get("content")
                raw_refs = (
                    item.get("refs")
                    or item.get("event_ids")
                    or item.get("source_event_ids")
                    or []
                )
            else:
                # A bare string carries no reference of its own. Attributing it
                # to the message it actually matches keeps both the content and
                # its provenance; dropping it would lose a fact the model did
                # extract, and rejecting the batch over it was fatal.
                raw_fact = item
                raw_refs = self._infer_fact_refs(clean_text(item, 160), row_by_id)
                if not raw_refs:
                    warnings.append("部分关键事实不受原文支持，已从本批事实中剔除")
                    continue
            fact = self._normalize_relative_time_mentions(
                self._sanitize_generated_memory_text(clean_text(raw_fact, 160), 160),
                rows,
            )
            if len(fact) < 2 or self._looks_like_prompt_injection(fact):
                continue
            if isinstance(raw_refs, str):
                raw_refs = [raw_refs]
            if not isinstance(raw_refs, list):
                continue
            refs = list(
                dict.fromkeys(
                    clean_text(ref, 160) for ref in raw_refs if clean_text(ref, 160)
                )
            )[:6]
            if not refs or any(ref not in row_by_id for ref in refs):
                if drop_reasons is not None:
                    drop_reasons.append(
                        f"第 {index + 1} 条关键事实的 refs 不是本批次存在的 event_id"
                    )
                errors.append(
                    "关键事实引用了本批次不存在的 event_id，请改用本批消息的真实 id"
                )
                continue
            evidence = self._key_fact_evidence(item, raw_fact, refs, row_by_id)
            if not evidence:
                if drop_reasons is not None:
                    drop_reasons.append(
                        f"第 {index + 1} 条关键事实没有可核验的原文引文"
                    )
                warnings.append("部分关键事实没有可核验的原文引文，已剔除")
                continue
            evidence_rows = [
                {**row_by_id[quote["ref"]], "content": quote["quote"]}
                for quote in evidence
            ]
            support_reason = self._support_failure_reason(fact, evidence_rows)
            verdict, _reason = self.citation_check(fact, evidence_rows)
            if support_reason or verdict == "unsupported":
                if drop_reasons is not None:
                    drop_reasons.append(
                        f"第 {index + 1} 条关键事实{support_reason or _reason}"
                    )
                warnings.append("部分关键事实不受原文支持，已剔除")
                continue
            if verdict == "conflicted":
                warnings.append(
                    "部分关键事实的肯定/否定表述与原文存在冲突，本批降级为待复核"
                )
            fact_key = fact.casefold()
            if fact_key in seen_facts:
                # A duplicate must not enter the traced list: validation_errors
                # compares len(key_facts_with_refs) with len(key_facts), so an
                # extra trace entry reports "关键事实缺少有效引用" for a batch
                # whose facts are all correctly referenced.
                continue
            seen_facts.add(fact_key)
            facts.append(fact)
            traced.append(
                {
                    "fact": fact,
                    "refs": list(dict.fromkeys(quote["ref"] for quote in evidence)),
                    "evidence": evidence,
                }
            )
            if len(facts) >= 8:
                break
        return traced[:8], list(dict.fromkeys(warnings)), list(dict.fromkeys(errors))

    def _key_fact_evidence(
        self,
        item: Any,
        raw_fact: Any,
        refs: list[str],
        row_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Resolve provider quotes or recover an excerpt from the cited source."""
        provided = item.get("evidence") if isinstance(item, dict) else None
        provided = [provided] if isinstance(provided, dict) else provided
        evidence: list[dict[str, str]] = []
        if provided:
            if not isinstance(provided, list):
                return []
            for quote_item in provided[:6]:
                if not isinstance(quote_item, dict):
                    return []
                ref = clean_text(
                    quote_item.get("ref") or quote_item.get("event_id"), 160
                )
                quote = self._sanitize_generated_memory_text(
                    clean_text(quote_item.get("quote"), 220), 220
                )
                if (
                    ref not in refs
                    or ref not in row_by_id
                    or not quote
                    or not self._quote_occurs_in_row(quote, row_by_id[ref])
                ):
                    return []
                evidence.append({"ref": ref, "quote": quote})
            return evidence
        raw = self._sanitize_generated_memory_text(clean_text(raw_fact, 160), 160)
        compact_raw = re.sub(r"\s+", "", raw).casefold()
        for ref in refs:
            row = row_by_id[ref]
            content = clean_text(row.get("content"), 4000)
            if not content:
                continue
            if compact_raw and compact_raw in re.sub(r"\s+", "", content).casefold():
                quote = raw
            elif len(content) <= 220:
                quote = content
            else:
                match = SequenceMatcher(
                    None, raw.casefold(), content.casefold(), autojunk=False
                ).find_longest_match()
                start = max(0, min(match.b - 60, len(content) - 220))
                quote = content[start : start + 220]
            evidence.append({"ref": ref, "quote": quote})
        return evidence
