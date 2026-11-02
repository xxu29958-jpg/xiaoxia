from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .companion_page_bridge import (
    CompanionPageBridgeError,
)

"""One-release, isolated N-1 Companion Page compatibility adapter.

This is the only Memory module allowed to unwrap the historical Companion
facade.  Its public results use the same bounded, path-free wire as the formal
producer; host objects and filesystem paths never leave this module.
"""


_LEGACY_REQUIRED_API_METHODS = (
    "get_p6_readonly_status",
    "story_migration_capabilities",
    "export_story_migration_snapshot",
    "get_bot_identity",
)


_PHOTO_REF_RE = re.compile(r"^mphoto_([0-9a-f]{12})_([A-Za-z0-9_-]{22})$")


_SAFE_CODE_RE = re.compile(r"[^a-z0-9_]+")


_MAX_PHOTO_REFS = 256


_PHOTO_TTL_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class _LegacyPhoto:
    root: Path
    parts: tuple[str, ...]
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str
    mime_type: str
    expires_at: float


def _fail(code: str) -> None:
    raise CompanionPageBridgeError(code)


def _text(value: Any, maximum: int) -> str:
    if value is None:
        return ""
    text = "".join(
        " " if unicodedata.category(character) in {"Cs", "Cc"} else character
        for character in str(value)
    )
    text = " ".join(text.split())
    return text[:maximum]


def _date(value: Any) -> str:
    text = _text(value, 16)
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return ""


def _timestamp(value: Any) -> int:
    try:
        result = int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0
    return result if 0 <= result <= 10**10 else 0


def _date_from_timestamp(value: Any) -> str:
    stamp = _timestamp(value)
    if not stamp:
        return ""
    try:
        return (
            datetime.fromtimestamp(stamp, ZoneInfo("Asia/Shanghai")).date().isoformat()
        )
    except (OSError, OverflowError, ValueError):
        return ""


def _safe_code(value: Any, fallback: str = "") -> str:
    code = _SAFE_CODE_RE.sub("_", _text(value, 64).lower()).strip("_")
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        return code
    return fallback


def _opaque_identifier(secret: bytes, value: str) -> str:
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _string_list(value: Any, *, count: int, maximum: int) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = _text(item, maximum)
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= count:
            break
    return rows


def _event_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _text(
                item.get("event")
                or item.get("content")
                or item.get("text")
                or item.get("topic")
                or item.get("why")
                or item.get("reason")
                or item.get("action"),
                180,
            )
        else:
            text = _text(item, 180)
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= 5:
            break
    return rows


def _plan_item(value: Any, index: int | None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "index": index,
        "time": _text(raw.get("time"), 20),
        "activity": _text(raw.get("activity") or raw.get("title"), 180),
        "mood": _text(raw.get("mood"), 80),
        "message_seed": _text(raw.get("message_seed"), 220),
    }


def _plan(value: Any, *, source: str = "none") -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    normalized = [
        _plan_item(item, index)
        for index, item in enumerate(items[:18])
        if isinstance(item, dict)
    ]
    return {
        "date": _date(raw.get("date")),
        "source": source if source in {"live", "history", "none"} else "none",
        "items": normalized,
    }


def _history_samples(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for sample in value:
        text = _text(sample, 260)
        if not text:
            continue
        parts = text.split(maxsplit=1)
        time_value = parts[0] if parts and ":" in parts[0] else ""
        activity = parts[1] if time_value and len(parts) > 1 else text
        rows.append(
            {
                "index": len(rows),
                "time": _text(time_value, 20),
                "activity": _text(activity, 180),
                "mood": "",
                "message_seed": "",
            }
        )
        if len(rows) >= 18:
            break
    return rows


def _plan_for_date(data: dict[str, Any], selected_date: str) -> dict[str, Any]:
    current = data.get("daily_plan")
    if isinstance(current, dict) and _date(current.get("date")) == selected_date:
        return _plan(current, source="live")
    history = data.get("daily_plan_history")
    if isinstance(history, list):
        for entry in reversed(history):
            if not isinstance(entry, dict) or _date(entry.get("date")) != selected_date:
                continue
            nested = entry.get("plan")
            if isinstance(nested, dict):
                candidate = dict(nested)
                candidate.setdefault("date", selected_date)
                return _plan(candidate, source="history")
            if isinstance(entry.get("items"), list):
                return _plan(entry, source="history")
            return {
                "date": selected_date,
                "source": "history",
                "items": _history_samples(entry.get("sample")),
            }
    return {"date": "", "source": "none", "items": []}


def _details_for_date(
    data: dict[str, Any],
    selected_date: str,
    secret: bytes,
) -> list[dict[str, Any]]:
    sources: list[Any] = []
    if _date(data.get("detail_enhanced_day")) == selected_date:
        sources.append(data.get("detail_enhanced_segments"))
    else:
        history = data.get("detail_enhanced_history")
        if isinstance(history, list):
            for entry in reversed(history[-512:]):
                if (
                    isinstance(entry, dict)
                    and _date(entry.get("date")) == selected_date
                ):
                    sources.append(entry.get("segments"))
                    break
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        if isinstance(source, dict):
            iterable = list(source.items())
        elif isinstance(source, list):
            iterable = [(str(index), item) for index, item in enumerate(source)]
        else:
            iterable = []
        for key, item in iterable:
            if not isinstance(item, dict):
                continue
            raw_key = _text(key, 80)
            opaque = _opaque_identifier(
                secret,
                f"{selected_date}:{raw_key}:{len(rows)}",
            )
            identifier = f"detail_{opaque}"
            if identifier in seen:
                continue
            seen.add(identifier)
            key_match = re.fullmatch(
                r"\d{4}-\d{2}-\d{2}:(\d{1,3}):(\d{1,2}:\d{2})",
                raw_key,
            )
            raw_index = item.get("index")
            index_value = (
                raw_index if type(raw_index) is int and 0 <= raw_index <= 17 else None
            )
            if index_value is None and key_match is not None:
                parsed_index = int(key_match.group(1))
                index_value = parsed_index if parsed_index <= 17 else None
            raw_status = _safe_code(item.get("status"), "unknown")
            status_value = {
                "done": "ready",
                "complete": "ready",
                "completed": "ready",
                "pending": "planned",
                "generating": "planned",
                "failed": "degraded",
            }.get(raw_status, raw_status)
            if status_value not in {
                "planned",
                "ready",
                "observed",
                "degraded",
                "story_plan",
                "unknown",
            }:
                status_value = "unknown"
            time_value = _text(item.get("time") or item.get("start_time"), 20)
            if not time_value and key_match is not None:
                time_value = key_match.group(2)
            rows.append(
                {
                    "id": identifier,
                    "index": index_value,
                    "status": status_value,
                    "time": time_value,
                    "summary": _text(item.get("summary"), 180),
                    "today_events": _event_list(item.get("today_events")),
                    "proactive_events": _event_list(item.get("proactive_events")),
                    "state_variables": _event_list(item.get("state_variables")),
                }
            )
            if len(rows) >= 18:
                return rows

    story = data.get("daily_story_plan")
    if not isinstance(story, dict) or _date(story.get("date")) != selected_date:
        story = None
        story_history = data.get("daily_story_plan_history")
        if isinstance(story_history, list):
            for entry in reversed(story_history):
                if (
                    isinstance(entry, dict)
                    and _date(entry.get("date")) == selected_date
                ):
                    story = entry
                    break
    if isinstance(story, dict) and len(rows) < 18:
        grouped: dict[str, dict[str, list[str]]] = {}
        for field in ("today_events", "proactive_events"):
            events = story.get(field) if isinstance(story.get(field), list) else []
            for item in events:
                if not isinstance(item, dict):
                    continue
                window = _text(
                    item.get("window") or item.get("time") or item.get("range"),
                    20,
                )
                bucket = grouped.setdefault(
                    window or "story",
                    {"today_events": [], "proactive_events": []},
                )
                text = _text(
                    item.get("event")
                    or item.get("content")
                    or item.get("text")
                    or item.get("topic")
                    or item.get("why")
                    or item.get("reason")
                    or item.get("action"),
                    180,
                )
                if text and text not in bucket[field] and len(bucket[field]) < 5:
                    bucket[field].append(text)
        for window, bucket in grouped.items():
            opaque = _opaque_identifier(secret, f"story:{selected_date}:{window}")
            rows.append(
                {
                    "id": f"detail_{opaque}",
                    "index": None,
                    "status": "story_plan",
                    "time": window,
                    "summary": (
                        bucket["today_events"] or bucket["proactive_events"] or [""]
                    )[0],
                    "today_events": bucket["today_events"],
                    "proactive_events": bucket["proactive_events"],
                    "state_variables": [],
                }
            )
            if len(rows) >= 18:
                break
    return rows[:18]


def _diaries_for_date(data: dict[str, Any], selected_date: str) -> list[dict[str, Any]]:
    values = data.get("bot_diaries")
    if not isinstance(values, list):
        return []
    rows: list[dict[str, Any]] = []
    for diary in reversed(values):
        if not isinstance(diary, dict) or _date(diary.get("date")) != selected_date:
            continue
        story = (
            diary.get("story_plan") if isinstance(diary.get("story_plan"), dict) else {}
        )
        rows.append(
            {
                "date": selected_date,
                "summary": _text(diary.get("summary"), 220),
                "body": _text(diary.get("body"), 520),
                "share_seed": _text(diary.get("share_seed"), 180),
                "tags": _string_list(diary.get("tags"), count=8, maximum=40),
                "today_events": _event_list(
                    diary.get("today_events") or story.get("today_events")
                ),
                "proactive_events": _event_list(
                    diary.get("proactive_events") or story.get("proactive_events")
                ),
                "long_term_events": _event_list(
                    diary.get("long_term_events") or story.get("long_term_events")
                ),
            }
        )
        if len(rows) >= 4:
            break
    return rows


def _available_dates(data: dict[str, Any]) -> list[str]:
    rows: set[str] = set()

    def add(value: Any) -> None:
        normalized = _date(value)
        if normalized:
            rows.add(normalized)

    current = data.get("daily_plan")
    if isinstance(current, dict):
        add(current.get("date"))
    for field in (
        "daily_plan_history",
        "detail_enhanced_history",
        "daily_story_plan_history",
        "bot_diaries",
    ):
        values = data.get(field)
        if isinstance(values, list):
            for item in values[-512:]:
                if isinstance(item, dict):
                    add(item.get("date"))
    add(data.get("detail_enhanced_day"))
    add(data.get("state_generated_day"))
    story = data.get("daily_story_plan")
    if isinstance(story, dict):
        add(story.get("date"))
    state = data.get("daily_state")
    if isinstance(state, dict):
        add(state.get("date"))
    outfit = data.get("daily_outfit_photo")
    if isinstance(outfit, dict):
        add(outfit.get("date") or _date_from_timestamp(outfit.get("generated_at")))
    outfit_history = data.get("daily_outfit_history")
    if isinstance(outfit_history, list):
        for item in outfit_history[-512:]:
            if isinstance(item, dict):
                add(item.get("date") or _date_from_timestamp(item.get("generated_at")))
    recent = data.get("recent_photo_generations")
    if isinstance(recent, list):
        for item in recent[:256]:
            if isinstance(item, dict):
                add(
                    item.get("date")
                    or _date_from_timestamp(item.get("generated_at") or item.get("ts"))
                )
    return sorted(rows, reverse=True)[:180]


def _bounded_data_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Detach only the N-1 fields consumed by this adapter, with history caps."""

    result: dict[str, Any] = {}
    for field in (
        "daily_plan",
        "daily_state",
        "daily_story_plan",
        "daily_outfit_photo",
    ):
        value = data.get(field)
        if isinstance(value, dict):
            result[field] = copy.deepcopy(value)
    segments = data.get("detail_enhanced_segments")
    if isinstance(segments, dict):
        bounded_segments: dict[Any, Any] = {}
        for index, (key, value) in enumerate(segments.items()):
            if index >= 64:
                break
            bounded_segments[key] = value
        result["detail_enhanced_segments"] = copy.deepcopy(bounded_segments)
    for field in ("detail_enhanced_day", "state_generated_day"):
        value = data.get(field)
        if isinstance(value, str):
            result[field] = value
    for field, limit, newest_at_end in (
        ("daily_plan_history", 512, True),
        ("detail_enhanced_history", 512, True),
        ("daily_story_plan_history", 512, True),
        ("bot_diaries", 512, True),
        ("daily_outfit_history", 512, True),
        ("recent_photo_generations", 256, False),
    ):
        value = data.get(field)
        if not isinstance(value, list):
            continue
        bounded = value[-limit:] if newest_at_end else value[:limit]
        result[field] = copy.deepcopy(bounded)
    return result
