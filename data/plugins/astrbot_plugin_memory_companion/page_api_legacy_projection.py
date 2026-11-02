from __future__ import annotations

import hashlib
import mimetypes
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .core.models import clean_text
from .page_api_support import (
    PAGE_API_PREFIXES,
)


class PluginPageApiLegacyProjection:
    """Legacy projection; shared runtime resources belong to the facade."""

    def _private_companion_status(self) -> dict[str, Any]:
        for module_name in (
            "data.plugins.astrbot_plugin_private_companion.main",
            "astrbot_plugin_private_companion.main",
        ):
            module = sys.modules.get(module_name)
            if module is None:
                continue
            getter = getattr(module, "get_private_companion_api", None)
            if not callable(getter):
                continue
            try:
                api = getter()
            except Exception:
                api = None
            if api is None:
                continue
            plugin = getattr(api, "_plugin", None)
            bridge_status: dict[str, Any] = {}
            status_getter = (
                getattr(plugin, "_memory_companion_coordination_status", None)
                if plugin
                else None
            )
            if callable(status_getter):
                try:
                    candidate = status_getter()
                    if isinstance(candidate, dict):
                        bridge_status = candidate
                except Exception:
                    bridge_status = {
                        "available": False,
                        "state": "degraded",
                        "reason": "bridge_status_failed",
                    }
            bridge_available = bool(
                bridge_status.get("available")
                and bridge_status.get("state")
                not in {"degraded", "local_only", "disabled", "inactive"}
                and not bridge_status.get("degraded")
            )
            return {
                "available": True,
                "plugin_name": "astrbot_plugin_private_companion",
                "daily_plan_enabled": bool(getattr(plugin, "enable_daily_plan", False))
                if plugin
                else False,
                "detail_enabled": bool(
                    getattr(plugin, "enable_detail_enhancement", False)
                )
                if plugin
                else False,
                "bridge_available": bridge_available,
                "bridge_state": clean_text(
                    bridge_status.get("state") or "degraded", 40
                ),
                "bridge_reason": clean_text(
                    bridge_status.get("reason")
                    or bridge_status.get("error_code")
                    or "bridge_status_unavailable",
                    120,
                ),
                "plugin": plugin,
            }
        return {
            "available": False,
            "plugin_name": "astrbot_plugin_private_companion",
            "reason": "未检测到已加载的主动陪伴插件",
        }

    @staticmethod
    def _merge_records_by_id(primary: list[Any], extra: list[Any]) -> list[Any]:
        rows: list[Any] = []
        seen: set[str] = set()
        for record in [*(primary or []), *(extra or [])]:
            record_id = clean_text(getattr(record, "id", ""), 160)
            key = (
                record_id
                or f"{getattr(record, 'memory_type', '')}:{getattr(record, 'content', '')}"
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(record)
        return rows

    def _private_companion_snapshot(
        self,
        plugin: Any,
        selected_date: str = "",
        records: list[Any] | None = None,
    ) -> dict[str, Any]:
        if plugin is None:
            return {}
        data = getattr(plugin, "data", {})
        if not isinstance(data, dict):
            data = {}
        plan = self._private_companion_plan_for_date(data, selected_date)
        if not isinstance(plan, dict):
            plan = {}
        memory_plan = self._schedule_memory_plan_for_date(records or [], selected_date)
        if memory_plan and len(memory_plan.get("items", []) or []) > len(
            plan.get("items", []) or []
        ):
            plan = memory_plan
        state = data.get("daily_state", {})
        if not isinstance(state, dict):
            state = {}
        enhanced = self._private_companion_detail_segments_for_date(data, selected_date)
        current_item = None
        getter = getattr(plugin, "_get_current_plan_item", None)
        if callable(getter) and (
            not selected_date or selected_date == clean_text(plan.get("date"), 16)
        ):
            try:
                current_item = getter(plan)
            except Exception:
                current_item = None
        if not isinstance(current_item, dict):
            current_item = {}
        details = self._merge_companion_details(
            self._compact_details(enhanced),
            self._story_plan_details_for_date(data, selected_date, plan),
            self._schedule_memory_details(records or [], selected_date, plan),
        )
        return {
            "bot_name": str(getattr(plugin, "bot_name", "") or ""),
            "plan": self._compact_plan(plan),
            "current_item": self._compact_plan_item(current_item),
            "daily_state": {
                "date": clean_text(state.get("date"), 40),
                "energy": state.get("energy", ""),
                "mood_bias": clean_text(state.get("mood_bias"), 80),
                "sleep": clean_text(state.get("sleep"), 120),
                "weather": clean_text(state.get("weather"), 160),
                "note": clean_text(state.get("note"), 240),
            },
            "details": details,
            "album": self._private_companion_album(
                data, selected_date, records or [], plugin=plugin
            ),
            "subjective_memories": self._private_companion_subjective_memories(
                data, selected_date, records or []
            ),
        }

    def _private_companion_album(
        self,
        data: dict[str, Any],
        selected_date: str,
        records: list[Any] | None = None,
        limit: int = 8,
        *,
        plugin: Any = None,
        include_local_path: bool = False,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(raw: Any, source: str) -> None:
            if not isinstance(raw, dict):
                return
            date = clean_text(raw.get("date"), 16)
            generated_at = raw.get("generated_at") or raw.get("ts")
            if not date and generated_at:
                date = self._timestamp_date_key(generated_at)
            if selected_date and date and date != selected_date:
                return
            path = clean_text(raw.get("path"), 500)
            error = clean_text(raw.get("error"), 240)
            if not path and not error:
                return
            item_id = clean_text(raw.get("trace"), 80)
            if not item_id:
                item_id = hashlib.sha1(
                    f"{source}:{date}:{path or error}".encode("utf-8", errors="ignore")
                ).hexdigest()[:16]
            if item_id in seen:
                return
            seen.add(item_id)
            safe_path = self._safe_companion_photo_path(path, plugin) if path else None
            exists = safe_path is not None
            query = f"date={quote(date, safe='')}&id={quote(item_id, safe='')}"
            row = {
                "id": item_id,
                "date": date,
                "kind": clean_text(raw.get("kind"), 40)
                or ("daily_outfit" if source == "daily_outfit_photo" else source),
                "title": {
                    "daily_outfit_photo": "每日穿搭图",
                    "recent_photo": "近期自拍",
                    "life_photo": "生活分享图",
                }.get(source, "近期照片"),
                "url": f"{PAGE_API_PREFIXES[0]}/companion/personal-photo?{query}",
                "image_data_url": f"/companion/personal-photo-data?{query}",
                "exists": exists,
                "backend": clean_text(raw.get("backend"), 80),
                "prompt": clean_text(raw.get("prompt"), 360),
                "note": clean_text(raw.get("note"), 220),
                "error": error if not exists else "",
                "generated_at": self._timestamp_label(generated_at),
            }
            if include_local_path and safe_path is not None:
                row["_local_path"] = str(safe_path)
            rows.append(row)

        add(data.get("daily_outfit_photo"), "daily_outfit_photo")
        recent = data.get("recent_photo_generations", [])
        if isinstance(recent, list):
            for item in recent[:12]:
                if not isinstance(item, dict):
                    continue
                ok = bool(item.get("ok"))
                path = clean_text(item.get("path"), 500)
                if not ok or not path:
                    continue
                session = clean_text(item.get("session"), 100)
                kind = clean_text(item.get("kind"), 30)
                if session == "daily_outfit" or kind == "selfie":
                    add(item, "recent_photo")
                elif session.startswith("natural_photo") or kind == "text2img":
                    add(item, "life_photo")

        for record in records or []:
            if not self._is_personal_album_memory(record):
                continue
            metadata = getattr(record, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                continue
            tags = {
                clean_text(tag, 80)
                for tag in (getattr(record, "tags", []) or [])
                if clean_text(tag, 80)
            }
            memory_type = clean_text(getattr(record, "memory_type", ""), 80)
            payload = (
                metadata.get("payload")
                if isinstance(metadata.get("payload"), dict)
                else {}
            )
            path = clean_text(
                metadata.get("image_path")
                or metadata.get("path")
                or payload.get("image_path")
                or payload.get("path"),
                500,
            )
            if not path:
                continue
            date = (
                clean_text(metadata.get("date"), 16)
                or clean_text(payload.get("date"), 16)
                or self._memory_date_key(record)
            )
            if selected_date and date and date != selected_date:
                continue
            source = "memory_photo"
            if "daily_outfit" in tags or "outfit" in tags:
                source = "daily_outfit_photo"
            elif memory_type == "image_action" or "life_photo" in tags:
                source = "life_photo"
            item_id = clean_text(getattr(record, "id", ""), 120)
            if not item_id:
                item_id = hashlib.sha1(
                    f"memory:{date}:{path}".encode("utf-8", errors="ignore")
                ).hexdigest()[:16]
            if item_id in seen:
                continue
            seen.add(item_id)
            safe_path = self._safe_companion_photo_path(path, plugin) if path else None
            exists = safe_path is not None
            query = f"date={quote(date, safe='')}&id={quote(item_id, safe='')}"
            row = {
                "id": item_id,
                "date": date,
                "kind": "daily_outfit" if source == "daily_outfit_photo" else source,
                "title": {
                    "daily_outfit_photo": "每日穿搭图",
                    "life_photo": "生活分享图",
                }.get(source, "记忆照片"),
                "url": f"{PAGE_API_PREFIXES[0]}/companion/personal-photo?{query}",
                "image_data_url": f"/companion/personal-photo-data?{query}",
                "exists": exists,
                "backend": clean_text(
                    metadata.get("backend") or payload.get("backend"), 80
                ),
                "prompt": clean_text(
                    metadata.get("prompt_preview")
                    or metadata.get("prompt")
                    or payload.get("prompt"),
                    360,
                ),
                "note": clean_text(
                    metadata.get("note")
                    or payload.get("note")
                    or getattr(record, "content", ""),
                    220,
                ),
                "error": "" if exists else "图片文件不可用",
                "generated_at": self._timestamp_label(
                    getattr(record, "occurred_at", "")
                    or getattr(record, "created_at", "")
                ),
            }
            if include_local_path and safe_path is not None:
                row["_local_path"] = str(safe_path)
            rows.append(row)
        safe_limit = max(1, min(2000, int(limit or 8)))
        return rows[:safe_limit]

    def _safe_companion_photo_path(
        self, raw_path: Any, plugin: Any = None
    ) -> Path | None:
        text = clean_text(raw_path, 1000)
        if not text:
            return None
        roots: list[Path] = []
        candidates = [
            getattr(plugin, "data_dir", "") if plugin is not None else "",
            getattr(plugin, "plugin_data_dir", "") if plugin is not None else "",
            getattr(getattr(self.plugin, "service", None), "data_dir", ""),
        ]
        data_file = getattr(plugin, "data_file", "") if plugin is not None else ""
        if data_file:
            candidates.append(Path(str(data_file)).parent)
        for candidate in candidates:
            if not candidate:
                continue
            try:
                root = Path(str(candidate)).expanduser().resolve()
            except (OSError, RuntimeError, ValueError):
                continue
            if root not in roots:
                roots.append(root)
        if not roots:
            return None
        source = Path(text).expanduser()
        paths = [source] if source.is_absolute() else [root / source for root in roots]
        allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
        max_bytes = 20 * 1024 * 1024
        for candidate in paths:
            try:
                resolved = candidate.resolve()
                if not any(resolved.is_relative_to(root) for root in roots):
                    continue
                mime = mimetypes.guess_type(str(resolved))[0] or ""
                if (
                    resolved.suffix.lower() not in allowed_extensions
                    or not mime.startswith("image/")
                ):
                    continue
                stat = resolved.stat()
                if (
                    not resolved.is_file()
                    or stat.st_size <= 0
                    or stat.st_size > max_bytes
                ):
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            return resolved
        return None

    def _private_companion_subjective_memories(
        self,
        data: dict[str, Any],
        selected_date: str,
        records: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        diaries = data.get("bot_diaries", [])
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(diaries, list):
            for diary in reversed(diaries):
                if not isinstance(diary, dict):
                    continue
                date = clean_text(diary.get("date"), 16)
                if selected_date and date != selected_date:
                    continue
                story_plan = (
                    diary.get("story_plan")
                    if isinstance(diary.get("story_plan"), dict)
                    else {}
                )
                row_key = f"diary:{date}:{clean_text(diary.get('summary'), 120)}"
                seen.add(row_key)
                rows.append(
                    {
                        "date": date,
                        "summary": clean_text(diary.get("summary"), 220),
                        "body": clean_text(diary.get("body"), 520),
                        "share_seed": clean_text(diary.get("share_seed"), 180),
                        "tags": [
                            clean_text(tag, 40)
                            for tag in (diary.get("tags") or [])
                            if clean_text(tag, 40)
                        ][:8]
                        if isinstance(diary.get("tags"), list)
                        else [],
                        "today_events": self._compact_detail_events(
                            story_plan.get("today_events")
                        )
                        if story_plan
                        else [],
                        "proactive_events": self._compact_detail_events(
                            story_plan.get("proactive_events")
                        )
                        if story_plan
                        else [],
                        "long_term_events": self._compact_detail_events(
                            story_plan.get("long_term_events")
                        )
                        if story_plan
                        else [],
                    }
                )
                if len(rows) >= 4:
                    break
        for record in records or []:
            if not self._is_personal_subjective_memory(record):
                continue
            date = self._memory_date_key(record)
            if selected_date and date != selected_date:
                continue
            record_id = clean_text(getattr(record, "id", ""), 120)
            if record_id in seen:
                continue
            seen.add(record_id)
            metadata = getattr(record, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            rows.append(
                {
                    "date": date,
                    "summary": "梦境碎片",
                    "body": clean_text(getattr(record, "content", ""), 520),
                    "share_seed": "",
                    "tags": [
                        tag
                        for tag in [
                            "梦境碎片",
                            clean_text(metadata.get("dream_type"), 40),
                            clean_text(metadata.get("dream_mood"), 40),
                        ]
                        if tag
                    ],
                    "today_events": [],
                    "proactive_events": [],
                    "long_term_events": [],
                }
            )
            if len(rows) >= 6:
                break
        return rows

    def _private_companion_plan_for_date(
        self, data: dict[str, Any], selected_date: str
    ) -> dict[str, Any]:
        plan = data.get("daily_plan", {})
        if isinstance(plan, dict) and (
            not selected_date or clean_text(plan.get("date"), 16) == selected_date
        ):
            return plan
        history = data.get("daily_plan_history", [])
        if isinstance(history, list):
            for entry in reversed(history):
                if not isinstance(entry, dict):
                    continue
                if clean_text(entry.get("date"), 16) != selected_date:
                    continue
                items = entry.get("items")
                if isinstance(items, list) and items:
                    return {
                        "date": selected_date,
                        "source": entry.get("source") or "history",
                        "items": [
                            self._compact_plan_item(item, index=index)
                            for index, item in enumerate(items)
                            if isinstance(item, dict)
                        ][:18],
                    }
                return {
                    "date": selected_date,
                    "source": entry.get("source") or "history",
                    "items": self._history_samples_to_plan_items(entry.get("sample")),
                }
        return plan if isinstance(plan, dict) else {}

    def _private_companion_detail_segments_for_date(
        self, data: dict[str, Any], selected_date: str
    ) -> dict[str, Any]:
        current_day = clean_text(data.get("detail_enhanced_day"), 16)
        current = data.get("detail_enhanced_segments", {})
        if isinstance(current, dict) and (
            not selected_date or current_day == selected_date
        ):
            return current
        history = data.get("detail_enhanced_history", [])
        if isinstance(history, list):
            for entry in reversed(history):
                if not isinstance(entry, dict):
                    continue
                if clean_text(entry.get("date"), 16) != selected_date:
                    continue
                segments = entry.get("segments")
                return segments if isinstance(segments, dict) else {}
        return {}

    def _history_samples_to_plan_items(self, samples: Any) -> list[dict[str, Any]]:
        if not isinstance(samples, list):
            return []
        rows = []
        for sample in samples:
            text = clean_text(sample, 180)
            if not text:
                continue
            parts = text.split(maxsplit=1)
            if parts and ":" in parts[0]:
                rows.append(
                    {
                        "index": len(rows),
                        "time": parts[0],
                        "activity": parts[1] if len(parts) > 1 else "",
                    }
                )
            else:
                rows.append({"index": len(rows), "time": "", "activity": text})
        return rows

    def _private_companion_dates(self, plugin: Any, records: list[Any]) -> list[str]:
        dates: set[str] = set()
        if plugin is not None:
            data = getattr(plugin, "data", {})
            if isinstance(data, dict):
                plan = data.get("daily_plan", {})
                if isinstance(plan, dict) and clean_text(plan.get("date"), 16):
                    dates.add(clean_text(plan.get("date"), 16))
                history = data.get("daily_plan_history", [])
                if isinstance(history, list):
                    for entry in history:
                        if isinstance(entry, dict) and clean_text(
                            entry.get("date"), 16
                        ):
                            dates.add(clean_text(entry.get("date"), 16))
                detail_history = data.get("detail_enhanced_history", [])
                if isinstance(detail_history, list):
                    for entry in detail_history:
                        if isinstance(entry, dict) and clean_text(
                            entry.get("date"), 16
                        ):
                            dates.add(clean_text(entry.get("date"), 16))
                story_history = data.get("daily_story_plan_history", [])
                if isinstance(story_history, list):
                    for entry in story_history:
                        if isinstance(entry, dict) and clean_text(
                            entry.get("date"), 16
                        ):
                            dates.add(clean_text(entry.get("date"), 16))
                if clean_text(data.get("detail_enhanced_day"), 16):
                    dates.add(clean_text(data.get("detail_enhanced_day"), 16))
                diaries = data.get("bot_diaries", [])
                if isinstance(diaries, list):
                    for diary in diaries:
                        if isinstance(diary, dict) and clean_text(
                            diary.get("date"), 16
                        ):
                            dates.add(clean_text(diary.get("date"), 16))
        for record in records:
            metadata = getattr(record, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            key = (
                clean_text(metadata.get("date"), 16)
                if self._is_personal_schedule_memory(record)
                else ""
            )
            key = key or self._memory_date_key(record)
            if key and (
                self._is_personal_action(record)
                or self._is_personal_schedule_memory(record)
                or self._is_personal_album_memory(record)
                or self._is_personal_subjective_memory(record)
            ):
                dates.add(key)
        return sorted(dates, reverse=True)

    def _memory_date_key(self, record: Any) -> str:
        metadata = getattr(record, "metadata", {}) or {}
        date = (
            clean_text(metadata.get("date"), 16) if isinstance(metadata, dict) else ""
        )
        return date or self._date_key(
            getattr(record, "occurred_at", "") or getattr(record, "created_at", "")
        )
