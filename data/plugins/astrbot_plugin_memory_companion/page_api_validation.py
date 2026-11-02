from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from quart import request

from .core.models import clean_text


class PluginPageApiValidation:
    """Validation; shared runtime resources belong to the facade."""

    def _cmd_config_provider_configs(self) -> list[dict[str, Any]]:
        config_path = self._astrbot_cmd_config_path()
        if not config_path.exists():
            return []
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return []
        if not isinstance(data, dict):
            return []

        source_configs = data.get("provider_sources")
        if not isinstance(source_configs, list):
            source_configs = []
        provider_configs = data.get("provider")
        if not isinstance(provider_configs, list):
            return []

        sources = {
            clean_text(source.get("id"), 160): source
            for source in source_configs
            if isinstance(source, dict) and clean_text(source.get("id"), 160)
        }
        rows: list[dict[str, Any]] = []
        for provider in provider_configs:
            if not isinstance(provider, dict):
                continue
            source = sources.get(clean_text(provider.get("provider_source_id"), 160))
            rows.append({**(source or {}), **provider})
        return rows

    def _query_int(self, key: str, default: int) -> int:
        return self._int(request.args.get(key), default)

    @staticmethod
    def _optional_choice(
        payload: dict[str, Any], key: str, allowed: set[str] | frozenset[str]
    ) -> Any:
        if key not in payload:
            return None
        value = clean_text(payload.get(key), 40).lower()
        return value if value in allowed else False

    @staticmethod
    def _optional_iso_timestamp(payload: dict[str, Any], key: str) -> str | None:
        if key not in payload:
            return None
        value = clean_text(payload.get(key), 80)
        if not value:
            return ""
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{key} must be an ISO-8601 timestamp") from exc
        return value

    @staticmethod
    def _optional_unit_score(payload: dict[str, Any], key: str) -> float | None:
        if key not in payload:
            return None
        try:
            value = float(payload.get(key))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a number between 0 and 1") from exc
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{key} must be a number between 0 and 1")
        return value

    @staticmethod
    def _iso_timestamp_number(value: str) -> float:
        parsed = datetime.fromisoformat(clean_text(value, 80).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return parsed.timestamp()

    @staticmethod
    def _acl_window_error(scope: str, window_id: str) -> str:
        if scope not in {"private", "group"}:
            return "ACL scope must be private or group"
        if not window_id:
            return "ACL window id is required"
        return ""

    @staticmethod
    def _acl_effect(value: Any) -> str:
        return (
            "deny"
            if clean_text(value, 20).lower() in {"deny", "block", "blacklist"}
            else "allow"
        )

    @staticmethod
    def _acl_mode(value: Any) -> str:
        text = clean_text(value, 20).lower()
        if not text:
            return ""
        return "blacklist" if text in {"blacklist", "deny", "block"} else "whitelist"

    @staticmethod
    def _int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "off", "no", "否", "关"}
        return bool(value)
