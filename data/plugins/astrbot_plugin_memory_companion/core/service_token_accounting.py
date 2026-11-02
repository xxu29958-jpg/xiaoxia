from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from .astrbot_compat import (
    logger,
)
from .models import (
    clean_text,
    json_dumps,
    json_loads,
)
from .service_support import (
    LOCAL_TZ,
)


class MemoryCompanionServiceTokenAccounting:
    """Token accounting; state and transaction ownership remain in the facade."""

    def _load_token_usage(self) -> dict[str, Any]:
        try:
            if not self.token_usage_path.exists():
                return {}
            payload = json.loads(self.token_usage_path.read_text(encoding="utf-8"))
            return (
                self._normalize_token_usage(payload)
                if isinstance(payload, dict)
                else {}
            )
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] Token 统计读取失败，已从空统计开始: %s", exc
            )
            return {}

    def _save_token_usage(self, *, force: bool = False) -> None:
        now_ts = time.time()
        if not force and now_ts - self._token_usage_last_save_at < 30:
            return
        self._token_usage_last_save_at = now_ts
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self.token_usage_path.with_suffix(
                self.token_usage_path.suffix + ".tmp"
            )
            tmp_path.write_text(
                json.dumps(
                    self._token_usage if isinstance(self._token_usage, dict) else {},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp_path.replace(self.token_usage_path)
        except Exception as exc:
            logger.debug("[MemoryCompanion] Token 统计保存失败: %s", exc)

    def token_usage_summary(self) -> dict[str, Any]:
        usage = self._token_usage if isinstance(self._token_usage, dict) else {}
        payload = self._normalize_token_usage(json_loads(json_dumps(usage), {}))
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "token_usage_schema_version": 2,
                "available": True,
                "display_name": "我会牢牢记住你",
                "plugin_name": "astrbot_plugin_memory_companion",
                "counted_in_private_companion_budget": False,
                "note": "仅展示记忆插件自身模型调用；已确认 Token 来自 Provider 用量，估算 Token 只用于标记无用量返回或失败请求，不计入陪伴插件每日 Token 限额。",
            }
        )
        return payload

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _estimate_token_count(text: str) -> int:
        raw = str(text or "")
        if not raw:
            return 0
        ascii_chars = sum(1 for ch in raw if ord(ch) < 128)
        non_ascii_chars = max(0, len(raw) - ascii_chars)
        return max(1, int(ascii_chars / 4.0 + non_ascii_chars / 1.6))

    @classmethod
    def _normalize_token_usage(cls, payload: Any) -> dict[str, Any]:
        """Add separated reported/estimated counters to legacy statistics."""
        if not isinstance(payload, dict):
            return {}

        def visit(value: Any) -> None:
            if not isinstance(value, dict):
                return
            if "calls" in value:
                total = max(0, cls._safe_int(value.get("total_tokens")))
                estimated = max(0, cls._safe_int(value.get("estimated_tokens")))
                reported = cls._safe_int(value.get("reported_tokens"), -1)
                if reported < 0:
                    reported = max(0, total - estimated)
                else:
                    reported = max(0, reported)
                if "estimated_tokens" not in value:
                    estimated = max(0, total - reported)
                value["total_tokens"] = total
                value["reported_tokens"] = reported
                value["estimated_tokens"] = estimated
                value.setdefault("estimated_calls", 0)
            for child in value.values():
                visit(child)

        visit(payload)
        payload.setdefault("token_usage_schema_version", 2)
        return payload

    @staticmethod
    def _usage_raw_value(usage: Any, key: str) -> Any:
        current = usage
        if not current:
            return None
        for part in str(key or "").split("."):
            if not part:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                return None
        return current

    @classmethod
    def _usage_value(cls, usage: Any, *keys: str) -> int:
        if not usage:
            return 0
        for key in keys:
            try:
                parsed = int(cls._usage_raw_value(usage, key))
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                return parsed
        return 0

    def _extract_token_usage(
        self, resp: Any, prompt: str, completion: str
    ) -> dict[str, Any]:
        candidates = [
            getattr(resp, "usage", None),
            getattr(resp, "token_usage", None),
            getattr(resp, "raw_usage", None),
        ]
        raw_completion = getattr(resp, "raw_completion", None)
        if raw_completion is not None:
            candidates.append(getattr(raw_completion, "usage", None))
        raw_response = getattr(resp, "raw_response", None)
        if isinstance(raw_response, dict):
            candidates.extend(
                [raw_response.get("usage"), raw_response.get("token_usage")]
            )
        usage = next((item for item in candidates if item), None)
        prompt_tokens = self._usage_value(
            usage, "prompt_tokens", "input_tokens", "prompt", "input"
        )
        completion_tokens = self._usage_value(
            usage, "completion_tokens", "output_tokens", "completion", "output"
        )
        total_tokens = self._usage_value(usage, "total_tokens", "total")
        cache_read_tokens = self._usage_value(
            usage,
            "input_cached",
            "prompt_tokens_details.cached_tokens",
            "input_tokens_details.cached_tokens",
            "input_token_details.cached_tokens",
            "input_token_details.cache_read",
            "cache_read_input_tokens",
            "cache_read_tokens",
            "prompt_cache_hit_tokens",
        )
        cache_write_tokens = self._usage_value(
            usage,
            "cache_creation_input_tokens",
            "cache_creation_tokens",
            "cache_write_input_tokens",
            "cache_write_tokens",
            "prompt_cache_creation_tokens",
        )
        cached_tokens = self._usage_value(
            usage,
            "input_cached",
            "cached_tokens",
            "prompt_cached_tokens",
            "input_cached_tokens",
            "prompt_tokens_details.cached_tokens",
            "input_tokens_details.cached_tokens",
            "input_token_details.cached_tokens",
        )
        if cached_tokens <= 0:
            cached_tokens = cache_read_tokens
        reported_prompt_tokens = max(0, prompt_tokens)
        reported_completion_tokens = max(0, completion_tokens)
        reported_tokens = 0
        estimated_tokens = 0
        if total_tokens <= 0:
            prompt_estimated = reported_prompt_tokens <= 0
            completion_estimated = reported_completion_tokens <= 0
            if prompt_estimated:
                prompt_tokens = self._estimate_token_count(prompt)
            if completion_estimated:
                completion_tokens = self._estimate_token_count(completion)
            total_tokens = prompt_tokens + completion_tokens
            reported_tokens = reported_prompt_tokens + reported_completion_tokens
            estimated_tokens = max(0, total_tokens - reported_tokens)
        else:
            reported_tokens = total_tokens if usage else 0
            estimated_tokens = 0 if usage else total_tokens
        estimated = estimated_tokens > 0
        usage_source = (
            "mixed"
            if reported_tokens > 0 and estimated_tokens > 0
            else "provider"
            if reported_tokens > 0
            else "estimated"
        )
        return {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
            "reported_tokens": max(0, reported_tokens),
            "estimated_tokens": max(0, estimated_tokens),
            "cached_tokens": max(0, cached_tokens),
            "cache_read_tokens": max(0, cache_read_tokens),
            "cache_write_tokens": max(0, cache_write_tokens),
            "estimated": bool(estimated),
            "usage_source": usage_source,
        }

    def _record_token_usage(
        self,
        *,
        task: str,
        provider_id: str,
        prompt: str = "",
        completion: str = "",
        resp: Any = None,
        success: bool = True,
        elapsed_ms: int = 0,
        error: str = "",
    ) -> None:
        usage = self._extract_token_usage(resp, prompt, completion)
        now_dt = datetime.now(LOCAL_TZ)
        now_ts = time.time()
        day = now_dt.strftime("%Y-%m-%d")
        hour = now_dt.strftime("%Y-%m-%dT%H:00")
        store = self._token_usage if isinstance(self._token_usage, dict) else {}
        self._token_usage = store
        totals = store.setdefault("totals", {})
        by_provider = store.setdefault("by_provider", {})
        by_task = store.setdefault("by_task", {})
        by_day = store.setdefault("by_day", {})
        by_day_provider = store.setdefault("by_day_provider", {})
        by_day_task = store.setdefault("by_day_task", {})
        by_hour = store.setdefault("by_hour", {})
        recent = store.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            store["recent"] = recent
        provider_key = clean_text(provider_id, 160) or "(default)"
        task_key = clean_text(task, 60) or "other"

        def bump(bucket: dict[str, Any]) -> None:
            bucket["calls"] = self._safe_int(bucket.get("calls")) + 1
            bucket["success"] = self._safe_int(bucket.get("success")) + (
                1 if success else 0
            )
            bucket["errors"] = self._safe_int(bucket.get("errors")) + (
                0 if success else 1
            )
            bucket["prompt_tokens"] = (
                self._safe_int(bucket.get("prompt_tokens")) + usage["prompt_tokens"]
            )
            bucket["completion_tokens"] = (
                self._safe_int(bucket.get("completion_tokens"))
                + usage["completion_tokens"]
            )
            bucket["total_tokens"] = (
                self._safe_int(bucket.get("total_tokens")) + usage["total_tokens"]
            )
            bucket["reported_tokens"] = (
                self._safe_int(bucket.get("reported_tokens")) + usage["reported_tokens"]
            )
            bucket["cached_tokens"] = (
                self._safe_int(bucket.get("cached_tokens")) + usage["cached_tokens"]
            )
            bucket["cache_read_tokens"] = (
                self._safe_int(bucket.get("cache_read_tokens"))
                + usage["cache_read_tokens"]
            )
            bucket["cache_write_tokens"] = (
                self._safe_int(bucket.get("cache_write_tokens"))
                + usage["cache_write_tokens"]
            )
            bucket["estimated_tokens"] = (
                self._safe_int(bucket.get("estimated_tokens"))
                + usage["estimated_tokens"]
            )
            bucket["estimated_calls"] = self._safe_int(
                bucket.get("estimated_calls")
            ) + (1 if usage["estimated"] else 0)
            bucket["elapsed_ms"] = self._safe_int(bucket.get("elapsed_ms")) + max(
                0, int(elapsed_ms or 0)
            )
            bucket["last_ts"] = now_ts

        for target in (
            totals,
            by_provider.setdefault(provider_key, {}),
            by_task.setdefault(task_key, {}),
            by_day.setdefault(day, {}),
            by_day_provider.setdefault(day, {}).setdefault(provider_key, {}),
            by_day_task.setdefault(day, {}).setdefault(task_key, {}),
            by_hour.setdefault(hour, {}),
        ):
            if isinstance(target, dict):
                bump(target)

        recent.append(
            {
                "ts": now_ts,
                "time": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": provider_key,
                "task": task_key,
                "success": bool(success),
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "total_tokens": usage["total_tokens"],
                "reported_tokens": usage["reported_tokens"],
                "estimated_tokens": usage["estimated_tokens"],
                "usage_source": usage["usage_source"],
                "cached_tokens": usage["cached_tokens"],
                "cache_read_tokens": usage["cache_read_tokens"],
                "cache_write_tokens": usage["cache_write_tokens"],
                "estimated": usage["estimated"],
                "elapsed_ms": max(0, int(elapsed_ms or 0)),
                "prompt_chars": len(str(prompt or "")),
                "completion_chars": len(str(completion or "")),
                "error": clean_text(error, 160),
            }
        )
        del recent[:-240]
        store["updated_at"] = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        self._save_token_usage()
