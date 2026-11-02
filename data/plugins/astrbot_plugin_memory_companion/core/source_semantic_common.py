from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

"""Rebuildable semantic source projections and their independent change queue.

The module stores references, spans, versions, and optional vectors. It never
stores a second copy of source text; callers must revalidate source authority.
"""


FRAGMENT_TARGET_CHARS = 600


FRAGMENT_OVERLAP_CHARS = 80


FRAGMENT_PROFILE = "unicode-codepoint-overlap-v1"


_TRACKED_GENERATION_STATES_SQL = "('building','ready','paused')"


_SOURCE_EVENT_SQL = "('user_message','bot_response')"


_SOURCE_PARTITION_FIELDS = (
    "scope",
    "session_id",
    "object_id",
    "owner_bot_id",
    "platform",
    "persona_id",
    "participant_user_id",
)


def split_source_fragments(
    text: str,
    *,
    target_chars: int = FRAGMENT_TARGET_CHARS,
    overlap_chars: int = FRAGMENT_OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    """Split readable text by Unicode code-point offsets, preserving the tail."""
    if not isinstance(text, str):
        raise TypeError("source_text_must_be_string")
    if (
        isinstance(target_chars, bool)
        or not isinstance(target_chars, int)
        or target_chars < 1
    ):
        raise ValueError("invalid_fragment_target")
    if (
        isinstance(overlap_chars, bool)
        or not isinstance(overlap_chars, int)
        or not 0 <= overlap_chars < target_chars
    ):
        raise ValueError("invalid_fragment_overlap")
    if not text:
        return []

    step = target_chars - overlap_chars
    fragments = []
    for start in range(0, len(text), step):
        end = min(len(text), start + target_chars)
        fragments.append(
            {"char_start": start, "char_end": end, "text": text[start:end]}
        )
        if end == len(text):
            break
    return fragments


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _source_snapshot(alias: str) -> str:
    return f"""json_object(
        'source_id',{alias}.id,'event_type',{alias}.event_type,'scope',{alias}.scope,
        'session_id',{alias}.session_id,'subject_id',{alias}.subject_id,'object_id',{alias}.object_id,
        'owner_bot_id',CASE WHEN json_valid({alias}.metadata) THEN COALESCE(json_extract({alias}.metadata,'$.owner_bot_id'),'') ELSE '' END,
        'bot_id',CASE WHEN json_valid({alias}.metadata) THEN COALESCE(json_extract({alias}.metadata,'$.bot_id'),'') ELSE '' END,
        'platform',CASE WHEN json_valid({alias}.metadata) THEN COALESCE(json_extract({alias}.metadata,'$.platform'),'') ELSE '' END,
        'persona_id',CASE WHEN json_valid({alias}.metadata) THEN COALESCE(json_extract({alias}.metadata,'$.persona_id'),'') ELSE '' END,
        'participant_user_id',CASE WHEN json_valid({alias}.metadata) THEN COALESCE(json_extract({alias}.metadata,'$.participant_user_id'),'') ELSE '' END,
        'occurred_at',{alias}.occurred_at,'created_at',{alias}.created_at
    )"""


def pack_semantic_vector(values: Sequence[float]) -> bytes:
    """Serialize a finite vector using the same stable float64 format as memory embeddings."""
    checked: list[float] = []
    for value in values:
        number = float(value)
        if number != number or number in {float("inf"), float("-inf")}:
            raise ValueError("semantic_vector_invalid")
        checked.append(number)
    if not checked:
        raise ValueError("semantic_vector_invalid")
    return struct.pack(f"<{len(checked)}d", *checked)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(value) if isinstance(value, str) else {}
    except (TypeError, ValueError):
        decoded = {}
    return decoded if isinstance(decoded, dict) else {}


def _trusted_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _source_position(source: Mapping[str, Any]) -> tuple[float, str, str]:
    value = source.get("occurred_at")
    if _trusted_timestamp(value) is None:
        raise ValueError("semantic_window_time_untrusted")
    try:
        sort_time = float(source.get("source_sort_time"))
    except (TypeError, ValueError):
        sort_time = float("nan")
    if sort_time != sort_time:
        # Python's ISO parser handles timezone equivalence without SQLite's Julian conversion.
        sort_time = _trusted_timestamp(value).timestamp()
    return (
        sort_time,
        str(source.get("created_at") or ""),
        str(source.get("source_id") or source.get("id") or ""),
    )


def _source_partition(source: Mapping[str, Any]) -> tuple[str, ...]:
    metadata = (
        source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
    )
    return (
        str(source.get("scope") or ""),
        str(source.get("session_id") or ""),
        str(source.get("object_id") or ""),
        str(metadata.get("owner_bot_id") or ""),
        str(metadata.get("platform") or ""),
        str(metadata.get("persona_id") or ""),
        str(metadata.get("participant_user_id") or ""),
    )


def semantic_document_id(
    generation: str,
    source_id: str,
    view_kind: str,
    char_start: int,
    char_end: int,
) -> str:
    return "sd_" + _digest([generation, source_id, view_kind, char_start, char_end])
