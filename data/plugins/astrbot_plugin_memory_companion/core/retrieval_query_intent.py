from __future__ import annotations

import re

from .models import (
    clean_text,
)


class RetrievalEngineQueryIntent:
    """Query intent; shared runtime resources belong to the facade."""

    def _terms(self, query: str) -> list[str]:
        words = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", query)
        terms: list[str] = []
        for word in words:
            contextual_word = self._looks_like_contextual_recall_query(word)
            trimmed_word = self._trim_query_scaffold_phrase(word)
            term_word = (
                trimmed_word
                if (contextual_word or trimmed_word != clean_text(word, 120))
                else word
            )
            if not term_word:
                continue
            term_word = term_word or word
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", term_word):
                terms.extend(term_word[i : i + 2] for i in range(0, len(term_word) - 1))
                if not contextual_word or len(term_word) <= 4:
                    terms.append(term_word)
                continue
            terms.append(term_word)
            terms.extend(self._current_state_anchor_terms(term_word))
        normalized_terms = [
            term
            for term in dict.fromkeys(
                term.lower() for term in terms if len(term.strip()) >= 2
            )
            if not self._is_query_scaffold_term(term)
        ]
        normalized_terms.extend(
            term
            for term in self._preference_equivalent_terms(query)
            if term not in normalized_terms
        )
        return normalized_terms[:24]

    @staticmethod
    def _preference_equivalent_terms(text: str) -> list[str]:
        compact = re.sub(r"\s+", "", clean_text(text, 1000)).lower()
        if not compact:
            return []
        families = (
            ("无糖", "不加糖", "不要加糖", "不放糖"),
            ("少糖", "低糖", "微糖"),
            ("无冰", "去冰", "不加冰", "不要冰"),
            ("不要香菜", "不吃香菜", "去香菜"),
        )
        expanded: list[str] = []
        for family in families:
            if any(term in compact for term in family):
                expanded.extend(term for term in family if term not in expanded)
        return expanded

    def _query_profile(self, query: str, terms: list[str]) -> dict[str, object]:
        compact = re.sub(r"\s+", "", query).lower()
        contextual_recall = self._looks_like_contextual_recall_query(compact)
        temporal_aggregate = self._looks_like_temporal_aggregate_query(compact)
        current_state = self._looks_like_current_state_query(compact)
        cjk_phrases = re.findall(r"[\u4e00-\u9fff]{4,}", compact)
        exact_phrases = (
            []
            if (contextual_recall or temporal_aggregate or current_state)
            else [phrase for phrase in cjk_phrases if len(phrase) >= 4]
        )
        # Long concrete Chinese phrases should not match by relation/recency alone.
        # Require at least two overlapping fragments unless the whole phrase appears.
        min_hits = 1
        if current_state:
            min_hits = 1
        elif temporal_aggregate:
            min_hits = min(2, max(1, len(terms)))
        elif contextual_recall:
            # Contextual follow-ups often contain mostly scaffolding words
            # ("之前", "那个", "为什么"). One concrete overlap is enough to
            # keep a candidate alive; ranking and ACL/lifecycle checks still
            # decide whether it is actually returned.
            min_hits = 1
        elif exact_phrases:
            min_hits = min(3, max(2, len(exact_phrases[0]) // 3))
        elif len(terms) >= 4:
            min_hits = 2
        # relax：把剩余分支的 min_hits 统一降到 1（默认关闭时行为与历史版本一致）。
        if self.relax_keyword_min_hits:
            min_hits = min(min_hits, 1)
        return {
            "query_text": compact,
            "exact_phrases": exact_phrases,
            "min_hits": min_hits,
            "contextual_recall": contextual_recall,
            "temporal_aggregate": temporal_aggregate,
            "open_loop_followup": self._looks_like_open_loop_followup(compact),
        }

    @staticmethod
    def _looks_like_current_state_query(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 1000)).lower()
        if not compact:
            return False
        if any(
            marker in compact
            for marker in ("记得", "记忆", "之前", "以前", "上次", "那次")
        ):
            return False
        markers = (
            "吃饭",
            "吃了",
            "吃晚饭",
            "晚饭",
            "午饭",
            "早餐",
            "夜宵",
            "喝了",
            "在干嘛",
            "在做什么",
            "忙什么",
            "累不累",
            "困不困",
            "饿不饿",
            "心情",
            "状态",
            "穿什么",
            "穿了什么",
            "衣服颜色",
            "什么颜色",
            "什么色",
        )
        question_markers = (
            "吗",
            "呢",
            "了没",
            "了吗",
            "没有",
            "什么",
            "啥",
            "怎么样",
            "如何",
        )
        return any(marker in compact for marker in markers) and any(
            marker in compact for marker in question_markers
        )

    @staticmethod
    def _current_state_anchor_terms(text: str) -> list[str]:
        compact = re.sub(r"\s+", "", clean_text(text, 1000)).lower()
        terms: list[str] = []
        for meal in ("早餐", "早饭", "午饭", "午餐", "晚饭", "晚餐", "夜宵"):
            if meal in compact:
                terms.append(meal)
        if not terms and any(marker in compact for marker in ("吃", "饭", "喝")):
            terms.extend(["吃饭", "吃过", "吃了", "用餐", "喝了"])
        if any(marker in compact for marker in ("干嘛", "做什么", "忙什么")):
            terms.extend(["做", "忙", "上课", "学习", "工作", "玩"])
        if any(marker in compact for marker in ("累", "困", "饿", "心情", "状态")):
            terms.extend(["累", "困", "饿", "心情", "状态", "感觉"])
        if any(
            marker in compact
            for marker in ("穿", "衣服", "穿搭", "裙", "外套", "裤", "颜色", "什么色")
        ):
            terms.extend(
                [
                    "穿",
                    "衣服",
                    "穿搭",
                    "今日穿搭",
                    "每日穿搭",
                    "衣服颜色",
                    "颜色",
                    "裙",
                    "外套",
                    "裤",
                ]
            )
        return list(dict.fromkeys(term for term in terms if len(term) >= 2))

    @staticmethod
    def _looks_like_temporal_aggregate_query(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 1000)).lower()
        if not compact:
            return False
        temporal_markers = (
            "最近一周",
            "近一周",
            "这一周",
            "这周",
            "本周",
            "最近几天",
            "这几天",
            "近几天",
            "最近7天",
            "最近七天",
            "过去一周",
            "过去7天",
            "过去七天",
        )
        return any(marker in compact for marker in temporal_markers) or bool(
            re.search(r"(最近|过去|近)\d{1,2}天", compact)
        )

    @staticmethod
    def _looks_like_contextual_recall_query(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 1000)).lower()
        if not compact:
            return False
        direct_recall_patterns = (
            "为什么说你是",
            "为什么说我是",
            "为什么叫你",
            "为什么叫我",
            "才说你是",
            "才说我是",
            "才叫你",
            "才叫我",
        )
        if any(pattern in compact for pattern in direct_recall_patterns):
            return True
        if any(
            marker in compact for marker in ("当时", "之前", "以前", "上次", "那次")
        ) and any(
            marker in compact
            for marker in ("说你是", "说我是", "叫你", "叫我", "一直提什么")
        ):
            return True
        recall_markers = (
            "记得",
            "还记得",
            "想起来",
            "想起",
            "想想",
            "再想想",
            "想一下",
            "想一想",
            "之前",
            "以前",
            "上次",
            "当时",
            "那次",
        )
        question_markers = (
            "为什么",
            "原因",
            "怎么",
            "哪",
            "啥",
            "什么",
            "说你",
            "说我",
            "叫你",
            "叫我",
            "吗",
            "呢",
        )
        return any(marker in compact for marker in recall_markers) and any(
            marker in compact for marker in question_markers
        )

    @staticmethod
    def _looks_like_open_loop_followup(text: str) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 1000)).lower()
        if not compact:
            return False
        markers = (
            "继续",
            "接着",
            "还有呢",
            "还有吗",
            "还有的吧",
            "还有什么",
            "后来呢",
            "然后呢",
            "刚才那个",
            "上次那个",
            "没说完",
            "还没说完",
        )
        return any(marker in compact for marker in markers) and len(compact) <= 18

    @staticmethod
    def _is_query_scaffold_term(term: str) -> bool:
        scaffold_terms = {
            "你再",
            "再想",
            "想想",
            "想为",
            "想一",
            "一下",
            "你想",
            "你还",
            "就是",
            "是当",
            "时你",
            "你一",
            "一直",
            "直提",
            "提什",
            "我才",
            "才说",
            "记得",
            "还记",
            "得我",
            "得你",
            "得糖",
            "我为",
            "你为",
            "为什",
            "什么",
            "么说",
            "说你",
            "说我",
            "叫你",
            "叫我",
            "你是",
            "我是",
            "是异",
            "的吗",
            "端吗",
            "骨吗",
            "原因",
            "之前",
            "以前",
            "上次",
            "当时",
            "那次",
            "想起",
            "起来",
            "最近",
            "近一",
            "一周",
            "周的",
            "过去",
            "几天",
            "这几",
            "本周",
            "这周",
            "的胖",
            "次颜",
        }
        return clean_text(term, 80).lower() in scaffold_terms
