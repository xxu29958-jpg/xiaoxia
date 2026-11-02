from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
from typing import Any

from .companion_page_bridge import (
    COMPANION_PLUGIN_ID,
    MEMORY_PAGE_PHOTO_VERSION,
    MEMORY_PAGE_SNAPSHOT_VERSION,
    CompanionPageBridgeError,
    seal_memory_page_snapshot,
    validate_memory_page_snapshot,
)
from .companion_page_legacy_support import (
    _PHOTO_REF_RE,
    _available_dates,
    _bounded_data_snapshot,
    _date,
    _date_from_timestamp,
    _details_for_date,
    _diaries_for_date,
    _fail,
    _LegacyPhoto,
    _opaque_identifier,
    _plan_for_date,
    _plan_item,
    _text,
    _timestamp,
)


class LegacyCompanionPageAdapterProjection:
    """Projection; shared runtime resources belong to the facade."""

    async def _photos_for_date(
        self,
        data: dict[str, Any],
        selected_date: str,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, _LegacyPhoto]]]:
        sources: list[tuple[dict[str, Any], str]] = []
        outfit = data.get("daily_outfit_photo")
        if isinstance(outfit, dict):
            sources.append((outfit, "daily_outfit"))
        outfit_history = data.get("daily_outfit_history")
        if isinstance(outfit_history, list):
            sources.extend(
                (item, "daily_outfit")
                for item in reversed(outfit_history[-128:])
                if isinstance(item, dict)
            )
        recent = data.get("recent_photo_generations")
        if isinstance(recent, list):
            sources.extend(
                (item, "recent_photo")
                for item in recent[:256]
                if isinstance(item, dict)
            )

        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for index, (item, default_kind) in enumerate(sources):
            item_date = _date(item.get("date")) or _date_from_timestamp(
                item.get("generated_at") or item.get("ts")
            )
            if item_date != selected_date:
                continue
            raw_path = item.get("path")
            path_identity = (
                os.fspath(raw_path) if isinstance(raw_path, (str, os.PathLike)) else ""
            )
            raw_kind = _text(item.get("kind"), 40).lower()
            kind = (
                "life_photo"
                if default_kind == "recent_photo" and "life" in raw_kind
                else default_kind
            )
            identity_key = (kind, item_date, path_identity)
            if identity_key in seen:
                continue
            seen.add(identity_key)
            opaque = _opaque_identifier(
                self._secret,
                (
                    f"photo|{self.instance_generation}|{item_date}|{kind}|"
                    f"{index}|{path_identity}"
                ),
            )
            candidates.append(
                {
                    "id": f"photo_{opaque}",
                    "date": item_date,
                    "kind": kind,
                    "generated_at": _timestamp(
                        item.get("generated_at") or item.get("ts")
                    ),
                    "raw_path": raw_path,
                }
            )
        candidates.sort(key=lambda item: item["generated_at"], reverse=True)

        rows: list[dict[str, Any]] = []
        staged: list[tuple[str, _LegacyPhoto]] = []
        for candidate in candidates[:8]:
            photo_ref = ""
            available = False
            error_code = "memory_page_photo_unavailable"
            raw_path = candidate["raw_path"]
            if raw_path:
                try:
                    photo_ref, record = await self._thread_call(
                        self._stage_photo_sync,
                        raw_path,
                    )
                    staged.append((photo_ref, record))
                    available = True
                    error_code = ""
                except asyncio.CancelledError:
                    raise
                except CompanionPageBridgeError as exc:
                    error_code = exc.code
            rows.append(
                {
                    "id": candidate["id"],
                    "date": candidate["date"],
                    "kind": candidate["kind"],
                    "generated_at": candidate["generated_at"],
                    "available": available,
                    "error_code": error_code,
                    "photo_ref": photo_ref,
                }
            )
        return rows, staged

    async def export_snapshot(self, selected_date: str = "") -> dict[str, Any]:
        plugin = self._live_plugin()
        data_lock = self._data_lock
        if (
            data_lock is None
            or not hasattr(data_lock, "__aenter__")
            or not hasattr(data_lock, "__aexit__")
        ):
            _fail("memory_page_snapshot_state_unavailable")
        try:
            async with data_lock:
                self._live_plugin()
                data = _bounded_data_snapshot(plugin.data)
                daily_plan_enabled = getattr(plugin, "enable_daily_plan", None) is True
                detail_enhancement_enabled = (
                    getattr(plugin, "enable_detail_enhancement", None) is True
                )
                bot_name = _text(getattr(plugin, "bot_name", ""), 80)
                current_item_getter = getattr(plugin, "_get_current_plan_item", None)
                dates = _available_dates(data)
                selected = selected_date or (dates[0] if dates else "")
                plan = (
                    _plan_for_date(data, selected)
                    if selected
                    else {
                        "date": "",
                        "source": "none",
                        "items": [],
                    }
                )
                current_item = _plan_item({}, None)
                current_plan = data.get("daily_plan")
                if (
                    selected
                    and isinstance(current_plan, dict)
                    and _date(current_plan.get("date")) == selected
                    and callable(current_item_getter)
                ):
                    try:
                        candidate = current_item_getter(current_plan)
                    except Exception:
                        candidate = None
                    if isinstance(candidate, dict):
                        current_index = None
                        items = current_plan.get("items")
                        if isinstance(items, list):
                            for candidate_index, item in enumerate(items[:18]):
                                if item is candidate or item == candidate:
                                    current_index = candidate_index
                                    break
                        current_item = _plan_item(candidate, current_index)
                self._live_plugin()
        except asyncio.CancelledError:
            raise
        except CompanionPageBridgeError:
            raise
        except Exception:
            _fail("memory_page_snapshot_state_unavailable")
        self._live_plugin()
        coordination = self._coordination(plugin)
        self._live_plugin()
        daily = data.get("daily_state")
        state_date = (
            _date(daily.get("date")) or _date(data.get("state_generated_day"))
            if isinstance(daily, dict)
            else ""
        )
        if not selected or not isinstance(daily, dict) or state_date != selected:
            daily = {}
            state_date = ""
        try:
            energy = (
                int(daily.get("energy")) if daily.get("energy") is not None else None
            )
        except (TypeError, ValueError):
            energy = None
        if energy is not None and not 0 <= energy <= 100:
            energy = None
        photos, staged_photo_refs = await self._photos_for_date(data, selected)
        self._live_plugin()
        payload = {
            "version": MEMORY_PAGE_SNAPSHOT_VERSION,
            "source_plugin_id": COMPANION_PLUGIN_ID,
            "instance_generation": self.instance_generation,
            "selected_date": selected,
            "available_dates": dates,
            "features": {
                "daily_plan_enabled": daily_plan_enabled,
                "detail_enhancement_enabled": detail_enhancement_enabled,
            },
            "coordination": coordination,
            "day": {
                "date": selected,
                "bot_name": bot_name,
                "plan": plan,
                "current_item": current_item,
                "daily_state": {
                    "date": state_date,
                    "energy": energy,
                    "mood_bias": _text(daily.get("mood_bias") or daily.get("mood"), 80),
                    "sleep": _text(daily.get("sleep"), 80),
                    "weather": _text(daily.get("weather"), 80),
                    "note": _text(daily.get("note") or daily.get("summary"), 180),
                },
                "details": _details_for_date(data, selected, self._secret),
                "photos": photos,
                "diaries": _diaries_for_date(data, selected),
            },
        }
        sealed = seal_memory_page_snapshot(payload)
        validated = validate_memory_page_snapshot(
            sealed,
            expected_generation=self.instance_generation,
            expected_selected_date=selected_date,
        )
        self._live_plugin()
        self._commit_photo_refs(staged_photo_refs)
        return validated

    def _read_photo_sync(self, photo_ref: str, record: _LegacyPhoto) -> bytes:
        if time.monotonic() >= record.expires_at:
            _fail("memory_page_photo_ref_expired")
        content, identity = self._read_descriptor(
            record.root,
            record.parts,
            expected=record,
        )
        if (
            identity.st_dev != record.device
            or identity.st_ino != record.inode
            or identity.st_size != record.size
            or identity.st_mtime_ns != record.mtime_ns
            or hashlib.sha256(content).hexdigest() != record.sha256
            or self._mime(content) != record.mime_type
        ):
            _fail("memory_page_photo_changed")
        return content

    async def read_photo(self, photo_ref: str) -> dict[str, Any]:
        match = _PHOTO_REF_RE.fullmatch(photo_ref)
        if match is None:
            _fail("memory_page_photo_ref_invalid")
        if match.group(1) != self.instance_generation[:12]:
            _fail("memory_page_photo_ref_stale")
        self._live_plugin()
        with self._lock:
            record = self._photos.get(photo_ref)
            if record is None:
                _fail("memory_page_photo_ref_expired")
            if time.monotonic() >= record.expires_at:
                self._photos.pop(photo_ref, None)
                _fail("memory_page_photo_ref_expired")
            self._photos.move_to_end(photo_ref)
        content = await self._thread_call(self._read_photo_sync, photo_ref, record)
        self._live_plugin()
        now = time.monotonic()
        with self._lock:
            current = self._photos.get(photo_ref)
            if current is not record:
                _fail("memory_page_photo_ref_expired")
            if record.expires_at <= now:
                self._photos.pop(photo_ref, None)
                _fail("memory_page_photo_ref_expired")
        return {
            "version": MEMORY_PAGE_PHOTO_VERSION,
            "source_plugin_id": COMPANION_PLUGIN_ID,
            "instance_generation": self.instance_generation,
            "photo_ref": photo_ref,
            "mime_type": record.mime_type,
            "size": len(content),
            "sha256": record.sha256,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
