from __future__ import annotations

import re

_TIME_CLAIM_RE = re.compile(
    r"(?:20\d{2}[-年]\d{1,2}(?:[-月]\d{1,2})?|\d{1,2}月\d{1,2}日|"
    r"周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"上午|下午|早上|晚上|凌晨|中午|傍晚|深夜|\d{1,2}点(?:\d{1,2}分)?)"
)


_DATE_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (
        re.compile(r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?"),
        True,
    ),
    (re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])"), False),
    (re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"), False),
)


_WEEKDAY_RE = re.compile(r"(?:周|星期)([一二三四五六日天])")


_RELATIVE_TIME_HINTS = (
    "昨天",
    "昨晚",
    "前天",
    "明晚",
    "明天",
    "后天",
    "大后天",
    "上周",
    "下周",
    "上个月",
    "下个月",
    "下个星期",
    "去年",
    "明年",
    "后年",
    "月底",
    "月初",
    "年底",
    "年初",
)


_NEGATION_CHARS = frozenset("不没未无别禁")


_HOUR_PERIODS: dict[int, tuple[str, ...]] = {
    0: ("凌晨", "深夜"),
    1: ("凌晨", "深夜"),
    2: ("凌晨", "深夜"),
    3: ("凌晨", "深夜"),
    4: ("凌晨", "深夜"),
    5: ("凌晨", "早上"),
    6: ("早上",),
    7: ("早上",),
    8: ("早上", "上午"),
    9: ("上午", "早上"),
    10: ("上午",),
    11: ("上午", "中午"),
    12: ("中午",),
    13: ("中午", "下午"),
    14: ("下午",),
    15: ("下午",),
    16: ("下午",),
    17: ("下午", "傍晚"),
    18: ("傍晚", "晚上"),
    19: ("晚上",),
    20: ("晚上",),
    21: ("晚上",),
    22: ("晚上", "深夜"),
    23: ("晚上", "深夜"),
}


_CONTRADICTION_MIN_CHARS = 4


_CONTRADICTION_MAX_CHARS = 120


_CONTRADICTION_COVERAGE = 0.6


class SummaryFormatError(ValueError):
    def __init__(self, response: str):
        super().__init__("summary provider returned invalid JSON")
        self.response = response[:4000]
