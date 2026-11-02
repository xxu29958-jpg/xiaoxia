from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import clean_text


class MemorySummarizerTimeEvidence:
    """Time evidence; shared runtime resources belong to the facade."""

    @staticmethod
    def _local_tz() -> ZoneInfo:
        return ZoneInfo("Asia/Shanghai")

    @classmethod
    def _now_local(cls) -> datetime:
        return datetime.now(cls._local_tz())

    @classmethod
    def _parse_local_datetime(cls, value: Any) -> datetime | None:
        text = clean_text(str(value or ""), 80)
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(cls._local_tz())
        except Exception:
            return None

    @classmethod
    def _format_local_time(cls, value: Any) -> str:
        dt = cls._parse_local_datetime(value)
        if dt is None:
            return clean_text(str(value or "")[:16].replace("T", " "), 20)
        return dt.strftime("%Y-%m-%d %H:%M")

    @classmethod
    def _rows_local_dates(cls, rows: list[dict[str, Any]]) -> list[str]:
        dates: list[str] = []
        for row in rows:
            dt = cls._parse_local_datetime(
                row.get("occurred_at") or row.get("created_at")
            )
            if dt is None:
                continue
            value = dt.strftime("%Y-%m-%d")
            if value not in dates:
                dates.append(value)
        return dates

    @classmethod
    def _local_hour_labels(cls, hour: int) -> list[str]:
        for boundary, names in cls.LOCAL_HOUR_LABELS:
            if hour < boundary:
                return list(names)
        return list(cls.LOCAL_HOUR_LABELS[-1][1])

    @classmethod
    def _rows_local_time_labels(cls, rows: list[dict[str, Any]]) -> list[str]:
        """Time-of-day words and hours justified by the rows' own timestamps.

        The temporal check used to require the claim's 时段 word to appear in a
        message body, so a summary written exactly as rule 8 demands ("2026-10-03
        晚上") was rejected unless the user had literally typed 晚上.  Deriving
        the vocabulary from the same timestamps that
        ``_normalize_relative_time_mentions`` uses keeps a genuine date mismatch
        rejected while a correct time-of-day claim passes.
        """
        labels: list[str] = []
        for row in rows:
            dt = cls._parse_local_datetime(
                row.get("occurred_at") or row.get("created_at")
            )
            if dt is None:
                continue
            for value in (*cls._local_hour_labels(dt.hour), f"{dt.hour}点"):
                if value not in labels:
                    labels.append(value)
        return labels

    @classmethod
    def _rows_local_time_range(cls, rows: list[dict[str, Any]]) -> str:
        values: list[datetime] = []
        for row in rows:
            dt = cls._parse_local_datetime(
                row.get("occurred_at") or row.get("created_at")
            )
            if dt is not None:
                values.append(dt)
        if not values:
            return ""
        start = min(values).strftime("%Y-%m-%d %H:%M")
        end = max(values).strftime("%Y-%m-%d %H:%M")
        return f"{start} 至 {end} Asia/Shanghai"

    @classmethod
    def _relative_time_anchor(cls, rows: list[dict[str, Any]]) -> tuple[str, str, str]:
        """Return (anchor, anchor-1day, anchor+1day) the relative-time rewrite uses."""
        dates = cls._rows_local_dates(rows)
        anchor = dates[0] if len(dates) == 1 else cls._now_local().strftime("%Y-%m-%d")
        try:
            anchor_dt = datetime.fromisoformat(anchor).replace(tzinfo=cls._local_tz())
        except Exception:
            anchor_dt = cls._now_local()
        midnight = anchor_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        previous = datetime.fromtimestamp(
            midnight.timestamp() - 86400, tz=cls._local_tz()
        )
        following = datetime.fromtimestamp(
            midnight.timestamp() + 86400, tz=cls._local_tz()
        )
        return (
            anchor,
            previous.strftime("%Y-%m-%d"),
            following.strftime("%Y-%m-%d"),
        )

    @classmethod
    def _relative_time_vocabulary(cls, rows: list[dict[str, Any]]) -> list[str]:
        """Every date and time-of-day expression the rewrite can mint.

        The rewrite turns "last night" into "2026-10-02 evening" out of the
        rows' own timestamps, so the checker has to accept exactly that set.
        Deriving the two independently let the rewrite produce a date the
        checker then rejected as ungrounded, which failed correct batches.
        """
        anchor, previous, following = cls._relative_time_anchor(rows)
        vocabulary = [*cls._rows_local_dates(rows), anchor, previous, following]
        vocabulary.extend(cls._rows_local_time_labels(rows))
        return vocabulary

    @classmethod
    def _normalize_relative_time_mentions(
        cls, text: str, rows: list[dict[str, Any]]
    ) -> str:
        text = clean_text(text, 4000)
        if not text:
            return ""
        anchor, yesterday_date, tomorrow_date = cls._relative_time_anchor(rows)

        replacements = [
            (r"昨晚|昨天晚上", f"{yesterday_date} 晚上"),
            (r"昨天中午", f"{yesterday_date} 中午"),
            (r"昨天早上|昨早", f"{yesterday_date} 早上"),
            (r"昨天", yesterday_date),
            (r"今晚|今天晚上", f"{anchor} 晚上"),
            (r"今天中午|今中午", f"{anchor} 中午"),
            (r"今天早上|今早", f"{anchor} 早上"),
            (r"今天下午|今下午", f"{anchor} 下午"),
            (r"今天", anchor),
            (r"明晚|明天晚上", f"{tomorrow_date} 晚上"),
            (r"明天中午", f"{tomorrow_date} 中午"),
            (r"明天早上", f"{tomorrow_date} 早上"),
            (r"明天", tomorrow_date),
        ]
        normalized = text
        for pattern, replacement in replacements:
            normalized = re.sub(pattern, replacement, normalized)
        return clean_text(normalized, 4000)
