from __future__ import annotations

import copy
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from quart import request, send_file

from .companion_page_bridge import (
    COMPANION_PLUGIN_ID,
    CompanionPageBridgeError,
    CompanionPagePhoto,
    CompanionPageSnapshot,
)
from .core.models import clean_text
from .page_api_support import (
    COMPANION_PERSONAL_FIELDS,
    DEFAULT_THEME_KEY,
    PAGE_API_PREFIXES,
    THEME_KEYS,
    THEME_NAME_TO_KEY,
)


class PluginPageApiCompanionPage:
    """Companion page; shared runtime resources belong to the facade."""

    def _theme_key(self, theme: str) -> str:
        value = clean_text(theme, 40)
        if value in THEME_NAME_TO_KEY:
            return THEME_NAME_TO_KEY[value]
        if value in THEME_KEYS:
            return value
        return DEFAULT_THEME_KEY

    async def companion_personal_memory(self):
        limit = max(1, min(300, self._query_int("limit", 80)))
        selected_date = clean_text(request.args.get("date", ""), 16)
        query = clean_text(request.args.get("q", ""), 200)
        try:
            companion = await self._companion_page_bridge.export_snapshot(selected_date)
        except CompanionPageBridgeError as exc:
            if exc.code == "memory_page_companion_unavailable":
                return self._no_store(
                    self._ok(
                        self._declared_payload(
                            self._companion_page_unavailable(exc.code),
                            COMPANION_PERSONAL_FIELDS,
                        )
                    )
                )
            return self._no_store(
                self._err(exc.code, self._companion_page_error_status(exc.code))
            )
        records = await self._personal_memory_records(
            limit=limit,
            query=query,
            selected_date=companion.payload["selected_date"],
        )

        payload = self._project_companion_page(companion, records)
        selected = payload["selected_date"]
        filtered = (
            [record for record in records if self._memory_date_key(record) == selected]
            if selected
            else records
        )
        payload["actions"] = [
            self._personal_action_projection(record)
            for record in filtered
            if self._is_personal_action(record)
        ][:limit]
        return self._no_store(
            self._ok(self._declared_payload(payload, COMPANION_PERSONAL_FIELDS))
        )

    async def companion_personal_photo(self):
        resolved = await self._read_companion_photo_from_request()
        if isinstance(resolved, dict):
            return self._no_store(
                self._err(
                    str(resolved.get("error") or "memory_page_photo_unavailable"),
                    int(resolved.get("status") or 404),
                )
            )
        return self._no_store(
            await send_file(BytesIO(resolved.content), mimetype=resolved.mime_type)
        )

    async def companion_personal_photo_data(self):
        resolved = await self._read_companion_photo_from_request()
        if isinstance(resolved, dict):
            return self._no_store(
                self._err(
                    str(resolved.get("error") or "memory_page_photo_unavailable"),
                    int(resolved.get("status") or 404),
                )
            )
        return self._no_store(
            self._ok(
                {
                    "mime": resolved.mime_type,
                    "size": resolved.size,
                    "data_url": f"data:{resolved.mime_type};base64,{resolved.content_base64}",
                }
            )
        )

    async def _read_companion_photo_from_request(
        self,
    ) -> CompanionPagePhoto | dict[str, Any]:
        photo_ref = clean_text(request.args.get("ref", ""), 64)
        selected_date = clean_text(request.args.get("date", ""), 16)
        photo_id = clean_text(request.args.get("id"), 120)
        expected_mode = clean_text(request.args.get("mode", ""), 16)
        if expected_mode not in {"", "formal", "legacy"}:
            return {"error": "memory_page_photo_ref_invalid", "status": 400}

        async def refresh_photo_ref() -> str | dict[str, Any]:
            dates = [selected_date]
            if photo_id and selected_date:
                dates.append("")
            last_error = "memory_page_photo_unavailable"
            for lookup_date in dates:
                try:
                    snapshot = await self._companion_page_bridge.export_snapshot(
                        lookup_date,
                        expected_mode=expected_mode or None,
                    )
                except CompanionPageBridgeError as exc:
                    last_error = exc.code
                    continue
                for item in snapshot.payload["day"]["photos"]:
                    if item["id"] == photo_id and item["available"] is True:
                        return item["photo_ref"]
            if last_error != "memory_page_photo_unavailable":
                return self._companion_photo_error(last_error)
            return {"error": "memory_page_photo_unavailable", "status": 404}

        if not photo_ref and photo_id:
            refreshed = await refresh_photo_ref()
            if isinstance(refreshed, dict):
                return refreshed
            photo_ref = refreshed
        if not photo_ref:
            return {"error": "memory_page_photo_ref_invalid", "status": 400}
        try:
            return await self._companion_page_bridge.read_photo(
                photo_ref, expected_mode=expected_mode or None
            )
        except CompanionPageBridgeError as exc:
            if photo_id and exc.code in {
                "memory_page_photo_ref_stale",
                "memory_page_photo_ref_expired",
                "memory_page_photo_changed",
            }:
                refreshed = await refresh_photo_ref()
                if isinstance(refreshed, dict):
                    return refreshed
                try:
                    return await self._companion_page_bridge.read_photo(
                        refreshed, expected_mode=expected_mode or None
                    )
                except CompanionPageBridgeError as refreshed_exc:
                    return self._companion_photo_error(refreshed_exc.code)
            return self._companion_photo_error(exc.code)

    @staticmethod
    def _companion_page_unavailable(code: str) -> dict[str, Any]:
        return {
            "available": False,
            "plugin_name": COMPANION_PLUGIN_ID,
            "reason": clean_text(code, 120) or "memory_page_companion_unavailable",
        }

    @staticmethod
    def _companion_photo_error(code: str) -> dict[str, Any]:
        if code in {
            "memory_page_photo_ref_stale",
            "memory_page_photo_ref_expired",
            "memory_page_contract_downgrade",
        }:
            status = 410
        elif code in {
            "memory_page_photo_ref_invalid",
            "memory_page_snapshot_invalid_date",
        }:
            status = 400
        elif code == "memory_page_photo_unavailable":
            status = 404
        elif code in {"memory_page_photo_too_large", "memory_page_snapshot_too_large"}:
            status = 413
        elif code == "memory_page_photo_unsupported":
            status = 415
        elif code.startswith("memory_page_"):
            status = 503
        else:
            status = 409
        return {"error": clean_text(code, 120), "status": status}

    @staticmethod
    def _companion_page_error_status(code: str) -> int:
        if code == "memory_page_snapshot_invalid_date":
            return 400
        if code == "memory_page_snapshot_too_large":
            return 413
        return 503

    @staticmethod
    def _no_store(response: Any) -> Any:
        headers = getattr(response, "headers", None)
        if headers is not None:
            headers["Cache-Control"] = "private, no-store"
            headers["Pragma"] = "no-cache"
            headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def _personal_memory_records(
        self, *, limit: int, query: str, selected_date: str
    ) -> list[Any]:
        try:
            records = await self.plugin.service.store.list_memories(
                limit=max(limit * 12, 1200),
                include_pending=False,
                query=query,
                visibility="bot_self",
            )
            if selected_date:
                date_records = await self.plugin.service.store.list_memories(
                    limit=240,
                    include_pending=False,
                    query=selected_date,
                    visibility="bot_self",
                )
                records = self._merge_records_by_id(records, date_records)
            return records
        except Exception:
            return []

    def _project_companion_page(
        self, snapshot: CompanionPageSnapshot, records: list[Any]
    ) -> dict[str, Any]:
        value = snapshot.payload
        dates = self._personal_dates(value["available_dates"], records)
        selected_date = value["selected_date"] or (dates[0] if dates else "")
        if selected_date and selected_date not in dates:
            dates.insert(0, selected_date)
        day = value["day"]
        plan = copy.deepcopy(day["plan"])
        current_item = copy.deepcopy(day["current_item"])
        memory_plan = self._schedule_memory_plan_for_date(records, selected_date)
        if memory_plan and (plan.get("source") == "none" or not plan.get("items")):
            plan = memory_plan
            current_item = {
                "index": None,
                "time": "",
                "activity": "",
                "mood": "",
                "message_seed": "",
            }
        formal_details = [
            {
                "key": item["id"],
                "index": "" if item["index"] is None else item["index"],
                "status": item["status"],
                "time": item["time"],
                "summary": item["summary"],
                "today_events": list(item["today_events"]),
                "proactive_events": list(item["proactive_events"]),
                "state_variables": list(item["state_variables"]),
            }
            for item in day["details"]
        ]
        details = self._merge_companion_details(
            formal_details, self._schedule_memory_details(records, selected_date, plan)
        )
        album = [
            self._project_companion_photo(item, snapshot.mode) for item in day["photos"]
        ]
        subjective = self._merge_personal_subjective_memories(
            day["diaries"], selected_date, records
        )
        return {
            "available": True,
            "plugin_name": COMPANION_PLUGIN_ID,
            "daily_plan_enabled": value["features"]["daily_plan_enabled"],
            "detail_enabled": value["features"]["detail_enhancement_enabled"],
            "bridge_available": value["coordination"]["available"],
            "bridge_state": value["coordination"]["state"],
            "bridge_reason": value["coordination"]["reason_code"],
            "selected_date": selected_date,
            "dates": dates,
            "snapshot": {
                "bot_name": day["bot_name"],
                "plan": plan,
                "current_item": current_item,
                "daily_state": copy.deepcopy(day["daily_state"]),
                "details": details,
                "album": album,
                "subjective_memories": subjective,
            },
        }

    def _project_companion_photo(
        self, item: dict[str, Any], mode: str
    ) -> dict[str, Any]:
        title = {
            "daily_outfit": "每日穿搭图",
            "recent_photo": "近期自拍",
            "life_photo": "生活分享图",
        }.get(item["kind"], "近期照片")
        query_parts = (
            [
                f"ref={quote(item['photo_ref'], safe='')}",
                f"date={quote(item['date'], safe='')}",
                f"id={quote(item['id'], safe='')}",
            ]
            if item.get("photo_ref")
            else []
        )
        if query_parts:
            query_parts.append(f"mode={quote(mode, safe='')}")
        query = "&".join(query_parts)
        return {
            "id": item["id"],
            "date": item["date"],
            "kind": item["kind"],
            "title": title,
            "url": f"{PAGE_API_PREFIXES[0]}/companion/personal-photo?{query}"
            if query
            else "",
            "image_data_url": f"/companion/personal-photo-data?{query}"
            if query
            else "",
            "exists": item["available"],
            "backend": "",
            "prompt": "",
            "note": "",
            "error": item["error_code"],
            "generated_at": self._timestamp_label(item["generated_at"]),
        }

    def _personal_dates(
        self, companion_dates: list[str], records: list[Any]
    ) -> list[str]:
        dates = set(companion_dates)
        for record in records:
            if (
                self._is_personal_action(record)
                or self._is_personal_schedule_memory(record)
                or self._is_personal_subjective_memory(record)
            ):
                value = self._memory_date_key(record)
                if value:
                    dates.add(value)
        return sorted(dates, reverse=True)[:180]

    @staticmethod
    def _personal_action_projection(record: Any) -> dict[str, Any]:
        metadata = getattr(record, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            image_count = max(0, min(8, int(metadata.get("image_count") or 0)))
        except (TypeError, ValueError):
            image_count = 0
        return {
            "id": clean_text(getattr(record, "id", ""), 160),
            "memory_type": clean_text(getattr(record, "memory_type", ""), 80),
            "visibility": "bot_self",
            "reality_level": clean_text(getattr(record, "reality_level", ""), 40),
            "content": clean_text(getattr(record, "content", ""), 520),
            "tags": [
                clean_text(tag, 80)
                for tag in (getattr(record, "tags", []) or [])
                if clean_text(tag, 80)
            ][:8],
            "created_at": clean_text(getattr(record, "created_at", ""), 80),
            "occurred_at": clean_text(getattr(record, "occurred_at", ""), 80),
            "metadata": {
                "action_label": clean_text(metadata.get("action_label"), 80),
                "image_count": image_count,
                "text": clean_text(metadata.get("text"), 220),
            },
        }

    def _merge_personal_subjective_memories(
        self,
        companion_diaries: list[dict[str, Any]],
        selected_date: str,
        records: list[Any],
    ) -> list[dict[str, Any]]:
        rows = [copy.deepcopy(item) for item in companion_diaries]
        seen = {
            (
                clean_text(item.get("date"), 16),
                clean_text(item.get("summary"), 220),
                clean_text(item.get("body"), 520),
            )
            for item in rows
        }
        for record in records:
            if not self._is_personal_subjective_memory(record):
                continue
            record_date = self._memory_date_key(record)
            if selected_date and record_date != selected_date:
                continue
            metadata = getattr(record, "metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            body = clean_text(getattr(record, "content", ""), 520)
            key = (record_date, "梦境碎片", body)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "date": record_date,
                    "summary": "梦境碎片",
                    "body": body,
                    "share_seed": "",
                    "tags": [
                        value
                        for value in (
                            "梦境碎片",
                            clean_text(metadata.get("dream_type"), 40),
                            clean_text(metadata.get("dream_mood"), 40),
                        )
                        if value
                    ][:8],
                    "today_events": [],
                    "proactive_events": [],
                    "long_term_events": [],
                }
            )
            if len(rows) >= 6:
                break
        return rows[:6]

    async def _resolve_companion_personal_photo_path_from_request(
        self,
    ) -> Path | dict[str, Any]:
        status = self._private_companion_status()
        if not status["available"]:
            return {
                "error": status.get("reason") or "private companion unavailable",
                "status": 404,
            }
        plugin = status.get("plugin")
        data = getattr(plugin, "data", {}) if plugin is not None else {}
        if not isinstance(data, dict):
            data = {}
        selected_date = clean_text(request.args.get("date", ""), 16)
        photo_id = clean_text(request.args.get("id"), 120)
        # When looking up by photo_id, skip date filtering so photos from any date can be served
        lookup_date = "" if photo_id else selected_date
        records: list[Any] = []
        try:
            records = await self.plugin.service.store.list_memories(
                limit=1200,
                include_pending=False,
                query=selected_date if selected_date and not photo_id else "",
                visibility="bot_self",
            )
        except Exception:
            records = []
        for item in self._private_companion_album(
            data,
            lookup_date,
            records,
            limit=1200 if photo_id else 80,
            plugin=plugin,
            include_local_path=True,
        ):
            if photo_id and clean_text(item.get("id"), 120) != photo_id:
                continue
            raw_path = clean_text(item.get("_local_path"), 500)
            if not raw_path:
                continue
            resolved = self._safe_companion_photo_path(raw_path, plugin)
            if resolved is not None:
                return resolved
        return {"error": "photo_not_found", "status": 404}
