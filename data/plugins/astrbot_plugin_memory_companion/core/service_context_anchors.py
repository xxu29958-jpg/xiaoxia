from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .models import (
    SessionContext,
    clean_text,
)
from .time_intent import TimeIntent
from .turn_signal import message_terms


class MemoryCompanionServiceContextAnchors:
    """service context anchors; the facade owns all shared resources."""

    def _should_expand_contextual_query(self, query: str, turn_signal: Any) -> bool:
        if self._message_has_explicit_memory_target(query):
            return False
        if self._looks_like_user_correction_text(
            query
        ) and not self._message_is_contextual_memory_request(query):
            return False
        if bool(
            getattr(turn_signal, "standalone_request", False)
        ) and not self._message_is_contextual_memory_request(query):
            return False
        # A small number of extracted anchors does not make a complete recall
        # question a follow-up. Keep its query intact; the reply model can use
        # the visible conversation to choose semantic follow-up searches.
        return bool(
            getattr(turn_signal, "low_information", False)
            or getattr(turn_signal, "context_dependent", False)
        )

    def _should_replace_current_query_with_anchors(
        self, query: str, turn_signal: Any, anchors: list[str]
    ) -> bool:
        compact = re.sub(r"\s+", "", clean_text(query, 500)).lower()
        # Keep the user's explicit memory intent in the retrieval query.  A
        # follow-up such as "还记得水蜜桃是什么情况吗" may be context-dependent,
        # but replacing it with recent dialogue anchors removes the recall
        # markers and reintroduces the multi-term keyword gate.
        if self._message_is_contextual_memory_request(compact):
            return False
        if bool(getattr(turn_signal, "low_information", False)):
            return True
        if bool(getattr(turn_signal, "context_dependent", False)) and (
            len(anchors) <= 2 or len(compact) <= 8
        ):
            return True
        return len(compact) <= 12 and len(anchors) <= 2

    def _recent_context_supports_memory_expansion(
        self,
        query: str,
        rows: list[dict[str, Any]],
        turn_signal: Any,
    ) -> bool:
        if self._message_is_contextual_memory_request(
            query
        ) or self._looks_like_user_correction_text(query):
            return True
        if not (
            bool(getattr(turn_signal, "low_information", False))
            or bool(getattr(turn_signal, "context_dependent", False))
        ):
            return False
        # Low-information follow-ups like "还有的吧" should only inherit a memory
        # search scene while that scene is still live in the newest turns.
        for row in (rows or [])[:3]:
            text = clean_text(row.get("content"), 600)
            if not text:
                continue
            speaker_is_user = (
                clean_text(row.get("event_type"), 40) != "bot_response"
                and clean_text(row.get("subject_id"), 80) != "self"
            )
            if speaker_is_user and self._message_is_contextual_memory_request(text):
                return True
            if speaker_is_user and self._looks_like_user_correction_text(text):
                return True
        return False

    def _contextual_query_anchors(
        self,
        query: str,
        rows: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[str]:
        current_terms = set(self._anchor_terms_for_text(query))
        scores: dict[str, float] = {}
        by_term_rows: dict[str, set[int]] = defaultdict(set)
        by_term_roles: dict[str, set[str]] = defaultdict(set)
        correction_terms: set[str] = set()
        memory_request_terms: set[str] = set()
        newest_support_index = 999
        relevant_indices = self._contextual_anchor_row_indices(rows)
        for index, row in enumerate(rows or []):
            if relevant_indices and index not in relevant_indices:
                continue
            text = clean_text(row.get("content"), 900)
            if not text:
                continue
            event_type = clean_text(row.get("event_type"), 40)
            speaker_is_user = (
                event_type != "bot_response"
                and clean_text(row.get("subject_id"), 80) != "self"
            )
            correction = speaker_is_user and self._looks_like_user_correction_text(text)
            memory_request = (
                speaker_is_user and self._message_is_contextual_memory_request(text)
            )
            if correction or memory_request:
                newest_support_index = min(newest_support_index, index)
            recency = 1.0 / (1.0 + index * 0.18)
            role_weight = 1.0 if speaker_is_user else 0.32
            if correction:
                role_weight *= 2.9
            elif memory_request and speaker_is_user:
                role_weight *= 1.45
            terms = self._anchor_terms_for_text(text)
            row_terms = set(terms)
            for term in row_terms:
                by_term_rows[term].add(index)
                by_term_roles[term].add("user" if speaker_is_user else "bot")
            if correction:
                correction_terms.update(row_terms)
            if memory_request:
                memory_request_terms.update(row_terms)
            for term in self._anchor_terms_for_text(text):
                term_role_weight = role_weight
                if not speaker_is_user and term not in current_terms:
                    term_role_weight *= 0.48
                quality = max(0.1, self._anchor_term_quality(term))
                score = quality * term_role_weight * recency
                if term in current_terms:
                    score += 1.8
                if correction:
                    score += 1.65
                elif memory_request:
                    score += 0.55
                scores[term] = scores.get(term, 0.0) + score
        if not scores:
            return []
        for term, row_set in by_term_rows.items():
            roles = by_term_roles.get(term, set())
            if "user" in roles and "bot" in roles:
                scores[term] = scores.get(term, 0.0) + 0.9
            if len(row_set) >= 2:
                scores[term] = scores.get(term, 0.0) + min(1.2, 0.35 * len(row_set))
            if term in correction_terms:
                scores[term] = scores.get(term, 0.0) + 1.35
            if term in memory_request_terms:
                scores[term] = scores.get(term, 0.0) + 0.45
            if (
                "bot" in roles
                and "user" not in roles
                and len(row_set) <= 1
                and term not in current_terms
            ):
                scores[term] = scores.get(term, 0.0) * 0.42
            if (
                newest_support_index != 999
                and min(row_set or {999}) > newest_support_index + 5
            ):
                scores[term] = scores.get(term, 0.0) * 0.65
        ordered = sorted(
            scores.items(),
            key=lambda item: (
                -item[1],
                -self._anchor_term_quality(item[0]),
                -len(item[0]),
                item[0],
            ),
        )
        selected: list[str] = []
        for term, score in ordered:
            if score < 0.75:
                continue
            if self._anchor_redundant_with_selected(term, selected):
                continue
            selected.append(term)
            if len(selected) >= max(1, int(limit or 1)):
                break
        return selected

    def _contextual_anchor_row_indices(self, rows: list[dict[str, Any]]) -> set[int]:
        support: set[int] = set()
        for index, row in enumerate(rows or []):
            text = clean_text(row.get("content"), 800)
            if not text:
                continue
            speaker_is_user = (
                clean_text(row.get("event_type"), 40) != "bot_response"
                and clean_text(row.get("subject_id"), 80) != "self"
            )
            if (
                speaker_is_user and self._message_is_contextual_memory_request(text)
            ) or (speaker_is_user and self._looks_like_user_correction_text(text)):
                support.add(index)
        if not support:
            return set()
        relevant: set[int] = set()
        for index in support:
            for nearby in range(max(0, index - 2), min(len(rows or []), index + 4)):
                relevant.add(nearby)
        return relevant

    def _anchor_terms_for_text(self, text: str) -> list[str]:
        normalized = clean_text(text, 900).lower()
        if not normalized:
            return []
        candidates: dict[str, float] = {}
        first_seen: dict[str, int] = {}
        order = 0

        def add(term: str, bonus: float = 0.0) -> None:
            nonlocal order
            cleaned = self._trim_anchor_boundaries(clean_text(term, 80).lower())
            if self._is_contextual_anchor_noise(cleaned):
                return
            quality = self._anchor_term_quality(cleaned) + bonus
            if quality <= 0.65:
                return
            if cleaned not in first_seen:
                first_seen[cleaned] = order
                order += 1
            candidates[cleaned] = max(candidates.get(cleaned, 0.0), quality)

        for token in re.findall(r"[a-z0-9_]{2,}", normalized):
            add(token, 0.2)

        compact = re.sub(
            r"[\s,，。.!！~～…、:：;；\"'“”‘’()（）\[\]【】<>《》]+", "", normalized
        )
        for block in re.findall(r"[\u4e00-\u9fff]+", compact):
            if len(block) <= 1:
                continue
            for segment in self._anchor_candidate_segments(block):
                if len(segment) <= 1:
                    continue
                if 2 <= len(segment) <= 6:
                    add(segment, 0.45 if len(segment) >= 3 else 0.0)
                max_size = min(6, len(segment))
                for size in range(max_size, 1, -1):
                    for index in range(0, len(segment) - size + 1):
                        chunk = segment[index : index + size]
                        add(chunk, 0.18 if size >= 3 else 0.0)

        if not candidates:
            return []
        frequency = Counter(
            self._trim_anchor_boundaries(term)
            for term in message_terms(text, limit=160)
        )
        scored = [
            (
                term,
                score + min(0.8, frequency.get(term, 0) * 0.18),
            )
            for term, score in candidates.items()
        ]
        scored.sort(
            key=lambda item: (
                -item[1],
                first_seen.get(item[0], 9999),
                -len(item[0]),
                item[0],
            )
        )

        selected: list[str] = []
        for term, _score in scored:
            if self._anchor_redundant_with_selected(term, selected):
                continue
            selected.append(term)
            if len(selected) >= 40:
                break
        return selected

    @staticmethod
    def _anchor_candidate_segments(block: str) -> list[str]:
        if not block:
            return []
        pieces = re.split(
            r"(?:为什么|怎么|什么|哪个|哪次|多少|是不是|有没有|就是|当时|之前|以前|上次|那次|刚才|"
            r"你再|再来|继续|接着|想想|想起|记得|回忆|明明|原来|其实|应该|不是|不对|错了|"
            r"我才|才说|说的是|说你是|说我是|叫你|叫我|问你|问我|告诉|直接|一直提|提起|提到|念叨|"
            r"不爱吃|爱吃|不吃|好吃|难吃|吃|喝|说|叫|问|想|"
            r"竟然|居然|突然|好像|可能|大概|"
            r"因为|所以|然后|但是|不过|如果|还是|只是|一个|这个|那个|这些|那些|"
            r"[我你他她它咱咱们我们你们他们她们它们的是了嘛吗呢吧呀哦啊嗯哈和与或在有把被给让对向从到地])",
            block,
        )
        result = [piece for piece in pieces if piece]
        return result or [block]

    @staticmethod
    def _trim_anchor_boundaries(term: str) -> str:
        text = clean_text(term, 80).lower()
        if not text:
            return ""
        leading_units = (
            "为什么",
            "怎么",
            "什么",
            "就是",
            "当时",
            "之前",
            "以前",
            "上次",
            "那次",
            "刚才",
            "明明",
            "原来",
            "其实",
            "应该",
            "不是",
            "不对",
            "错了",
            "因为",
            "所以",
            "然后",
            "但是",
            "不过",
            "如果",
            "一直",
            "提起",
            "提到",
            "念叨",
            "竟然",
            "居然",
            "突然",
            "好像",
            "可能",
            "大概",
            "不爱吃",
            "喜欢",
            "爱吃",
            "好吃",
            "不爱",
            "不吃",
            "吃",
            "喝",
            "说你",
            "说我",
            "你是",
            "我是",
        )
        trailing_units = (
            "为什么",
            "怎么",
            "什么",
            "就是",
            "当时",
            "之前",
            "以前",
            "上次",
            "那次",
            "刚才",
            "因为",
            "所以",
            "然后",
            "但是",
            "不过",
            "如果",
            "一直",
            "提起",
            "提到",
            "念叨",
            "竟然",
            "居然",
            "突然",
            "好像",
            "可能",
            "大概",
            "不爱吃",
            "喜欢",
            "爱吃",
            "好吃",
            "难吃",
            "不爱",
            "不吃",
            "吃",
            "喝",
            "说你",
            "说我",
            "你是",
            "我是",
        )
        changed = True
        while changed and len(text) > 1:
            changed = False
            for unit in leading_units:
                if text.startswith(unit) and len(text) > len(unit) + 1:
                    text = text[len(unit) :]
                    changed = True
                    break
            if changed:
                continue
            for unit in trailing_units:
                if text.endswith(unit) and len(text) > len(unit) + 1:
                    text = text[: -len(unit)]
                    changed = True
                    break
        edge_chars = "的是了嘛吗呢吧呀哦啊嗯哈我你他她它这那才又还再就和与或但把被给让在对向从到为问说叫想地"
        return text.strip(edge_chars)

    @staticmethod
    def _anchor_term_quality(term: str) -> float:
        if not term:
            return 0.0
        score = 1.0 + min(2.1, len(term) * 0.2)
        if re.fullmatch(r"[a-z_]+", term) and len(term) <= 2:
            score -= 1.0
        if len(term) >= 4:
            score += 0.75
        if len(term) >= 7:
            score -= 0.45
        edge_chars = "的是了嘛吗呢吧呀哦啊嗯哈我你他她它这那才又还再就和与或但把被给让在对向从到为问说叫想地"
        if term[0] in edge_chars:
            score -= 1.1
        if term[-1] in edge_chars:
            score -= 0.8
        scaffold_hits = len(
            re.findall(
                r"为什么|怎么|什么|说你|说我|你是|我是|才说|一直提|记得|想想|还有|继续|直接|告诉|"
                r"竟然|居然|突然|好像|可能|大概",
                term,
            )
        )
        if scaffold_hits:
            score -= 1.15 * scaffold_hits
        if len(term) >= 3 and not re.search(
            r"[的是了嘛吗呢吧呀哦啊嗯哈我你他她它这那才又还再就和与或但把被给让在对向从到为问说叫想]",
            term,
        ):
            score += 0.45
        if len(set(term)) <= 1:
            score -= 1.0
        return score

    @staticmethod
    def _looks_like_user_correction_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 500)).lower()
        if not compact:
            return False
        correction_markers = (
            "不是",
            "不对",
            "错了",
            "明明",
            "其实是",
            "应该是",
            "原来是",
            "才是",
            "说的是",
        )
        if any(marker in compact for marker in correction_markers):
            return True
        return compact.startswith("是") and 3 <= len(compact) <= 14

    @staticmethod
    def _is_contextual_anchor_noise(term: str) -> bool:
        if not term or len(term) < 2:
            return True
        if re.fullmatch(r"[的是了嘛吗呢吧呀哦啊嗯哈]+", term):
            return True
        if re.fullmatch(
            r"(?:就是|当时|什么|为什么|怎么|继续|还有|有的|想想|明明|原来|其实|不是|不对|应该|因为|所以|"
            r"这个|那个|这些|那些|直接|告诉|一直|用户|当前|消息|回复|记得|之前|以前|上次|那次|刚才|现在|真的|感觉|"
            r"竟然|居然|突然|好像|可能|大概|"
            r"还有的|有的吧|还有的吧)+",
            term,
        ):
            return True
        if (
            len(term) == 2
            and term[0] in "我是你他她它这那"
            and term[1] in "的是在有说叫想问才"
        ):
            return True
        if re.fullmatch(
            r"(?:为什|什么|么说|说你|说我|你是|我是|才说|直提|提什|提什么|么我|我才)+",
            term,
        ):
            return True
        return False

    def _anchor_redundant_with_selected(self, term: str, selected: list[str]) -> bool:
        quality = self._anchor_term_quality(term)
        for kept in selected:
            if term == kept:
                return True
            kept_quality = self._anchor_term_quality(kept)
            if term in kept:
                if len(term) == 2 and quality >= kept_quality - 0.25:
                    continue
                return True
            if kept in term:
                if quality <= kept_quality + 0.35 or self._bad_anchor_boundary(term):
                    return True
        return False

    @staticmethod
    def _bad_anchor_boundary(term: str) -> bool:
        edge_chars = "的是了嘛吗呢吧呀哦啊嗯哈我你他她它这那才又还再就和与或但把被给让在对向从到为问说叫想地"
        return bool(term) and (term[0] in edge_chars or term[-1] in edge_chars)

    def _query_focuses_self_timeline(
        self,
        query: str,
        *,
        time_intent: TimeIntent | None = None,
        bot_id: str = "",
    ) -> bool:
        compact = re.sub(r"\s+", "", clean_text(query, 1000)).lower()
        if not compact:
            return False
        if self._message_is_future_arrangement_question(compact, bot_id=bot_id):
            return True
        return self._message_targets_bot_personal_schedule(
            compact,
            bot_id=bot_id,
            allow_implicit_temporal=bool(
                time_intent is not None and time_intent.active
            ),
        )

    def _short_reply_chain_anchor(self, ctx: SessionContext, event: Any) -> str:
        if ctx.scope != "group" or event is None:
            return ""
        current = clean_text(ctx.message_text, 180)
        compact = re.sub(r"\s+", "", current)
        if not compact or len(compact) > 32:
            return ""
        if self._message_requests_memory_context(
            current
        ) or self._message_requests_temporal_aggregate(current):
            return ""
        chain = self.reply_chain.cached_chain_for_event(event)
        if not chain:
            return ""
        direct = next(
            (
                row
                for row in chain
                if int(row.get("depth") or 1) == 1 and clean_text(row.get("text"), 360)
            ),
            None,
        )
        if direct is None:
            return ""
        quoted = clean_text(direct.get("text"), 360)
        return clean_text(f"{current} {quoted}", 520) if quoted else ""

    def _apply_short_reply_chain_policy(
        self,
        ctx: SessionContext,
        event: Any,
        slot_map: dict[str, list[Any]],
        blocked: list[dict[str, Any]],
        retrieval_path_info: dict[str, Any],
    ) -> tuple[str, dict[str, list[Any]]]:
        anchor = self._short_reply_chain_anchor(ctx, event)
        if not anchor:
            return "", slot_map
        filtered = self._filter_short_reply_chain_slots(slot_map, anchor, blocked)
        retrieval_path_info["short_reply_chain"] = True
        retrieval_path_info["short_reply_selected"] = len(
            self._flatten_slot_map(filtered)
        )
        return anchor, filtered

    @staticmethod
    def _short_reply_chain_guard() -> str:
        return (
            "- 当前是短引用接话：优先依据本轮用户文字和直接引用，旧记忆只补充少量连续性。\n"
            "- 人物、动作和地点尽量按原句对应；除非上下文明确指向同一对象，不要把引用中信使、第三方或旧记忆里的动作顺手换到另一个人身上。"
        )

    def _filter_short_reply_chain_slots(
        self,
        slot_map: dict[str, list[Any]],
        anchor_text: str,
        blocked: list[dict[str, Any]],
    ) -> dict[str, list[Any]]:
        ignored_terms = {
            "给你",
            "让你",
            "这个",
            "那个",
            "怎么",
            "什么",
            "可以",
            "不是",
            "就是",
            "一下",
        }
        anchors = {
            term
            for term in message_terms(anchor_text, limit=60)
            if term not in ignored_terms
        }
        if not anchors:
            return {}
        slot_limits = {
            "open_loop": 1,
            "self_timeline": 1,
            "conversation_summary": 1,
            "current_window": 1,
        }
        for slot, items in slot_map.items():
            if slot in slot_limits:
                continue
            for item in items or []:
                memory = getattr(item, "memory", None)
                blocked.append(
                    {
                        "id": clean_text(getattr(memory, "id", ""), 120),
                        "reason": f"short_reply_slot_suppressed:{clean_text(slot, 60)}",
                        "content": clean_text(getattr(memory, "content", ""), 180),
                    }
                )
        filtered: dict[str, list[Any]] = {}
        total = 0
        for slot in (
            "open_loop",
            "self_timeline",
            "conversation_summary",
            "current_window",
        ):
            kept: list[Any] = []
            for item in slot_map.get(slot) or []:
                memory = getattr(item, "memory", None)
                if memory is None:
                    continue
                if total >= 3:
                    blocked.append(
                        {
                            "id": clean_text(memory.id, 120),
                            "reason": "short_reply_global_limit",
                            "content": clean_text(memory.content, 180),
                        }
                    )
                    continue
                if len(kept) >= slot_limits[slot]:
                    blocked.append(
                        {
                            "id": clean_text(memory.id, 120),
                            "reason": f"short_reply_slot_limit:{slot}",
                            "content": clean_text(memory.content, 180),
                        }
                    )
                    continue
                metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
                memory_text = " ".join(
                    [
                        clean_text(memory.content, 2200),
                        clean_text(memory.evidence, 1000),
                        " ".join(clean_text(tag, 80) for tag in (memory.tags or [])),
                        clean_text(metadata.get("canonical_summary"), 1000),
                    ]
                )
                memory_terms = set(message_terms(memory_text, limit=120))
                if not anchors.intersection(memory_terms):
                    blocked.append(
                        {
                            "id": clean_text(memory.id, 120),
                            "reason": "short_reply_quote_topic_mismatch",
                            "content": clean_text(memory.content, 180),
                        }
                    )
                    continue
                kept.append(item)
                total += 1
            if kept:
                filtered[slot] = kept
        return filtered
