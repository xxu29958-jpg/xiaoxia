from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from . import bot_personal_contract
from .bot_personal_contract import (
    BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
    BOT_PERSONAL_LEGACY_CANONICAL_SCHEMA_VERSIONS,
    BOT_PERSONAL_MAX_PAYLOAD_BYTES,
    BOT_PERSONAL_MEMORY_DOMAIN,
    BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION,
    BOT_PERSONAL_WINDOWS,
    WINDOW_SLUGS,
)
from .bot_personal_dto_common import (
    _FORBIDDEN_KEY_PARTS,
    _SENSITIVE_VALUE_PATTERNS,
    BOT_PERSONAL_MAX_RECORD_VERSION,
    BotPersonalValidationError,
)


def _key_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _check_string(value: str, path: str) -> str:
    text = _text(value, BOT_PERSONAL_MAX_PAYLOAD_BYTES)
    if any(pattern.search(text) for pattern in _SENSITIVE_VALUE_PATTERNS):
        raise BotPersonalValidationError(
            "privacy_rejected",
            path,
            "credential, binary media or absolute path is not accepted",
        )
    return text


def sanitize_bot_personal_value(
    value: Any, *, path: str = "payload", depth: int = 0
) -> Any:
    if depth > 8:
        raise BotPersonalValidationError(
            "privacy_rejected", path, "payload nesting is too deep"
        )
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise BotPersonalValidationError(
                "payload_too_large", path, "too many fields"
            )
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _text(raw_key, 80)
            normalized = _key_name(key)
            if not key:
                raise BotPersonalValidationError("invalid", path, "empty field name")
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise BotPersonalValidationError(
                    "privacy_rejected",
                    f"{path}.{key}",
                    "sensitive or raw field is not accepted",
                )
            result[key] = sanitize_bot_personal_value(
                raw_value, path=f"{path}.{key}", depth=depth + 1
            )
        return result
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise BotPersonalValidationError(
            "privacy_rejected", path, "binary media is not accepted"
        )
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            raise BotPersonalValidationError(
                "payload_too_large", path, "too many list items"
            )
        return [
            sanitize_bot_personal_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        return _check_string(value, path)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise BotPersonalValidationError("invalid", path, "value is not JSON serializable")


def _json_size(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise BotPersonalValidationError(
            "invalid", "payload", f"payload is not JSON serializable: {exc}"
        ) from exc


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BotPersonalValidationError(
            "invalid", field, f"{field} must be ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BotPersonalValidationError(
            "invalid", field, f"{field} requires a timezone"
        )
    return parsed


def _canonical_text(value: Any, choices: frozenset[str], default: str) -> str:
    candidate = _text(value, 80).lower()
    return candidate if candidate in choices else default


def _string_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        reference = _text(item, 240)
        if reference and reference not in result:
            result.append(reference)
        if len(result) >= 30:
            break
    return result


def _canonical_mapping(canonical: str, archive: str) -> dict[str, Any]:
    return {
        "canonical_evidence_level": canonical,
        "archive_evidence_level": archive,
        "lossy": canonical != archive,
    }


def _raw_field(
    source: Mapping[str, Any], payload: Mapping[str, Any], key: str, default: Any = None
) -> Any:
    value = source.get(key)
    if value in (None, ""):
        value = payload.get(key, default)
    return value


def _derive_window(occurred_at: str) -> str:
    hour = _parse_datetime(occurred_at, "occurred_at").hour
    minutes = hour * 60 + _parse_datetime(occurred_at, "occurred_at").minute
    return bot_personal_contract.window_for_minutes(minutes)


def _record_id(
    memory_type: str,
    idempotency_key: str,
    *,
    canonical_schema_version: int = 1,
    owner_bot_id: str = "",
    persona_id: str = "",
) -> str:
    identity = (
        f"{BOT_PERSONAL_MEMORY_DOMAIN}|{owner_bot_id}|{persona_id}|{memory_type}|{idempotency_key}"
        if canonical_schema_version >= BOT_PERSONAL_CANONICAL_SCHEMA_VERSION
        else f"{BOT_PERSONAL_MEMORY_DOMAIN}|{memory_type}|{idempotency_key}"
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"botmem_{digest}"


def _fingerprint(data: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_bot_personal_idempotency_key(value: Any) -> str:
    if not isinstance(value, str):
        raise BotPersonalValidationError(
            "invalid", "idempotency_key", "idempotency_key must be a string"
        )
    key = _text(value, 240)
    if not key or any(ord(char) < 32 for char in key):
        raise BotPersonalValidationError(
            "invalid",
            "idempotency_key",
            "idempotency_key is required and must be printable",
        )
    normalized = _key_name(key)
    if any(part in normalized for part in _FORBIDDEN_KEY_PARTS) or any(
        pattern.search(key) for pattern in _SENSITIVE_VALUE_PATTERNS
    ):
        raise BotPersonalValidationError(
            "privacy_rejected", "idempotency_key", "unsafe idempotency_key"
        )
    return key


def _archive_time_and_references(key, now, safe, source):
    """Archive time and references."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    occurred = _text(_raw_field(source, safe, "occurred_at", ""), 80)
    if not occurred:
        occurred = current.isoformat(timespec="seconds")
    _parse_datetime(occurred, "occurred_at")
    record_date = _text(_raw_field(source, safe, "date", ""), 20) or occurred[:10]
    try:
        datetime.strptime(record_date, "%Y-%m-%d")
    except ValueError as exc:
        raise BotPersonalValidationError(
            "invalid", "date", "date must be YYYY-MM-DD"
        ) from exc
    record_window = _text(
        _raw_field(source, safe, "window", ""), 40
    ).lower() or _derive_window(occurred)
    if record_window not in BOT_PERSONAL_WINDOWS or record_window not in WINDOW_SLUGS:
        raise BotPersonalValidationError(
            "invalid", "window", "window is outside the shared contract"
        )
    try:
        confidence = float(source.get("certainty", 0.6))
    except (TypeError, ValueError) as exc:
        raise BotPersonalValidationError(
            "invalid", "certainty", "certainty must be numeric"
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise BotPersonalValidationError(
            "invalid", "certainty", "certainty must be between 0 and 1"
        )
    try:
        record_version = int(source.get("version", 1))
    except (TypeError, ValueError) as exc:
        raise BotPersonalValidationError(
            "invalid", "version", "version must be a positive integer"
        ) from exc
    if not 1 <= record_version <= BOT_PERSONAL_MAX_RECORD_VERSION:
        raise BotPersonalValidationError(
            "invalid",
            "version",
            f"version must be between 1 and {BOT_PERSONAL_MAX_RECORD_VERSION}",
        )
    schema = (
        _text(source.get("payload_schema_version"), 40)
        or BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION
    )
    if schema != BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION:
        raise BotPersonalValidationError(
            "invalid", "payload_schema_version", "unsupported payload schema"
        )
    refs_value = source.get("source_refs")
    if refs_value is None:
        refs_value = safe.get("source_refs")
    if refs_value is None:
        refs_value = [f"archive:{key}"]
    if not isinstance(refs_value, (list, tuple)):
        raise BotPersonalValidationError(
            "invalid", "source_refs", "source_refs must be a list"
        )
    refs_list: list[str] = []
    for item in refs_value:
        if not isinstance(item, str):
            raise BotPersonalValidationError(
                "invalid", "source_refs", "source_refs must contain strings"
            )
        reference = _check_string(_text(item, 240), "source_refs")
        if reference:
            refs_list.append(reference)
    refs = tuple(refs_list)
    if not refs:
        raise BotPersonalValidationError(
            "invalid", "source_refs", "source_refs must not be empty"
        )
    safe["source_refs"] = list(refs)

    return (
        current,
        occurred,
        record_date,
        record_window,
        confidence,
        record_version,
        schema,
        refs,
    )


def _archive_namespace(kind, source):
    """Archive namespace."""
    key = validate_bot_personal_idempotency_key(source.get("idempotency_key"))
    try:
        requested_canonical_schema = int(source.get("canonical_schema_version") or 1)
    except (TypeError, ValueError) as exc:
        raise BotPersonalValidationError(
            "invalid", "canonical_schema_version", "canonical schema must be numeric"
        ) from exc
    supported_canonical_schemas = {
        *BOT_PERSONAL_LEGACY_CANONICAL_SCHEMA_VERSIONS,
        BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
    }
    if requested_canonical_schema not in supported_canonical_schemas:
        raise BotPersonalValidationError(
            "invalid", "canonical_schema_version", "unsupported canonical schema"
        )
    owner_bot_id = _check_string(_text(source.get("owner_bot_id"), 120), "owner_bot_id")
    persona_id = _check_string(_text(source.get("persona_id"), 96), "persona_id")
    if requested_canonical_schema >= BOT_PERSONAL_CANONICAL_SCHEMA_VERSION:
        if not owner_bot_id or not persona_id:
            raise BotPersonalValidationError(
                "invalid_namespace",
                "owner_bot_id",
                "current Bot Personal schema requires owner_bot_id and persona_id",
            )
    else:
        owner_bot_id = ""
        persona_id = "legacy"
    derived_record_id = _record_id(
        kind,
        key,
        canonical_schema_version=requested_canonical_schema,
        owner_bot_id=owner_bot_id,
        persona_id=persona_id,
    )
    supplied_record_id = source.get("record_id")
    if supplied_record_id not in (None, ""):
        if not isinstance(supplied_record_id, str):
            raise BotPersonalValidationError(
                "invalid", "record_id", "record_id must be a string"
            )
        record_id = _check_string(_text(supplied_record_id, 120), "record_id")
        if record_id != derived_record_id:
            raise BotPersonalValidationError(
                "invalid", "record_id", "record_id must match the idempotency key"
            )

    return key, requested_canonical_schema, owner_bot_id, persona_id, derived_record_id
