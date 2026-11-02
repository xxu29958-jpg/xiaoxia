from __future__ import annotations

import re

from .models import (
    clean_text,
)


class RetrievalEngineQueryTerms:
    """Query terms; shared runtime resources belong to the facade."""

    def _keyword_candidate_terms(self, query: str, terms: list[str]) -> list[str]:
        keyword_terms = list(terms)
        compact = re.sub(r"\s+", "", clean_text(query, 1000)).lower()
        for phrase in self._concrete_exact_phrases(compact):
            if phrase not in keyword_terms:
                keyword_terms.append(phrase)
        return keyword_terms[:24]

    def _concrete_exact_phrases(self, compact_query: str) -> list[str]:
        phrases: list[str] = []
        for phrase in re.findall(r"[\u4e00-\u9fff]{3,}", compact_query):
            trimmed = self._trim_query_scaffold_phrase(phrase)
            if len(trimmed) >= 3 and trimmed not in phrases:
                phrases.append(trimmed)
            if len(phrases) >= 8:
                break
        return phrases

    def _knowledge_graph_anchor_terms(
        self,
        labels: list[str],
        *,
        seed_terms: list[str],
        limit: int,
    ) -> list[str]:
        seed = {
            clean_text(term, 80).lower() for term in seed_terms if clean_text(term, 80)
        }
        candidates: dict[str, float] = {}
        first_seen: dict[str, int] = {}
        order = 0

        def add(raw: str, bonus: float = 0.0) -> None:
            nonlocal order
            term = self._trim_graph_anchor(raw)
            if self._is_graph_anchor_noise(term):
                return
            score = self._graph_anchor_quality(term) + bonus
            if term in seed:
                score += 0.8
            if score <= 0.65:
                return
            if term not in first_seen:
                first_seen[term] = order
                order += 1
            candidates[term] = max(candidates.get(term, 0.0), score)

        for label in labels or []:
            text = clean_text(label, 240).lower()
            if not text:
                continue
            for quoted in re.findall(
                r"[\"'“”‘’「」『』]([^\"'“”‘’「」『』]{2,16})[\"'“”‘’「」『』]", text
            ):
                add(quoted, 0.8)
            for token in re.findall(r"[a-z0-9_]{3,}", text):
                add(token, 0.2)
            compact = re.sub(
                r"[\s,，。.!！~～…、:：;；\"'“”‘’()（）\[\]【】<>《》]+", "", text
            )
            for block in re.findall(r"[\u4e00-\u9fff]+", compact):
                for segment in self._graph_anchor_segments(block):
                    if 2 <= len(segment) <= 8:
                        add(segment, 0.45 if len(segment) >= 3 else 0.0)
                    for size in range(min(6, len(segment)), 1, -1):
                        for index in range(0, len(segment) - size + 1):
                            add(
                                segment[index : index + size],
                                0.15 if size >= 3 else 0.0,
                            )

        if not candidates:
            return []
        ordered = sorted(
            candidates.items(),
            key=lambda item: (
                -item[1],
                first_seen.get(item[0], 9999),
                -len(item[0]),
                item[0],
            ),
        )
        selected: list[str] = []
        for term, _score in ordered:
            if self._graph_anchor_redundant(term, selected):
                continue
            selected.append(term)
            if len(selected) >= max(1, int(limit or 1)):
                break
        return selected

    @staticmethod
    def _graph_anchor_segments(block: str) -> list[str]:
        pieces = re.split(
            r"(?:为什么|怎么|什么|哪个|哪次|是否|是不是|有没有|称呼|叫做|起因|原因|因为|所以|"
            r"不爱吃|爱吃|不吃|好吃|难吃|吃|喝|提到|提起|念叨|关于|相关|属于|"
            r"当前|记忆|总结|阶段|会话|用户|消息|回复|来源|可见性|现实层|置信|绰号|"
            r"[我你他她它咱咱们我们你们他们她们它们的是了嘛吗呢吧呀哦啊嗯哈和与或在有把被给让对向从到地为])",
            block,
        )
        return [piece for piece in pieces if piece]

    @staticmethod
    def _trim_graph_anchor(term: str) -> str:
        text = clean_text(term, 80).lower()
        if not text:
            return ""
        leading_units = (
            "为什么",
            "怎么",
            "什么",
            "称呼",
            "叫做",
            "起因",
            "原因",
            "因为",
            "所以",
            "关于",
            "相关",
            "不爱吃",
            "爱吃",
            "不吃",
            "好吃",
            "难吃",
            "吃",
            "喝",
            "提到",
            "提起",
            "念叨",
            "当前",
            "记忆",
            "总结",
            "阶段",
            "会话",
            "用户",
            "消息",
            "回复",
            "绰号",
        )
        trailing_units = leading_units
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
    def _graph_anchor_quality(term: str) -> float:
        if not term:
            return 0.0
        score = 1.0 + min(2.0, len(term) * 0.18)
        if re.fullmatch(r"[a-z_]+", term) and len(term) <= 2:
            score -= 1.0
        if len(term) >= 4:
            score += 0.65
        if len(term) >= 7:
            score -= 0.55
        edge_chars = "的是了嘛吗呢吧呀哦啊嗯哈我你他她它这那才又还再就和与或但把被给让在对向从到为问说叫想地"
        if term[0] in edge_chars:
            score -= 1.0
        if term[-1] in edge_chars:
            score -= 0.7
        if re.search(
            r"为什么|怎么|什么|称呼|起因|原因|因为|所以|当前|记忆|总结|会话|用户|消息|回复",
            term,
        ):
            score -= 1.2
        if len(term) >= 3 and not re.search(
            r"[的是了嘛吗呢吧呀哦啊嗯哈我你他她它这那才又还再就和与或但把被给让在对向从到为问说叫想地]",
            term,
        ):
            score += 0.35
        if len(set(term)) <= 1:
            score -= 1.0
        return score

    @staticmethod
    def _is_graph_anchor_noise(term: str) -> bool:
        if not term or len(term) < 2:
            return True
        if re.fullmatch(r"[的是了嘛吗呢吧呀哦啊嗯哈]+", term):
            return True
        if re.fullmatch(
            r"(?:为什么|怎么|什么|称呼|叫做|起因|原因|因为|所以|关于|相关|当前|记忆|总结|阶段|会话|用户|消息|回复|"
            r"来源|可见性|现实层|置信|绰号|id|bot)+",
            term,
        ):
            return True
        return False

    def _graph_anchor_redundant(self, term: str, selected: list[str]) -> bool:
        quality = self._graph_anchor_quality(term)
        for kept in selected:
            if term == kept:
                return True
            kept_quality = self._graph_anchor_quality(kept)
            if term in kept:
                if len(term) == 2 and quality >= kept_quality - 0.25:
                    continue
                return True
            if kept in term and quality <= kept_quality + 0.35:
                return True
        return False

    @staticmethod
    def _trim_query_scaffold_phrase(phrase: str) -> str:
        text = clean_text(phrase, 120)
        prefixes = (
            "就是当时你一直提什么我才说你是",
            "就是当时你一直提什么我才说我是",
            "当时你一直提什么我才说你是",
            "当时你一直提什么我才说我是",
            "就是当时你一直提什么才说你是",
            "就是当时你一直提什么才说我是",
            "当时你一直提什么才说你是",
            "当时你一直提什么才说我是",
            "就是当时你一直提什么",
            "当时你一直提什么",
            "我才说你是",
            "我才说我是",
            "才说你是",
            "才说我是",
            "你再想想为什么说你是",
            "你再想想为什么说我是",
            "再想想为什么说你是",
            "再想想为什么说我是",
            "你想想为什么说你是",
            "你想想为什么说我是",
            "想想为什么说你是",
            "想想为什么说我是",
            "你再想一下为什么说你是",
            "你再想一下为什么说我是",
            "再想一下为什么说你是",
            "再想一下为什么说我是",
            "还记得我为什么说你是",
            "还记得我为什么说我是",
            "记得我为什么说你是",
            "记得我为什么说我是",
            "为什么说你是",
            "为什么说我是",
            "为什么叫你",
            "为什么叫我",
            "你再想想",
            "再想想",
            "你想想",
            "想想",
            "你再想一下",
            "再想一下",
            "你想一下",
            "想一下",
            "你想一想",
            "想一想",
            "你还记得",
            "我还记得",
            "还记得",
            "记得",
            "明明是",
            "就是",
            "原来是",
            "是",
        )
        suffixes = ("是什么", "是啥", "了吗", "吗", "呢", "啊", "呀")
        changed = True
        while changed and text:
            changed = False
            for prefix in prefixes:
                if text == prefix:
                    text = ""
                    changed = True
                    break
                if text.startswith(prefix) and len(text) > len(prefix):
                    text = text[len(prefix) :]
                    changed = True
                    break
            if changed:
                continue
            for suffix in suffixes:
                if text.endswith(suffix) and len(text) > len(suffix):
                    text = text[: -len(suffix)]
                    changed = True
                    break
        return text
