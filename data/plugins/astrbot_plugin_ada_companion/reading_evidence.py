"""Immutable source identity and exact excerpts handed to a reading consumer."""

import hashlib
import json
import time

MAX_SOURCE_CHARS = 200_000
MAX_EXCERPT_CHARS = 24_000
SOURCE_KINDS = {"web_dom", "rss_entry", "bilibili_subtitles", "captured_excerpt"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_record(data):
    text = data.get("_source_text", data["text"])
    if not isinstance(text, str) or len(text) > MAX_SOURCE_CHARS:
        raise ValueError("Reading source exceeds its capture limit.")
    kind = data.get("source_kind", "captured_excerpt")
    if kind not in SOURCE_KINDS:
        raise ValueError("Unknown reading source kind.")
    total = data.get("source_total_chars", len(text))
    if type(total) is not int or total < len(text):
        raise ValueError("Invalid reading source extent.")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    truncated = bool(data.get("source_truncated", False))
    identity = [
        data["url"],
        data.get("source_url", data["url"]),
        kind,
        sha,
        total,
        truncated,
    ]
    return {
        "id": hashlib.sha256(encoded(identity).encode()).hexdigest(),
        "url": data["url"],
        "source_url": identity[1],
        "title": data["title"],
        "kind": kind,
        "sha256": sha,
        "captured_at": time.time(),
        "captured_chars": len(text),
        "source_total_chars": total,
        "upstream_truncated": truncated,
        "text": text,
    }


def excerpt(record, start, limit):
    if type(start) is not int or start < 0 or start > record["captured_chars"]:
        raise ValueError("Reading offset is outside the captured source.")
    if type(limit) is not int or not 1 <= limit <= MAX_EXCERPT_CHARS:
        raise ValueError("Reading limit must be 1–24000 characters.")
    end = min(record["captured_chars"], start + limit)
    return record["text"][start:end], start, end


def provenance(record, start, end):
    capture_incomplete = (
        record["captured_chars"] < record["source_total_chars"]
        or record["upstream_truncated"]
    )
    return {
        "source_id": record["id"],
        "source_sha256": record["sha256"],
        "source_kind": record["kind"],
        "source_url": record["source_url"],
        "material_url": record["url"],
        "captured_at": record["captured_at"],
        "provided_range": {"start": start, "end": end, "unit": "unicode_codepoints"},
        "captured_chars": record["captured_chars"],
        "source_total_chars": record["source_total_chars"],
        "capture_incomplete": capture_incomplete,
        "current_model_context": "unknown",
        "range_evidence": "prepared_for_consumer",
        "next_offset": end if end < record["captured_chars"] else None,
        "meaning": (
            "This exact text range was prepared at the reading consumer boundary; "
            "delivery to the current model is not confirmed. It does not "
            "prove the whole article/video was read, understood, heard or watched. "
            "RSS entries and subtitles retain their own source scope."
        ),
    }


def note_provenance(page, quote):
    reading = dict(page.get("reading") or {})
    if not reading:
        return {"source_kind": "legacy_unknown", "coverage": "unknown"}
    if quote:
        offset = page["text"].find(quote)
        if offset < 0:
            raise ValueError("Quote is outside the provided reading excerpt.")
        start = reading["provided_range"]["start"] + offset
        reading["quote_range"] = {"start": start, "end": start + len(quote)}
    return reading
