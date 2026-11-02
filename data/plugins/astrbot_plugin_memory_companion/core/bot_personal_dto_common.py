from __future__ import annotations

import re

from .bot_personal_contract import (
    BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
)

"""Structured, privacy-limited DTOs for the Bot Personal archive boundary."""


BOT_PERSONAL_MAX_RECORD_VERSION = 1_000_000


CANONICAL_SCHEMA_VERSION = BOT_PERSONAL_CANONICAL_SCHEMA_VERSION


CANONICAL_EVIDENCE_LEVELS = frozenset({"L0", "L1", "L2", "L3", "L4", "L5"})


ARCHIVE_EVIDENCE_LEVELS = frozenset({"L0", "L1", "L2", "L3"})


EVIDENCE_KINDS = frozenset(
    {
        "none",
        "interaction",
        "self_state_commit",
        "tool_action",
        "external_record",
        "external_commitment",
    }
)


AUTHORITY_KINDS = frozenset(
    {
        "calendar",
        "timetable",
        "roster",
        "appointment",
        "user_confirmation",
        "routine",
        "persona",
        "state",
        "llm",
    }
)


COMMITMENT_LEVELS = frozenset({"confirmed", "routine", "tentative"})


EPISTEMIC_STATUSES = frozenset({"asserted", "inferred", "observed"})


CONTENT_GRANULARITIES = frozenset({"commitment", "intent", "candidate", "scene"})


MATERIALIZATION_STATES = frozenset(
    {"none", "candidate", "active", "rejected", "expired"}
)


FACT_ELIGIBILITIES = frozenset(
    {
        "none",
        "schedule_commitment",
        "current_internal",
        "current_observed",
        "history_observed",
    }
)


EXTERNAL_AUTHORITIES = frozenset(
    {"calendar", "timetable", "roster", "appointment", "user_confirmation"}
)


class BotPersonalValidationError(ValueError):
    def __init__(self, error_code: str, field: str = "", message: str = "") -> None:
        self.error_code = str(error_code or "invalid").strip()[:80]
        self.field = str(field or "").strip()[:160]
        self.message = str(message or self.error_code).strip()[:300]
        super().__init__(self.message)


_FORBIDDEN_KEY_PARTS = (
    "prompt",
    "conversation",
    "transcript",
    "message_chain",
    "message_history",
    "raw_message",
    "messages",
    "raw_prompt",
    "chat_history",
    "cookie",
    "token",
    "secret",
    "credential",
    "password",
    "passwd",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
    "private_key",
    "base64",
    "binary",
    "media_bytes",
    "image_bytes",
    "audio_bytes",
    "video_bytes",
    "media_binary",
    "media_data",
    "media_content",
    "media_blob",
    "file_path",
    "filepath",
    "absolute_path",
    "local_path",
    "raw_payload",
)


_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)^bearer\s+\S+"),
    re.compile(
        r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)base64,"),
    re.compile(r"(?i)^data:[a-z0-9.+-]+/[a-z0-9.+-]+[;,]"),
    re.compile(r"(?i)^-----begin "),
    re.compile(
        r"(?i)(?:^|[\s=:;,])(?:[A-Za-z]:[\\/]|\\\\|/home/|/root/|/tmp/|/var/|/volume\d+/)"
    ),
)


_FORBIDDEN_TOP_LEVEL_KEYS = frozenset(
    {
        "conversation",
        "transcript",
        "message_chain",
        "raw_prompt",
        "raw_payload",
        "payload_raw",
    }
)
