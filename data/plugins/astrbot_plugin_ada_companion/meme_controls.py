"""Apply sticker feedback to shared preferences before any model or media hook."""

import re
import sqlite3

from astrbot.api import logger


def meme_feedback(text):
    """Recognize direct, short feedback; quoted examples are not commands."""
    if len(text) > 180 or re.search(r'[\n“”"「」]|他说|她说|比如|例如', text):
        return ""
    if re.search(r"(?:恢复|开启|继续)(?:自动)?(?:发)?表情包", text):
        return "resume"
    if re.search(r"(?:别|不要|不许|停止|不准).{0,18}(?:表情包|这张|那张|这个图)", text):
        return "block" if re.search(r"这(?:个|张)?|那张|刚才", text) else "pause"
    if re.search(r"(?:这条|这次|现在).{0,6}(?:只|仅)(?:回|用|发)文字|只回文字", text):
        return "text"
    return ""


class MemeControls:
    def __init__(self, owners, services):
        self.owners, self.services = owners, services

    async def receive(self, event):
        try:
            return await self._receive(event)
        except (sqlite3.Error, OSError, ValueError) as exc:
            event.set_extra("meme_manager_suppress", True)
            event.set_extra(
                "ada_meme_feedback",
                "Sticker preferences could not be read or saved. This reply is text only; do not claim a setting was changed.",
            )
            logger.warning(
                "Sticker preference storage unavailable: %s", type(exc).__name__
            )

    async def _receive(self, event):
        try:
            scope = self.owners.scope(event)
        except PermissionError:
            return
        event.set_extra("meme_manager_scope", scope)
        library = self.services.plugin("astrbot_plugin_meme_manager")
        usage = getattr(library, "meme_usage", None)
        action = meme_feedback(event.get_message_str().strip())
        if action and action != "resume":
            event.set_extra("meme_manager_suppress", True)
        if usage is None:
            if action:
                event.set_extra(
                    "ada_meme_feedback",
                    "Sticker settings are unavailable; do not claim they were saved.",
                )
            return
        if action == "block":
            changed = usage.block_latest(scope)
            note = (
                "The last dispatched sticker is now blocked across QQ and WeChat."
                if changed
                else "No recent sticker is recorded to identify; this reply will contain no sticker."
            )
        elif action in ("pause", "resume"):
            usage.set_enabled(scope, action == "resume")
            note = (
                "Stickers are now enabled across QQ and WeChat."
                if action == "resume"
                else "Stickers are now paused across QQ and WeChat until the owner says 恢复表情包."
            )
        else:
            note = "This reply is text only." if action == "text" else ""
        if not usage.status(scope)["enabled"]:
            event.set_extra("meme_manager_suppress", True)
        if note:
            event.set_extra("ada_meme_feedback", note)
