"""Bind derived core state to the actual incoming event, never model-selected IDs."""

from datetime import UTC, datetime

from .core_state_versions import CoreStateError, digest
from .models import utc_now
from .sensitive_data import redact_sensitive_text


def event_source(event):
    obj = getattr(event, "message_obj", None)
    parts = getattr(obj, "message", None)
    text = (
        "".join(str(p.text) for p in parts if type(p).__name__ == "Plain")
        if isinstance(parts, list)
        else event.get_message_str()
    )
    if not event.is_private_chat() or not text.strip():
        raise CoreStateError("core_source_required")
    # Use the ingress observation, not the time a delayed model calls the tool.
    source = {
        "kind": "user_message",
        "derivation": "model_tool_interpretation",
        "observation": "speech_transcript"
        if event.get_extra("voice_input")
        else "native_text",
        "origin": event.unified_msg_origin,
        "platform": event.get_platform_name(),
        "user_id": str(event.get_sender_id()),
        "bot_id": str(event.get_self_id()),
        "message_id": str(getattr(obj, "message_id", "") or ""),
        "received_ns": getattr(event, "_ada_memory_received_ns", 0),
        "text": redact_sensitive_text(text),
        "redacted": redact_sensitive_text(text) != text,
        "occurred_at": message_time(getattr(obj, "timestamp", None)),
        "recorded_at": utc_now(),
    }
    if len(text.encode()) > 256 * 1024:
        raise CoreStateError("core_source_too_large")
    receipt = getattr(event, "_memory_capture_receipt", None)
    if receipt is not None:
        if (
            receipt.get("status") != "committed"
            or receipt.get("source_state") != "current"
        ):
            raise CoreStateError("core_source_unavailable")
        source["capture"] = {
            key: receipt[key] for key in ("operation", "source_version", "source_ref")
        }
    return source


def message_time(stamp):
    try:
        return datetime.fromtimestamp(float(stamp), UTC).isoformat() if stamp else ""
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def operation_for(source, label):
    return digest(
        [
            source.get(k, "")
            for k in ("origin", "platform", "user_id", "bot_id", "message_id")
        ]
        + [label.casefold()]
    )


def source_identity(source):
    # Re-observing one native message after restart does not create a new intent.
    return {
        key: source.get(key, "")
        for key in (
            "kind",
            "origin",
            "platform",
            "user_id",
            "bot_id",
            "message_id",
            "text",
            "occurred_at",
        )
    }
