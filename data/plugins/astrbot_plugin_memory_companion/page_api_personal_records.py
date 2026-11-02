from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .core.models import clean_text


class PluginPageApiPersonalRecords:
    """Personal records; shared runtime resources belong to the facade."""

    def _date_key(self, value: Any) -> str:
        text = clean_text(value, 80)
        if not text:
            return ""
        try:
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is not None:
                dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
            return dt.date().isoformat()
        except Exception:
            return text[:10] if len(text) >= 10 else ""

    def _timestamp_date_key(self, value: Any) -> str:
        try:
            ts = float(value or 0)
        except Exception:
            return self._date_key(value)
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai")).date().isoformat()

    def _timestamp_label(self, value: Any) -> str:
        try:
            ts = float(value or 0)
        except Exception:
            return clean_text(value, 40)
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M"
        )

    def _is_personal_action(self, record: Any) -> bool:
        tags = getattr(record, "tags", []) or []
        tag_set = {clean_text(tag, 80) for tag in tags if clean_text(tag, 80)}
        action_types = {
            "self_action",
            "proactive_message",
            "search_action",
            "creative_work",
            "image_action",
            "qzone_action",
            "reading_memory",
        }
        non_action_tags = {
            "schedule",
            "daily_plan",
            "daily_detail",
            "daily_outfit",
            "outfit",
            "dream",
            "dream_fragment",
        }
        positive_action_tags = {
            "bot_action",
            "qzone",
            "qzone_publish",
            "proactive",
            "proactive_message",
            "search",
            "creative_work",
            "image_action",
            "reading",
            "self_meal",
        }
        memory_type = getattr(record, "memory_type", "")
        return getattr(record, "visibility", "") == "bot_self" and (
            memory_type in action_types
            or bool(tag_set & positive_action_tags)
            or (
                getattr(record, "source_plugin", "") == "private_companion"
                and memory_type != "schedule_fragment"
                and not bool(tag_set & non_action_tags)
            )
        )

    def _is_personal_schedule_memory(self, record: Any) -> bool:
        tags = getattr(record, "tags", []) or []
        tag_set = {clean_text(tag, 80) for tag in tags if clean_text(tag, 80)}
        metadata = getattr(record, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        content = clean_text(getattr(record, "content", ""), 360)
        payload = (
            metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
        )
        memory_type = clean_text(getattr(record, "memory_type", ""), 80)
        return getattr(record, "visibility", "") == "bot_self" and (
            memory_type
            in {
                "schedule_fragment",
                "bot_schedule_plan",
                "bot_detail_fragment",
                "bot_calendar_event",
            }
            or "schedule" in tag_set
            or "daily_plan" in tag_set
            or "daily_detail" in tag_set
            or bool(
                clean_text(metadata.get("start"), 20)
                or clean_text(metadata.get("end"), 20)
            )
            or bool(
                clean_text(payload.get("date"), 16)
                and (
                    clean_text(payload.get("summary"), 180)
                    or isinstance(payload.get("items"), list)
                    or isinstance(payload.get("events"), list)
                    or isinstance(payload.get("today_events"), list)
                )
            )
            or "当日生活日程" in content
            or "日程细化" in content
        )

    def _is_personal_album_memory(self, record: Any) -> bool:
        if getattr(record, "visibility", "") != "bot_self":
            return False
        metadata = getattr(record, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            return False
        payload = (
            metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
        )
        if not clean_text(
            metadata.get("image_path")
            or metadata.get("path")
            or payload.get("image_path")
            or payload.get("path"),
            500,
        ):
            return False
        tags = {
            clean_text(tag, 80)
            for tag in (getattr(record, "tags", []) or [])
            if clean_text(tag, 80)
        }
        return (
            getattr(record, "memory_type", "")
            in {"image_action", "persona_life", "bot_media_memory"}
            or bool(
                tags
                & {"daily_outfit", "outfit", "life_photo", "image", "current_state"}
            )
            or getattr(record, "source_plugin", "") == "private_companion"
        )

    def _is_personal_subjective_memory(self, record: Any) -> bool:
        if getattr(record, "visibility", "") != "bot_self":
            return False
        tags = {
            clean_text(tag, 80)
            for tag in (getattr(record, "tags", []) or [])
            if clean_text(tag, 80)
        }
        return bool(
            tags & {"dream", "dream_fragment", "subjective_memory", "bot_diary"}
        )

    def _compact_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        items = plan.get("items", [])
        if not isinstance(items, list):
            items = []
        return {
            "date": clean_text(plan.get("date"), 40),
            "source": clean_text(plan.get("source"), 40),
            "items": [
                self._compact_plan_item(item, index=index)
                for index, item in enumerate(items)
                if isinstance(item, dict)
            ][:18],
        }

    def _compact_plan_item(
        self, item: dict[str, Any], index: int | None = None
    ) -> dict[str, Any]:
        return {
            "index": index if index is not None else "",
            "time": clean_text(item.get("time"), 20),
            "activity": clean_text(item.get("activity") or item.get("title"), 180),
            "mood": clean_text(item.get("mood"), 80),
            "message_seed": clean_text(item.get("message_seed"), 220),
        }

    def _compact_details(self, enhanced: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for key, item in enhanced.items():
            if not isinstance(item, dict):
                continue
            key_text = clean_text(key, 80)
            rows.append(
                {
                    "key": key_text,
                    "index": self._detail_index_from_key(key_text),
                    "status": clean_text(item.get("status"), 40),
                    "time": clean_text(
                        item.get("time")
                        or self._detail_time_from_key(key_text)
                        or item.get("started_at"),
                        40,
                    ),
                    "summary": clean_text(item.get("summary"), 180),
                    "today_events": self._compact_detail_events(
                        item.get("today_events")
                    ),
                    "proactive_events": self._compact_detail_events(
                        item.get("proactive_events")
                    ),
                    "state_variables": self._compact_detail_events(
                        item.get("state_variables")
                    ),
                }
            )
        return rows[-12:]

    def _merge_companion_details(
        self, *sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in sources:
            if not isinstance(source, list):
                continue
            for item in source:
                if not isinstance(item, dict):
                    continue
                key = (
                    clean_text(item.get("key"), 120)
                    or f"{item.get('index')}:{item.get('time')}"
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(item)
        return rows[-18:]

    def _story_plan_details_for_date(
        self,
        data: dict[str, Any],
        selected_date: str,
        plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        story = self._story_plan_for_date(data, selected_date)
        if not story:
            return []
        grouped: dict[str, dict[str, Any]] = {}

        def ensure(window: str) -> dict[str, Any]:
            key = clean_text(window, 40) or "story"
            row = grouped.setdefault(
                key,
                {
                    "key": f"story:{selected_date}:{key}",
                    "index": self._schedule_detail_index_for_time(
                        plan, key.split("-", 1)[0].strip()
                    ),
                    "status": "story_plan",
                    "time": key,
                    "summary": "",
                    "today_events": [],
                    "proactive_events": [],
                    "state_variables": [],
                },
            )
            return row

        for item in (
            story.get("today_events")
            if isinstance(story.get("today_events"), list)
            else []
        ):
            if not isinstance(item, dict):
                continue
            window = clean_text(
                item.get("window") or item.get("time") or item.get("range"), 40
            )
            row = ensure(window)
            text = clean_text(
                item.get("event") or item.get("content") or item.get("text"), 180
            )
            if text and text not in row["today_events"]:
                row["today_events"].append(text)
            if not row["summary"]:
                row["summary"] = text

        for item in (
            story.get("proactive_events")
            if isinstance(story.get("proactive_events"), list)
            else []
        ):
            if not isinstance(item, dict):
                continue
            window = clean_text(
                item.get("window") or item.get("time") or item.get("range"), 40
            )
            row = ensure(window)
            text = clean_text(
                item.get("topic")
                or item.get("why")
                or item.get("motive")
                or item.get("reason")
                or item.get("action"),
                180,
            )
            if text and text not in row["proactive_events"]:
                row["proactive_events"].append(text)

        rows = [
            row
            for row in grouped.values()
            if row["summary"] or row["today_events"] or row["proactive_events"]
        ]
        rows.sort(key=lambda item: clean_text(item.get("time"), 40))
        return rows[-18:]

    def _story_plan_for_date(
        self, data: dict[str, Any], selected_date: str
    ) -> dict[str, Any]:
        current = data.get("daily_story_plan", {})
        if isinstance(current, dict) and (
            not selected_date or clean_text(current.get("date"), 16) == selected_date
        ):
            return current
        history = data.get("daily_story_plan_history", [])
        if isinstance(history, list):
            for entry in reversed(history):
                if (
                    isinstance(entry, dict)
                    and clean_text(entry.get("date"), 16) == selected_date
                ):
                    return entry
        return {}

    def _schedule_memory_details(
        self,
        records: list[Any],
        selected_date: str,
        plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not selected_date:
            return []
        rows: list[dict[str, Any]] = []
        for record in records:
            if not self._is_personal_schedule_memory(record):
                continue
            metadata = getattr(record, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            payload = (
                metadata.get("payload")
                if isinstance(metadata.get("payload"), dict)
                else {}
            )
            date = (
                clean_text(metadata.get("date"), 16)
                or clean_text(payload.get("date"), 16)
                or self._memory_date_key(record)
            )
            if date != selected_date:
                continue
            start = clean_text(metadata.get("start"), 20) or clean_text(
                payload.get("start"), 20
            )
            end = clean_text(metadata.get("end"), 20) or clean_text(
                payload.get("end"), 20
            )
            summary = clean_text(metadata.get("summary"), 180) or clean_text(
                payload.get("summary"), 180
            )
            content = str(getattr(record, "content", "") or "")[:1600]
            if not summary:
                summary = self._schedule_detail_summary_from_content(content)
            today_events = self._schedule_detail_lines_from_content(
                content, "生活片段："
            )
            proactive_events = self._schedule_detail_lines_from_content(
                content, "可能主动念头："
            )
            if not today_events:
                today_events = self._schedule_detail_payload_lines(
                    payload.get("events") or payload.get("today_events")
                )
            if not proactive_events:
                proactive_events = self._schedule_detail_payload_lines(
                    payload.get("proactive_events")
                )
            if not summary and not today_events and not proactive_events:
                continue
            index = self._schedule_detail_index_for_time(plan, start)
            rows.append(
                {
                    "key": clean_text(getattr(record, "id", ""), 120)
                    or f"memory:{selected_date}:{start}:{end}",
                    "index": index,
                    "status": "memory",
                    "time": f"{start}-{end}" if start and end else start,
                    "summary": summary,
                    "today_events": today_events,
                    "proactive_events": proactive_events,
                    "state_variables": [],
                }
            )
        rows.sort(key=lambda item: clean_text(item.get("time"), 40))
        return rows[-18:]

    @staticmethod
    def _schedule_detail_payload_lines(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        rows: list[str] = []
        for item in value[:8]:
            if isinstance(item, dict):
                text = clean_text(
                    item.get("event")
                    or item.get("topic")
                    or item.get("text")
                    or item.get("summary"),
                    180,
                )
            else:
                text = clean_text(item, 180)
            if text and text not in rows:
                rows.append(text)
        return rows[:6]

    def _schedule_memory_plan_for_date(
        self, records: list[Any], selected_date: str
    ) -> dict[str, Any]:
        if not selected_date:
            return {}
        best_items: list[dict[str, Any]] = []
        for record in records:
            if not self._is_personal_schedule_memory(record):
                continue
            metadata = getattr(record, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            payload = (
                metadata.get("payload")
                if isinstance(metadata.get("payload"), dict)
                else {}
            )
            date = (
                clean_text(metadata.get("date"), 16)
                or clean_text(payload.get("date"), 16)
                or self._memory_date_key(record)
            )
            if date != selected_date:
                continue
            content = str(getattr(record, "content", "") or "")
            items = (
                payload.get("items") if isinstance(payload.get("items"), list) else []
            )
            if (
                "当日生活日程" not in content
                and not items
                and clean_text(getattr(record, "memory_type", ""), 80)
                not in {"schedule_fragment", "bot_schedule_plan"}
            ):
                continue
            if not items:
                items = self._schedule_plan_items_from_content(content)
            else:
                items = [
                    self._compact_plan_item(item, index=index)
                    for index, item in enumerate(items)
                    if isinstance(item, dict)
                ]
            if len(items) > len(best_items):
                best_items = items
        if not best_items:
            return {}
        return {
            "date": selected_date,
            "source": "memory_companion",
            "items": best_items[:18],
        }

    def _schedule_plan_items_from_content(self, content: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in str(content or "").splitlines():
            text = clean_text(line.strip("- "), 260)
            if not text:
                continue
            if (
                "日程细化" in text
                or text.startswith("生活片段：")
                or text.startswith("可能主动念头：")
            ):
                continue
            match = re.match(
                r"^(\d{1,2}:\d{2})(?:\s*[-~—至]\s*(\d{1,2}:\d{2}))?\s*(.+)$", text
            )
            if not match:
                continue
            activity = clean_text(match.group(3), 220)
            mood = ""
            seed = ""
            mood_match = re.search(r"情绪[:：]([^可]+)", activity)
            if mood_match:
                mood = clean_text(mood_match.group(1), 80)
                activity = clean_text(activity[: mood_match.start()], 180)
            seed_match = re.search(r"可分享[:：](.+)$", text)
            if seed_match:
                seed = clean_text(seed_match.group(1), 220)
            rows.append(
                {
                    "index": len(rows),
                    "time": clean_text(match.group(1), 20),
                    "activity": activity,
                    "mood": mood,
                    "message_seed": seed,
                }
            )
        return rows

    def _schedule_detail_index_for_time(self, plan: dict[str, Any], start: str) -> Any:
        if not start or not isinstance(plan, dict):
            return ""
        items = plan.get("items", [])
        if not isinstance(items, list):
            return ""
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            time_text = clean_text(item.get("time"), 40)
            if time_text and (time_text.startswith(start) or start in time_text):
                return index
        return ""

    def _schedule_detail_summary_from_content(self, content: str) -> str:
        for line in content.splitlines()[1:]:
            text = clean_text(line, 180)
            if (
                text
                and not text.startswith("生活片段：")
                and not text.startswith("可能主动念头：")
            ):
                return text
        return ""

    def _schedule_detail_lines_from_content(
        self, content: str, prefix: str
    ) -> list[str]:
        for line in content.splitlines():
            text = clean_text(line, 500)
            if not text.startswith(prefix):
                continue
            payload = text[len(prefix) :].strip()
            return [
                clean_text(part, 180)
                for part in payload.split("；")
                if clean_text(part, 180)
            ][:5]
        return []

    def _detail_time_from_key(self, key: Any) -> str:
        parts = clean_text(key, 80).split(":")
        if len(parts) >= 4:
            return f"{parts[2]}:{parts[3]}"
        if len(parts) >= 3:
            return parts[2]
        return ""

    def _detail_index_from_key(self, key: Any) -> Any:
        parts = clean_text(key, 80).split(":")
        if len(parts) >= 2:
            try:
                return int(parts[1])
            except Exception:
                return ""
        return ""

    def _compact_detail_events(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        rows = []
        for item in value[:5]:
            if isinstance(item, dict):
                window = clean_text(
                    item.get("window")
                    or item.get("time")
                    or item.get("range")
                    or item.get("when"),
                    40,
                )
                text = (
                    item.get("event")
                    or item.get("content")
                    or item.get("detail")
                    or item.get("description")
                    or item.get("text")
                    or item.get("topic")
                    or item.get("why")
                    or item.get("motive")
                    or item.get("reason")
                    or item.get("action")
                    or item.get("label")
                    or item.get("title")
                )
            else:
                window = ""
                text = item
            cleaned = clean_text(text, 180)
            if cleaned:
                rows.append(f"{window} {cleaned}".strip() if window else cleaned)
        return rows
