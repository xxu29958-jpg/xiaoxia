"""Fit short original Mandarin lyrics to a gentle, explicitly scored melody."""

import json
import re
from pathlib import Path

from pypinyin import Style, lazy_pinyin, pinyin

from services.runtime import path as runtime_path

MELODY = (
    (62, 64, 66, 69, 67, 64, 62, 62),
    (64, 66, 69, 67, 66, 64, 62, 64),
    (62, 64, 66, 64, 62, 61, 62, 64),
    (62, 66, 64, 62, 61, 64, 62, 62),
)
RHYTHM = {
    1: (1.5,),
    2: (0.75, 1.5),
    3: (0.75, 0.75, 1.5),
    4: (0.75, 1.0, 0.5, 1.25),
    5: (1.0, 0.5, 0.5, 0.5, 1.5),
    6: (0.5, 0.5, 1.0, 0.5, 0.5, 1.5),
    7: (0.75, 0.5, 0.5, 0.75, 0.5, 0.5, 1.5),
    8: (0.5, 0.5, 0.5, 0.5, 0.75, 0.5, 0.75, 1.5),
}


def write_score(text: str, folder: Path) -> Path:
    """Write aligned syllables, notes and durations for a short Mandarin song.

    Args:
        text: Original Mandarin lyrics, with punctuation between phrases.
        folder: Output directory for score and readable note metadata.

    Returns:
        Path to the SoulX score metadata.

    Raises:
        ValueError: Lyrics are empty, too long or contain unsupported syllables.
    """
    phrases = []
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        # Keep all lyric characters; divide long phrases into singable lines.
        chunks = max(1, (len(phrase) + 11) // 12)
        for index in range(chunks):
            left, right = (
                round(index * len(phrase) / chunks),
                round((index + 1) * len(phrase) / chunks),
            )
            phrases.append(phrase[left:right])
    if not phrases or len(phrases) > 8:
        raise ValueError("Provide one to eight short Mandarin lyric lines")
    phones = set(
        json.loads(
            (
                runtime_path("soul_repo") / "soulxsinger/utils/phoneme/phone_set.json"
            ).read_text(encoding="utf-8")
        )
    )
    events = [
        {"text": "<SP>", "phone": "<SP>", "pitch": 0, "frames": 12, "note_type": 1}
    ]
    beat_frames = 60 / 86 * 50
    beat = 0.0
    for phrase_index, phrase in enumerate(phrases):
        melody = (
            MELODY[-1]
            if phrase_index == len(phrases) - 1 and phrase_index
            else MELODY[phrase_index % len(MELODY)]
        )
        pronunciation = lazy_pinyin(
            phrase, style=Style.TONE3, neutral_tone_with_five=True, tone_sandhi=True
        )
        rhythm = RHYTHM.get(len(phrase)) or tuple(
            [(0.5, 0.5, 0.75, 0.5)[i % 4] for i in range(len(phrase) - 1)] + [1.5]
        )
        for index, (character, syllable) in enumerate(zip(phrase, pronunciation)):
            phone = "zh_" + syllable.replace("v", "u:")
            if phone not in phones:
                # The singer omits some spoken tone variants (er5, bu2).
                # Retain the syllable; the score supplies its sung pitch.
                variants = pinyin(character, style=Style.TONE3, heteronym=True)[0]
                variants += [syllable[:-1] + str(tone) for tone in range(1, 5)]
                for variant in variants:
                    candidate = "zh_" + variant.replace("v", "u:")
                    if variant[:-1] == syllable[:-1] and candidate in phones:
                        phone = candidate
                        break
            if phone not in phones:
                raise ValueError(
                    f"Unsupported singing syllable: {character} ({syllable})"
                )
            duration = rhythm[index]
            frames = round((beat + duration) * beat_frames) - round(beat * beat_frames)
            beat += duration
            pitch = melody[round(index * (len(melody) - 1) / max(1, len(phrase) - 1))]
            # Carry the final vowel across a descending pair on an open phrase.
            if index == len(phrase) - 1 and len(phrase) >= 5 and phrase_index % 2 == 0:
                first_frames = round(frames * 0.6)
                events.append(
                    {
                        "text": character,
                        "phone": phone,
                        "pitch": pitch + 2,
                        "frames": first_frames,
                        "note_type": 2,
                    }
                )
                events.append(
                    {
                        "text": character,
                        "phone": phone,
                        "pitch": pitch,
                        "frames": frames - first_frames,
                        "note_type": 3,
                    }
                )
            else:
                events.append(
                    {
                        "text": character,
                        "phone": phone,
                        "pitch": pitch,
                        "frames": frames,
                        "note_type": 2,
                    }
                )
        events.append(
            {"text": "<SP>", "phone": "<SP>", "pitch": 0, "frames": 17, "note_type": 1}
        )
    events[-1]["frames"] = 28
    total_frames = sum(event["frames"] for event in events)
    durations = [event["frames"] / 50 for event in events]
    if int(sum(durations) * 50) < total_frames:
        durations[-1] += 1e-7
    metadata = [
        {
            "index": "xiaxia_original",
            "language": "Mandarin",
            "time": [0, total_frames * 20],
            "duration": " ".join(f"{duration:.8f}" for duration in durations),
            "text": " ".join(event["text"] for event in events),
            "phoneme": " ".join(event["phone"] for event in events),
            "note_pitch": " ".join(str(event["pitch"]) for event in events),
            "note_type": " ".join(str(event["note_type"]) for event in events),
        }
    ]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "score.json"
    path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "notes.json").write_text(
        json.dumps(
            {"bpm": 86, "lyrics": phrases, "events": events},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
