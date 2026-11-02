from __future__ import annotations

import importlib
from typing import Any

from .models import (
    clean_text,
)


class HistoricalChatImporterIdentity:
    """Identity; shared runtime resources belong to the facade."""

    def _private_api(self) -> Any | None:
        for module_name in (
            "data.plugins.astrbot_plugin_private_companion.main",
            "astrbot_plugin_private_companion.main",
        ):
            try:
                module = importlib.import_module(module_name)
                getter = getattr(module, "get_private_companion_api", None)
                api = getter() if callable(getter) else None
                if api is not None:
                    return api
            except Exception:
                continue
        return None

    def _identity_context(self, speakers: list[str]) -> dict[str, Any]:
        api = self._private_api()
        resolver = (
            getattr(api, "resolve_historical_chat_identities", None)
            if api is not None
            else None
        )
        if not callable(resolver):
            return {"available": False, "matches": {}, "bot": {}, "target_users": []}
        try:
            result = resolver(speakers)
            return (
                result
                if isinstance(result, dict)
                else {"available": False, "matches": {}}
            )
        except Exception as exc:
            return {"available": False, "matches": {}, "error": clean_text(exc, 160)}

    def _suggest_speaker_roles(
        self,
        messages: list[dict[str, Any]],
        identity: dict[str, Any],
    ) -> list[dict[str, Any]]:
        speakers = list(
            dict.fromkeys(clean_text(item.get("speaker"), 80) for item in messages)
        )
        counts = {
            speaker: sum(1 for item in messages if item.get("speaker") == speaker)
            for speaker in speakers
        }
        matches = (
            identity.get("matches") if isinstance(identity.get("matches"), dict) else {}
        )
        bot = identity.get("bot") if isinstance(identity.get("bot"), dict) else {}
        bot_aliases = [
            clean_text(item, 40)
            for item in (bot.get("aliases") or [])
            if clean_text(item, 40)
        ]
        bot_scores = dict.fromkeys(speakers, 0)
        reasons: dict[str, list[str]] = {speaker: [] for speaker in speakers}
        if len(speakers) == 2 and bot_aliases:
            for speaker in speakers:
                mentions = sum(
                    1
                    for item in messages
                    if item.get("speaker") == speaker
                    and any(
                        alias in str(item.get("content") or "") for alias in bot_aliases
                    )
                )
                if mentions:
                    partner = speakers[1] if speaker == speakers[0] else speakers[0]
                    bot_scores[partner] += min(6, mentions)
                    reasons[partner].append(f"另一位说话人 {mentions} 次使用 Bot 称呼")
        for speaker in speakers:
            candidates = (
                matches.get(speaker) if isinstance(matches.get(speaker), list) else []
            )
            if candidates:
                reasons[speaker].append(
                    "关系网名称或别名唯一命中"
                    if len(candidates) == 1
                    else "关系网存在多个候选"
                )
        if len(speakers) == 2 and not any(bot_scores.values()):
            ranked = sorted(
                speakers, key=lambda item: counts.get(item, 0), reverse=True
            )
            if counts.get(ranked[0], 0) >= counts.get(ranked[1], 0) * 1.5:
                bot_scores[ranked[0]] = 1
                reasons[ranked[0]].append("连续多段回复特征仅作为低置信提示")

        strongest_bot = (
            max(speakers, key=lambda item: bot_scores.get(item, 0)) if speakers else ""
        )
        suggestions: list[dict[str, Any]] = []
        for speaker in speakers:
            candidates = (
                matches.get(speaker) if isinstance(matches.get(speaker), list) else []
            )
            if speaker == strongest_bot and bot_scores.get(speaker, 0) > 0:
                role = "bot"
                confidence = "high" if bot_scores[speaker] >= 2 else "low"
            elif (
                len(speakers) == 2
                and strongest_bot
                and bot_scores.get(strongest_bot, 0) > 0
            ):
                role = "user"
                confidence = "medium" if len(candidates) != 1 else "high"
            elif len(candidates) == 1:
                role = "user"
                confidence = "high"
            else:
                role = "unknown"
                confidence = "low"
            suggestions.append(
                {
                    "speaker": speaker,
                    "message_count": counts.get(speaker, 0),
                    "suggested_role": role,
                    "confidence": confidence,
                    "reasons": reasons[speaker],
                    "relationship_candidates": candidates[:8],
                }
            )
        return suggestions
