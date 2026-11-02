"""Read the owner's retained chat timeline without creating a second archive."""

import asyncio
import html
import json
import re


async def read_shared_history(
    links, origin, store, query="", page=0, offset=0, budget=24000, revision=""
):
    """Read a bounded page of original messages across explicitly linked owners.

    Args:
        links: Verified private-session identities maintained by OwnerMemory.
        origin: Current native event origin, never a model-selected account.
        store: MemoryCompanion's existing timeline store.
        query: Optional space-separated literal keywords.
        page: Timeline page, with 200 source rows per linked channel.
        offset: Text-part cursor within the selected timeline page.
        budget: Maximum text characters returned in one call.
        revision: Native source revision returned with a continuation cursor.

    Returns:
        Attributed original user/assistant text and explicit continuation data.
    """
    if origin not in links.owners:
        return {"ok": False, "error": "unlinked_session", "items": []}
    if store is None:
        return {"ok": False, "error": "history_unavailable", "items": []}
    try:
        current_revision = await asyncio.wait_for(store.source_revision(), timeout=8)
    except Exception as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "items": [],
            "complete": False,
        }
    if revision and revision != current_revision:
        return {
            "ok": False,
            "error": "history_changed_restart_query",
            "items": [],
            "complete": False,
        }
    page, offset = max(0, int(page)), max(0, int(offset))
    budget = max(1600, min(32000, int(budget)))
    terms = [x.casefold() for x in query.split() if x.strip()][:8]
    records, errors, more_pages, scanned = await _collect_page(
        links, store, page, terms
    )
    records.sort(key=lambda x: (x["at"] or "", x["source_id"], x["part"]), reverse=True)
    selected, used, position = [], 0, offset
    for record in records[offset:]:
        if selected and used + len(record["text"]) > budget:
            break
        selected.append(record)
        used += len(record["text"])
        position += 1
    next_cursor = None
    if position < len(records):
        next_cursor = {"page": page, "offset": position, "revision": current_revision}
    elif more_pages:
        next_cursor = {"page": page + 1, "offset": 0, "revision": current_revision}
    try:
        if (
            await asyncio.wait_for(store.source_revision(), timeout=8)
            != current_revision
        ):
            return {
                "ok": False,
                "error": "history_changed_restart_query",
                "items": [],
                "complete": False,
            }
    except Exception as exc:
        errors.append({"error": type(exc).__name__})
    return {
        "ok": not errors,
        "items": list(reversed(selected)),
        "next_cursor": next_cursor,
        "scanned_source_rows": scanned,
        "complete": not errors and next_cursor is None and page == 0 and offset == 0,
        "errors": errors,
        "meaning": "Original chat evidence. Assistant suggestions are not completed actions; later corrections win.",
    }


def _original_parts(rows, peer, ctx, bot_ids, terms):
    """Filter exact-source provenance and preserve complete attributed message parts."""
    user_id = ctx["user_id"]
    records = []
    for row in rows:
        kind = row.get("event_type")
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (ValueError, TypeError):
                continue
        if (
            row.get("session_id") != peer
            or row.get("scope") != "private"
            or kind not in {"user_message", "bot_response"}
            or not isinstance(meta, dict)
            or meta.get("platform") not in (None, "", ctx.get("platform"))
            or meta.get("owner_bot_id") not in {None, "", *bot_ids}
            or meta.get("participant_user_id", user_id) not in ("", user_id)
            or (kind == "user_message" and row.get("subject_id") != user_id)
            or (kind == "bot_response" and row.get("object_id") != user_id)
        ):
            continue
        text = str(row.get("content") or "").strip()
        text = re.sub(r"<system_reminder>.*?</system_reminder>", "", text, flags=re.S)
        if '<message_batch source="continuous_message">' in text:
            parts = re.findall(r"<text>(.*?)</text>", text, flags=re.S)
            if parts:
                text = "\n".join(html.unescape(part) for part in parts)
        if not text or (terms and not any(term in text.casefold() for term in terms)):
            continue
        for start in range(0, len(text), 1600):
            records.append(
                {
                    "source_id": row["id"],
                    "channel": peer.split(":", 1)[0],
                    "at": row.get("occurred_at") or row.get("created_at"),
                    "role": "user" if kind == "user_message" else "assistant",
                    "text": text[start : start + 1600],
                    "part": start // 1600,
                    "parts": (len(text) + 1599) // 1600,
                }
            )
    return records


async def _collect_page(links, store, page, terms):
    """Read each verified native timeline, preserving partial failures and provenance."""
    records, errors = [], []
    more_pages, scanned = False, 0
    for peer in links.owners:
        user_id = peer.split(":", 2)[-1]
        try:
            variants = links.context_variants(peer)
        except ValueError:
            errors.append(
                {"channel": peer.split(":", 1)[0], "error": "identity_not_observed"}
            )
            continue
        ctx = variants[0]
        bot_ids = {x["bot_id"] for x in variants}
        try:
            rows = await asyncio.wait_for(
                store.recent_timeline(
                    limit=200,
                    scope="private",
                    session_id=peer,
                    entity_id=user_id,
                    offset=page * 200,
                ),
                timeout=8,
            )
        except Exception as exc:
            errors.append(
                {"channel": peer.split(":", 1)[0], "error": type(exc).__name__}
            )
            continue
        more_pages = more_pages or len(rows) == 200
        scanned += len(rows)
        records.extend(_original_parts(rows, peer, ctx, bot_ids, terms))
    return records, errors, more_pages, scanned
