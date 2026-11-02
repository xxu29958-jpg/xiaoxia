from __future__ import annotations

import re

from .models import (
    MemoryRecord,
    SearchResult,
    SessionContext,
    clean_text,
    is_memory_placeholder,
)
from .profile_quality import PROFILE_MEMORY_TYPES, profile_quality_decision


class RetrievalEngineProfilePolicy:
    """Profile policy; shared runtime resources belong to the facade."""

    def _search_visibility_reason(
        self,
        memory: MemoryRecord,
        ctx: SessionContext,
        acl_state: dict[str, object],
        *,
        include_core_memory: bool = False,
    ) -> tuple[str, str]:
        if is_memory_placeholder(memory):
            return "", "placeholder_memory"
        is_core = (
            memory.memory_type == "core_memory"
            or (memory.metadata or {}).get("core_memory") is True
        )
        quality_allowed, quality_reason = (
            (True, "static_core_memory")
            if include_core_memory and is_core
            else self._profile_retrieval_decision(memory)
        )
        if not quality_allowed:
            return "", quality_reason
        visible, visibility_reason = self.policy.is_visible(memory, ctx)
        acl_deny_reason = self._acl_deny_reason(memory, ctx, acl_state)
        if visible:
            if acl_deny_reason:
                return "", acl_deny_reason
            return visibility_reason, ""
        if acl_deny_reason:
            return "", acl_deny_reason
        acl_reason = self._acl_visibility_reason(
            memory, ctx, visibility_reason, acl_state
        )
        if acl_reason:
            return acl_reason, ""
        privacy_reason = self._acl_privacy_guard_reason(
            memory, ctx, visibility_reason, acl_state
        )
        return "", privacy_reason or visibility_reason

    @staticmethod
    def _profile_retrieval_decision(memory: MemoryRecord) -> tuple[bool, str]:
        """Keep inactive and low-quality profile candidates out of every route."""
        memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        if memory_type == "core_memory" or metadata.get("core_memory") is True:
            return False, "static_core_memory"
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
        allowed, reason = profile_quality_decision(memory, require_active=True)
        if not allowed:
            return False, reason
        return True, "profile_quality_compatible"

    def _query_allows_profile_fallback(self, query: str) -> bool:
        """Reserve a profile slot without lexical evidence only for profile intent."""
        text = clean_text(query, 1000)
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", text).lower()
        return bool(
            compact
            and not self._looks_like_current_task_query(compact)
            and self._looks_like_explicit_profile_query(text)
        )

    def _query_blocks_profile_retrieval(self, query: str) -> bool:
        """Block portrait retrieval for greetings, current state, and empty queries."""
        text = clean_text(query, 1000)
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", text).lower()
        if not compact:
            return True
        if self._looks_like_current_state_query(compact):
            return True
        if self._query_allows_profile_fallback(text):
            return False
        greetings = {
            "你好",
            "您好",
            "嗨",
            "哈喽",
            "hello",
            "hi",
            "早",
            "早安",
            "早上好",
            "午安",
            "晚上好",
            "晚安",
            "在吗",
            "在不在",
            "最近好吗",
            "你好吗",
        }
        greeting_base = re.sub(r"(?:呀|啊|哈|哦|喔|哟|啦|呢|诶|欸)+$", "", compact)
        if compact in greetings or greeting_base in greetings:
            return True
        return not bool(self._terms(text))

    def _profile_result_has_relevance(self, item: SearchResult) -> bool:
        reason = clean_text(getattr(item, "reason", ""), 1600).lower()
        if re.search(r"(?:^|;)(?:exact|profile)=1(?:;|$)", reason):
            return True
        hits = re.search(r"(?:^|;)hits=(\d+)", reason)
        if hits and int(hits.group(1)) > 0:
            return True
        vector = re.search(r"(?:^|;)vector=([0-9.]+)", reason)
        if vector:
            try:
                return float(vector.group(1)) >= self.embedding_score_threshold
            except ValueError:
                return False
        return False

    @staticmethod
    def _looks_like_explicit_profile_query(text: str) -> bool:
        source = clean_text(text, 1000).lower()
        compact = re.sub(r"\s+", "", source)
        if not compact:
            return False
        chinese_markers = (
            "我的称呼",
            "称呼我",
            "怎么叫我",
            "如何叫我",
            "该叫我",
            "喊我什么",
            "我叫什么",
            "我的名字",
            "我的姓名",
            "我的昵称",
            "我的职业",
            "我做什么工作",
            "我从事什么工作",
            "我是做什么的",
            "我的生日",
            "我的出生",
            "我的偏好",
            "我喜欢什么",
            "我讨厌什么",
            "我的爱好",
            "我的习惯",
            "我的专业",
            "我的学业",
            "我的星座",
            "我的血型",
            "我的禁忌",
            "我的忌口",
            "我对什么过敏",
            "我不能吃什么",
            "我的沟通习惯",
            "我的边界",
            "我的雷区",
            "我不想聊什么",
            "我的资料",
            "我的画像",
        )
        english_patterns = (
            r"\bmy\s+(?:name|nickname|birthday|occupation|job|profession|"
            r"preferences?|favorites?|favourites?|habits?|profile|major|"
            r"education|zodiac|blood\s+type|allerg(?:y|ies)|boundary)\b",
            r"\bcall\s+me\b",
            r"\babout\s+me\b",
            r"\b(?:myname|mynickname|mybirthday|myoccupation|myjob|"
            r"myprofession|mypreference|myfavorite|myfavourite|myhabit|"
            r"myprofile|mymajor|myeducation|myzodiac|mybloodtype|"
            r"myallergy|myboundary|callme|aboutme)\b",
        )
        if any(marker in compact for marker in chinese_markers) or any(
            re.search(pattern, source) for pattern in english_patterns
        ):
            return True
        occupation_patterns = (
            r"(?:你(?:还)?记得)?我的(?:工作岗位|职位|职务)(?:是)?(?:什么|啥|哪(?:一)?个)?",
            r"(?:你(?:还)?记得)?我(?:现在|目前)?是做什么工作的?",
        )
        general_profile_patterns = (
            r"关于我的(?:资料|画像|个人信息|基本信息)",
            r"(?:关于我|你(?:还)?记得我)(?:你)?(?:记得|知道|了解)(?:什么|哪些)",
        )
        return any(
            re.search(pattern, compact)
            for pattern in (*occupation_patterns, *general_profile_patterns)
        )

    @staticmethod
    def _looks_like_current_task_query(text: str) -> bool:
        """Identify task/project context that must not be treated as a profile query."""
        compact = re.sub(r"\s+", "", clean_text(text, 1000)).lower()
        if not compact:
            return False
        explicit_task_markers = (
            "工作安排",
            "工作进度",
            "工作计划",
            "工作任务",
            "项目安排",
            "项目进度",
            "项目计划",
            "项目任务",
            "学业进度",
            "学习进度",
            "课程进度",
            "作业进度",
            "我们的项目",
            "我们的工作",
            "待办",
            "日程",
            "排班",
            "工单",
        )
        if any(marker in compact for marker in explicit_task_markers):
            return True
        english_context_markers = (
            "job",
            "work",
            "project",
            "task",
            "meeting",
            "schedule",
            "agenda",
        )
        english_state_markers = (
            "progress",
            "plan",
            "status",
            "assignment",
            "deadline",
        )
        if any(marker in compact for marker in english_context_markers) and any(
            marker in compact for marker in english_state_markers
        ):
            return True
        temporal_markers = (
            "今天",
            "今日",
            "现在",
            "当前",
            "这次",
            "本周",
            "这周",
            "最近",
        )
        task_markers = ("工作", "任务", "项目", "计划", "安排", "进度", "日程")
        return any(marker in compact for marker in temporal_markers) and any(
            marker in compact for marker in task_markers
        )

    @staticmethod
    def _profile_query_matches_memory(query: str, memory: MemoryRecord) -> bool:
        memory_type = clean_text(getattr(memory, "memory_type", ""), 80).lower()
        if memory_type not in PROFILE_MEMORY_TYPES:
            return False
        source = clean_text(query, 1000).lower()
        if not RetrievalEngineProfilePolicy._looks_like_explicit_profile_query(source):
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", source)
        if not compact:
            return False
        if any(
            marker in compact
            for marker in ("我的画像", "我的资料", "myprofile", "aboutme")
        ) or re.search(r"关于我的(?:资料|画像|个人信息|基本信息)", compact):
            return True
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        dimension = clean_text(metadata.get("profile_dimension"), 80).lower()
        aliases = {
            "preferred_address": (
                "我的称呼",
                "称呼我",
                "怎么叫我",
                "如何叫我",
                "该叫我",
                "喊我什么",
                "我叫什么",
                "我的昵称",
                "callme",
                "myname",
                "mynickname",
            ),
            "birthday": ("我的生日", "我的出生", "mybirthday"),
            "occupation": (
                "我的职业",
                "我做什么工作",
                "我从事什么工作",
                "我是做什么的",
                "我的工作岗位",
                "我的职位",
                "我的职务",
                "myoccupation",
                "myjob",
                "myprofession",
            ),
            "education": ("我的专业", "我的学业", "mymajor", "myeducation"),
            "zodiac": ("我的星座", "myzodiac"),
            "blood_type": ("我的血型", "mybloodtype"),
            "preference": (
                "我的偏好",
                "我喜欢什么",
                "我讨厌什么",
                "我的爱好",
                "mypreference",
                "myfavorite",
                "myfavourite",
            ),
            "dietary_restriction": (
                "我的禁忌",
                "我的忌口",
                "我对什么过敏",
                "我不能吃什么",
                "myallergy",
            ),
            "communication_preference": ("我的习惯", "我的沟通习惯", "myhabit"),
            "habit": ("我的习惯", "myhabit"),
            "boundary": ("我的边界", "我的雷区", "我不想聊什么", "myboundary"),
        }
        return any(marker in compact for marker in aliases.get(dimension, ()))

    def _prefilter_summary_reason(self, prefiltered: dict[str, int]) -> str:
        total = sum(max(0, int(count or 0)) for count in prefiltered.values())
        top = sorted(prefiltered.items(), key=lambda item: (-item[1], item[0]))[:6]
        detail = ",".join(f"{reason}:{count}" for reason, count in top)
        return f"prefiltered_out_of_search_range:{total};{detail}"
