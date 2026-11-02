"""Shared expression policy and bounded rhythm cues from actual visible replies."""

import json
import re
from collections import Counter
from html import unescape

# Design references and adoption limits are recorded in maintenance/README.md.
# These are delivery instructions, not a replacement for the user's character card.
CHAT_STYLE = """
沿用小夏的人格和这段关系，用对方当前的语言自然接话。你的看法、好奇、
情绪和表达方式由眼前的对话决定，普通回应也可以很平淡。最近的回复只是
发生过的对话，不是本轮要模仿的模板。记忆里的旧话题随当下相关性出现。
"""


def _visible_text(turn) -> str:
    """Return one visible assistant text, excluding tool calls and reasoning."""
    content = turn.get("content", turn.get("text", ""))
    if isinstance(content, list):
        content = " ".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") in {"text", "output_text"}
            and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str):
        return ""
    content = content.strip()
    if turn.get("role") == "user":
        if content.startswith("[Image from tool '"):
            return ""
        if content.startswith("<message_batch"):
            batch = content.split("</message_batch>", 1)[0]
            content = (
                "\n".join(
                    unescape(text)
                    for text in re.findall(r"<text>(.*?)</text>", batch, re.S)
                )
                or "[图片或附件]"
            )
    return content[:2000]


def visible_replies(history: list[dict], limit=4) -> list[str]:
    """Collect recent assistant texts that the user actually saw.

    Args:
        history: Chronological, attributed visible conversation records.
        limit: Maximum number of trailing replies to return.

    Returns:
        Bounded visible reply texts. Tool calls and archived duplicates excluded.
    """
    replies = []
    seen = set()
    for turn in (history or [])[-24:]:
        if not isinstance(turn, dict) or turn.get("role") != "assistant":
            continue
        if turn.get("tool_calls"):
            continue
        # Archive chunks belong to one visible reply, not multiple repetitions.
        source = (turn.get("channel"), turn.get("source_id"))
        if turn.get("source_id"):
            if source in seen or turn.get("part", 0) != 0:
                continue
            seen.add(source)
        text = _visible_text(turn)
        if text:
            replies.append(text)
    return replies[-limit:] if limit else []


def rhythm_context(history: list[dict]) -> str:
    """Describe recent delivery habits without guessing emotions or retaining new state.

    Args:
        history: Chronological, attributed visible conversation records. Tool output
            and reasoning are excluded; linked channels use their shared timeline.

    Returns:
        A bounded, optional delivery cue. No facts, preferences or permissions are
        inferred from assistant text. The current request always controls the tone.
    """
    replies = visible_replies(history) or []
    if len(replies) < 2:
        return ""
    questions = sum(bool(re.search(r"[?？][\s\"'’”）)]*$", text)) for text in replies)
    openings = Counter(
        re.split(r"[，。！？,.!?\n]", text, maxsplit=1)[0].strip()[:12]
        for text in replies
    )
    repeated = [text for text, count in openings.items() if count >= 2 and text]
    cues = []
    if questions >= 2:
        cues.append(
            "Several recent replies ended in questions; a statement can leave room too."
        )
    if repeated:
        cues.append(
            "A repeated opening is becoming a tic; let this turn find its own wording."
        )
    if sum(len(text) >= 180 for text in replies) >= 2:
        cues.append(
            "Recent replies were elaborate; ordinary chat need not keep that length."
        )
    if not cues:
        return ""
    return (
        "\n[Recent delivery rhythm; observations, not personality or user facts]\n"
        + json.dumps(
            {"observed_replies": len(replies), "cues": cues}, ensure_ascii=False
        )
        + "\nApply only if it fits this turn. A necessary question or detailed answer is welcome.\n"
    )
