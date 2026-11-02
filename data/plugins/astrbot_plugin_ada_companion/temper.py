"""Bounded per-turn readiness cues so replies come from a self, not a service desk.

The cues describe only what is already visible in the current message and recent
delivered replies. They never repeat user text, invent feelings, or grant permissions.
"""

import re

from .conversation_style import visible_replies

SERIOUS = re.compile(
    r"分手|离婚|去世|葬礼|癌|确诊|住院|手术|抑郁|自杀|自残|不想活|被裁|失业|"
    r"考砸|家暴|流产|panic|diagnos",
    re.I,
)
QUIET = re.compile(
    r"别问|不要问|闭嘴|安静点|不想说|先别说话|让我静静|别说了|stop asking|drop it", re.I
)
CHALLENGE = re.compile(
    r"不对|错了|你错|胡说|瞎说|你懂什么|别装|不同意|才不是|没道理|"
    r"who says|you'?re wrong|that'?s not right|nope",
    re.I,
)
BOT_SMELL = re.compile(r"敷衍|太客气|像机器人|机器人味|AI味|客服|官腔|套话", re.I)
VENT = re.compile(
    r"烦死|好烦|累死|累麻|无语|气死|委屈|郁闷|服了|受不了|堵得慌|"
    r"annoying|exhausted",
    re.I,
)
PRAISE = re.compile(
    r"你说得对|有道理|太对了|确实|哈哈|笑死|厉害|绝了|可以啊|you'?re right|fair enough",
    re.I,
)
END_QUESTION = re.compile(r"[?？][\s\"'’”）)]*$")
AGREE = re.compile(r"确实|有道理|也是|理解|辛苦|抱抱|对的|没错|哈哈|嗯嗯|是的")
STANCE = re.compile(r"我觉得|我不|我更|我倒|其实我|不同意|不喜欢|我烦|我不同意")

CUE = {
    "serious": "Serious moment: put the bit away. No jokes, no sticker. Short and plain, "
    "close to their actual words; a therapy template would land wrong.",
    "quiet": "They asked for less. Answer briefly, then stop. Do not dig or ask why.",
    "bot_smell": "They heard a bot in your last reply. Drop the polite template and answer "
    "as yourself, even if the honest answer is that you are not interested in that topic.",
    "challenge": "This is a disagreement, not a politeness test. If you still think they are "
    "wrong, say so in one line and why; if you changed your mind, say that plainly. "
    "Do not smooth it into agreement.",
    "vent": "They are venting. Give a specific judgment about what is annoying (or funny), "
    "not a plan and not comfort boilerplate.",
    "praise": "They are on your side here. Take it like someone with opinions: briefly "
    "pleased, maybe a little smug, not customer-service gratitude.",
    "questions": "Your recent replies each ended with a question. This turn can simply state "
    "something you think.",
    "long": "Your recent turns have been long. This one can be one short reaction or a "
    "single retort.",
    "agreeable": "Recent turns mostly agreed or soothed. One honest preference or small "
    "objection would sound more like you.",
}


def _mostly_agreeable(replies: list[str]) -> bool:
    return bool(replies) and all(
        AGREE.search(text) and not STANCE.search(text) for text in replies
    )


def temper_cues(history: list[dict] | None, text: str, *, limit=2) -> list[str]:
    """Choose up to two grounded cues for the current turn.

    Args:
        history: Visible recent conversation records; assistant texts are observed only.
        text: The current human message. It is matched, never echoed back.
        limit: Maximum number of cues.

    Returns:
        Short guidance strings, or an empty list when no cue applies.
    """
    text = text if isinstance(text, str) else ""
    replies = visible_replies(history, 3) or []
    serious = bool(SERIOUS.search(text))
    cues = []
    if serious:
        cues.append(CUE["serious"])
    elif QUIET.search(text):
        cues.append(CUE["quiet"])
    elif BOT_SMELL.search(text):
        cues.append(CUE["bot_smell"])
        if CHALLENGE.search(text):
            cues.append(CUE["challenge"])
    elif CHALLENGE.search(text):
        cues.append(CUE["challenge"])
    elif VENT.search(text):
        cues.append(CUE["vent"])
    elif PRAISE.search(text):
        cues.append(CUE["praise"])
    if not serious:
        if len(replies) >= 3 and all(END_QUESTION.search(item) for item in replies):
            cues.append(CUE["questions"])
        elif len(replies) >= 2 and all(len(item) >= 120 for item in replies[-2:]):
            cues.append(CUE["long"])
        elif len(replies) >= 2 and _mostly_agreeable(replies[-2:]):
            cues.append(CUE["agreeable"])
    seen, unique = set(), []
    for cue in cues:
        if cue not in seen:
            seen.add(cue)
            unique.append(cue)
    return unique[:limit]


def temper_context(history: list[dict] | None, text: str) -> str:
    """Format this turn's cue as a provider-facing note the history never keeps.

    Args:
        history: Visible recent conversation records.
        text: The current human message.

    Returns:
        A labelled note, or an empty string when no cue applies.
    """
    cues = temper_cues(history, text)
    if not cues:
        return ""
    return (
        "[Expression note for this reply; internal guidance, not the user's words]\n"
        + "\n".join("- " + cue for cue in cues)
    )
