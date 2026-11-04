"""Read-only context accessibility; scores are not belief, utility or evidence."""

import math
import re
from collections import Counter

from .appraisal_rules import public_record
from .core_state_versions import CoreStateError, digest
from .working_state_rules import valid_clock

POLICY = "event-context-v1"


def terms(text):
    """A lexical baseline: Latin words and Chinese bi/trigrams, no model calls."""
    result = set()
    for block in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.casefold()):
        if not re.fullmatch(r"[\u4e00-\u9fff]+", block) or len(block) == 1:
            result.add(block)
        else:
            for size in (2, 3):
                result.update(block[i : i + size] for i in range(len(block) - size + 1))
    return result


def _evidence_terms(record):
    return terms(
        " ".join(
            record.get(key, "")
            for key in (
                "title",
                "understanding",
                "response_implication",
                "original_quote",
                "evidence_quote",
            )
        )
    )


def _cue(cues, available, weights):
    matches = cues & available
    mass = sum(weights.get(t, 0.0) for t in sorted(cues))
    return (
        sum(weights.get(t, 0.0) for t in sorted(matches)) / mass if mass else 0.0,
        sorted(matches, key=lambda t: (-weights[t], t))[:8],
    )


def rank(records, query, goals, now):
    """Pure elapsed-time calculation: repeated reads do not practice a memory."""
    corpus = {r["id"]: _evidence_terms(r) for r in records}
    fans = Counter(t for values in corpus.values() for t in values)
    cues, goal_cues = terms(query), terms(goals)
    # Fan weighting makes ubiquitous cues weaker than specific shared cues.
    # Only cues with a retained target can distribute activation. Unknown words
    # must not drown a specific shared cue in a long current request or goal.
    weights = {
        t: math.log1p(len(records) / fans[t]) for t in cues | goal_cues if fans[t]
    }
    result = []
    for record in records:
        current, matches = _cue(cues, corpus[record["id"]], weights)
        goal, goal_matches = _cue(goal_cues, corpus[record["id"]], weights)
        elapsed = max(0.0, now - record["updated_at"])
        # Engineering defaults, not empirically fitted human-memory parameters.
        scale_days = 7.0 if record["status"] == "active" else 2.0
        freshness = (1.0 + elapsed / (86400 * scale_days)) ** -0.5
        score = 0.4 * freshness + 0.9 * current + 0.3 * goal
        result.append(
            (
                record,
                {
                    "score": score,
                    "elapsed_seconds": elapsed,
                    "freshness": freshness,
                    "current_cue": current,
                    "goal_cue": goal,
                    "matched_terms": matches,
                    "goal_terms": goal_matches,
                },
            )
        )
    return result


def select(records, *, owner, revision, mode, query, goals, now, limit, offset, cursor):
    if mode not in {"recent", "attention", "search"}:
        raise CoreStateError("invalid_appraisal_selection")
    for name, value, maximum in (("query", query, 2000), ("goals", goals, 4000)):
        if not isinstance(value, str) or len(value) > maximum:
            raise CoreStateError("invalid_appraisal_" + name)
    if type(offset) is not int or offset < 0 or not isinstance(cursor, str):
        raise CoreStateError("invalid_appraisal_cursor")
    if mode != "search" and (offset or cursor):
        raise CoreStateError("appraisal_search_cursor_required")
    valid_clock(now)
    if mode == "recent":
        ordered = sorted(records, key=lambda r: (-r["updated_at"], r["id"]))
        return {
            "items": [public_record(r) for r in ordered[:limit]],
            "revision": revision,
            "truncated": len(ordered) > limit,
        }
    ranked = rank(records, query, goals, now)
    if mode == "attention":
        ranked = [r for r in ranked if r[1]["score"] >= 0.12]
        ranked.sort(key=lambda r: (-r[1]["score"], -r[0]["updated_at"], r[0]["id"]))
    elif mode == "search":
        revision = digest([owner, revision, query, POLICY])
        if (offset and not cursor) or (cursor and cursor != revision):
            raise CoreStateError("appraisal_search_changed")
        # Explicit lookup has no freshness cutoff. A stable cursor is not tied
        # to the wall clock; the entire source version and query are bound.
        ranked = [r for r in ranked if not query.strip() or r[1]["current_cue"] > 0]
        ranked.sort(
            key=lambda r: (-r[1]["current_cue"], -r[0]["updated_at"], r[0]["id"])
        )
    page = ranked[offset : offset + limit]
    return {
        "items": [{**public_record(r), "accessibility": trace} for r, trace in page],
        "revision": revision,
        "matched": len(ranked),
        "offset": offset,
        "next_offset": offset + limit
        if mode == "search" and len(ranked) > offset + limit
        else None,
        "truncated": offset > 0 or len(ranked) > offset + limit,
        "selection": {
            "policy": POLICY,
            "mode": mode,
            "at": now,
            "basis": "lexical_context_and_elapsed_time_not_truth_or_utility",
        },
    }
