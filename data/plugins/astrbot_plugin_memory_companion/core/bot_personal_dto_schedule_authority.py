from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .bot_personal_contract import (
    BOT_PERSONAL_SUBJECT,
)
from .bot_personal_dto_common import EXTERNAL_AUTHORITIES
from .bot_personal_dto_validation import _string_refs, _text


def _parse_optional_moment(value: Any, timezone_name: str = "") -> datetime | None:
    """Parse a trusted-reference timestamp without accepting naive clocks."""

    if isinstance(value, datetime):
        parsed = value
    else:
        text = _text(value, 96)
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not timezone_name:
            return None
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except Exception:
            return None
    elif timezone_name:
        try:
            parsed = parsed.astimezone(ZoneInfo(timezone_name))
        except Exception:
            return None
    return parsed


def _iso_moment(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value is not None else ""


def _expected_schedule_ref_id(
    namespace: str,
    event_id: str,
    revision: str,
    provider: str,
    subject_actor_id: str,
    *,
    updated_at: str = "",
    timezone: str = "",
    effective_from: str = "",
    effective_to: str = "",
    expires_at: str = "",
    authority_kind: str = "",
    confirmation_event_id: str = "",
    confirmation_actor_id: str = "",
    proposition: str = "",
    confirmed_at: str = "",
    target_user_id: str = "",
) -> str:
    # Keep this verifier-side canonicalization byte-for-byte compatible with
    # schedule_authority._ref_id in the companion plugin.
    raw = json.dumps(
        {
            "namespace": namespace,
            "event_id": event_id,
            "revision": revision,
            "provider": provider,
            "subject_actor_id": subject_actor_id,
            "updated_at": updated_at,
            "timezone": timezone,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "expires_at": expires_at,
            "authority_kind": authority_kind,
            "confirmation_event_id": confirmation_event_id,
            "confirmation_actor_id": confirmation_actor_id,
            "proposition": proposition,
            "confirmed_at": confirmed_at,
            "target_user_id": target_user_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "trusted_schedule:" + hashlib.sha256(raw).hexdigest()[:24]


def _has_trusted_schedule_ref(
    payload: Mapping[str, Any], authority: str, now: datetime
) -> bool:
    """Validate an adapter-shaped schedule reference, never a trust boolean.

    The memory plugin intentionally does not own the authority adapter.  It
    can still reject forged references by checking the adapter's stable id,
    actor binding, absolute interval, state, and expiry before accepting a
    ``confirmed`` commitment.
    """

    schedule_ref = payload.get("schedule_ref")
    if not isinstance(schedule_ref, Mapping):
        return False
    ref_id = _text(schedule_ref.get("ref_id"), 240)
    source_refs = set(_string_refs(payload.get("source_refs")))
    if not ref_id or ref_id not in source_refs:
        return False
    if any(
        schedule_ref.get(key) is False
        or _text(schedule_ref.get(key), 16).lower()
        in {"false", "no", "denied", "invalid", "revoked"}
        for key in ("authorized", "permission_valid", "subject_authorized")
        if key in schedule_ref
    ):
        return False
    declared_authority = _text(schedule_ref.get("authority_kind"), 48).lower()
    if (
        declared_authority != _text(authority, 48).lower()
        or declared_authority not in EXTERNAL_AUTHORITIES
    ):
        return False
    subject = _text(schedule_ref.get("subject_actor_id"), 120)
    if subject != BOT_PERSONAL_SUBJECT:
        return False
    namespace = _text(schedule_ref.get("namespace"), 120)
    event_id = _text(schedule_ref.get("event_id"), 160)
    provider = _text(schedule_ref.get("provider"), 120)
    revision = _text(schedule_ref.get("revision"), 80)
    timezone_name = _text(schedule_ref.get("timezone"), 80)
    if (
        not namespace
        or not event_id
        or not provider
        or not revision
        or not timezone_name
    ):
        return False
    try:
        ZoneInfo(timezone_name)
    except Exception:
        return False
    updated = _parse_optional_moment(schedule_ref.get("updated_at"), timezone_name)
    start = _parse_optional_moment(schedule_ref.get("effective_from"), timezone_name)
    end = _parse_optional_moment(schedule_ref.get("effective_to"), timezone_name)
    if updated is None or start is None or end is None or end <= start:
        return False
    expires = _parse_optional_moment(schedule_ref.get("expires_at"), timezone_name)
    if schedule_ref.get("expires_at") not in (None, "") and expires is None:
        return False
    confirmation_event_id = _text(schedule_ref.get("confirmation_event_id"), 160)
    confirmation_actor_id = _text(
        schedule_ref.get("confirmation_actor_id")
        or schedule_ref.get("confirmed_by")
        or schedule_ref.get("source_actor_id"),
        120,
    )
    proposition = _text(
        schedule_ref.get("proposition")
        or schedule_ref.get("title")
        or schedule_ref.get("activity")
        or schedule_ref.get("summary"),
        240,
    )
    confirmed_at = _parse_optional_moment(
        schedule_ref.get("confirmed_at") or schedule_ref.get("confirmation_time"),
        timezone_name,
    )
    expected_ref_id = _expected_schedule_ref_id(
        namespace,
        event_id,
        revision,
        provider,
        subject,
        updated_at=_iso_moment(updated),
        timezone=timezone_name,
        effective_from=_iso_moment(start),
        effective_to=_iso_moment(end),
        expires_at=_iso_moment(expires),
        authority_kind=declared_authority,
        confirmation_event_id=confirmation_event_id,
        confirmation_actor_id=confirmation_actor_id,
        proposition=proposition,
        confirmed_at=_iso_moment(confirmed_at),
        target_user_id=_text(schedule_ref.get("target_user_id"), 120),
    )
    if ref_id != expected_ref_id:
        return False
    if declared_authority == "user_confirmation":
        if (
            not confirmation_event_id
            or not confirmation_actor_id
            or confirmation_actor_id == subject
            or not proposition
            or confirmed_at is None
        ):
            return False
    state = _text(schedule_ref.get("state"), 24).lower() or "active"
    if state != "active":
        return False
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    try:
        current = current.astimezone(ZoneInfo(timezone_name))
    except Exception:
        return False
    return expires is None or current < expires
