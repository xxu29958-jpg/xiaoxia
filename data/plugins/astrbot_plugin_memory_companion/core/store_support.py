from __future__ import annotations

import math
import struct
import threading
from datetime import datetime, timezone
from typing import Any

from .models import (
    clean_text,
    json_loads,
)

_ACL_UNSET = object()


def _timeline_time_sql(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"COALESCE(julianday(NULLIF({prefix}occurred_at, '')), "
        f"julianday(NULLIF({prefix}created_at, '')), 0)"
    )


def _memory_time_sql(
    alias: str = "",
    *,
    include_updated: bool = False,
    include_valid_from: bool = False,
) -> str:
    prefix = f"{alias}." if alias else ""
    columns = (["valid_from"] if include_valid_from else []) + ["occurred_at"]
    if include_updated:
        columns.append("updated_at")
    columns.append("created_at")
    values = [f"julianday(NULLIF({prefix}{column}, ''))" for column in columns]
    return f"COALESCE({', '.join(values)}, 0)"


def _timeline_order_key(row: dict[str, Any]) -> tuple[float, str, str]:
    for value in (row.get("occurred_at"), row.get("created_at")):
        text = clean_text(value, 80)
        if not text:
            continue
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            timestamp = moment.astimezone(timezone.utc).timestamp()
            return (
                timestamp,
                clean_text(row.get("created_at"), 80),
                clean_text(row.get("id"), 160),
            )
        except (TypeError, ValueError, OverflowError):
            continue
    return (
        float("-inf"),
        clean_text(row.get("created_at"), 80),
        clean_text(row.get("id"), 160),
    )


class SharedTextCache:
    """Bounded, thread-safe text cache owned by the store.

    Consumers such as the retrieval engine are rebuilt per call, so a cache
    held on the instance only survives a single recall.  Keeping the entries
    here lets one invalidation broadcast drop them together with the other
    projections instead of leaving a copy that nobody can reach.
    """

    def __init__(self, max_entries: int = 4096) -> None:
        self._max_entries = max(1, int(max_entries or 1))
        self._entries: dict[Any, str] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> str | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: Any, value: str) -> None:
        with self._lock:
            if len(self._entries) >= self._max_entries:
                self._entries.clear()
            self._entries[key] = value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_EMBEDDING_VECTOR_FMT = "<d"


def _pack_embedding_vector(values: list[float]) -> bytes:
    """把向量序列化为二进制 bytes（兼容旧 JSON 文本读取）。"""
    floats = [float(item) for item in values if isinstance(item, (int, float))]
    if not floats:
        return b""
    return struct.pack(f"<{len(floats)}d", *floats)


def _unpack_embedding_vector(raw: Any) -> list[float]:
    """按二进制格式解析向量；若是旧 JSON 文本则回退 json 解析。"""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        payload = bytes(raw)
        count = len(payload) // 8
        if count <= 0:
            return []
        return list(struct.unpack(f"<{count}d", payload[: count * 8]))
    if isinstance(raw, str) and raw:
        payload = json_loads(raw, [])
        if isinstance(payload, list):
            vector: list[float] = []
            for item in payload:
                try:
                    vector.append(float(item))
                except Exception:
                    return []
            return vector
    return []


def _normalize_embedding_vector_values(values: list[float]) -> list[float]:
    """对向量做 L2 归一化，供候选缓存一次性归一化，避免每次召回重复计算。"""
    if not values or not all(math.isfinite(value) for value in values):
        return []
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm <= 0:
        return []
    return [value / norm for value in values]
