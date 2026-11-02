"""Attach verified memes and optional speech to an existing reply."""

import asyncio
import re
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain

from .audio_files import AudioFiles
from .cloud_voice import VoiceError
from .policy import speech_text


class ResponseMedia:
    def __init__(self, config, folder, voice):
        self.config = config
        self.folder = folder
        self.voice = voice
        self.audio_files = AudioFiles(folder, config)
        self.voice_lock = asyncio.Lock()
        self.last_voice = {}

    async def image_only_meme_reply(self, event: AstrMessageEvent, response):
        """Remove selection chatter only when a current valid meme was requested.

        Args:
            event: The current private conversation turn.
            response: The final model response before the native meme parser.
        """
        if not event.get_extra("ada_meme_only", False):
            return
        candidates = event.get_extra("meme_manager_semantic_candidates", {}) or {}
        selected = [
            value
            for value in re.findall(
                r"&&(meme:[a-f0-9]{12,64})&&", response.completion_text or ""
            )
            if value in candidates
        ]
        if selected:
            response.completion_text = f"&&{selected[0]}&&"

    async def decorate(self, event: AstrMessageEvent):
        if not event.get_extra("ada_voice_requested", False) or event.get_extra(
            "ada_voice_done", False
        ):
            return
        # The final reply composer sets this even when it keeps plain model wording.
        if (
            event.get_extra("ada_native_reply")
            and event.get_extra("ada_reply_reaction") is None
        ):
            return
        result = event.get_result()
        if not result or not result.chain:
            return
        event.set_extra("ada_voice_done", True)
        if not self.config.get("voice_enabled", False):
            self.last_voice = {
                "state": "disabled",
                "at": time.time(),
                "platform": event.get_platform_name(),
            }
            result.chain.append(Plain("\n（语音暂时停用了，这条先用文字回你。）"))
            return
        text = speech_text(
            " ".join(x.text for x in result.chain if isinstance(x, Plain))
        )
        if not text or len(text) > 650:
            result.chain.append(
                Plain("\n（这条内容较长，先保留文字；可以让我把其中一小段用语音说。）")
            )
            return
        if self.voice_lock.locked():
            result.chain.append(Plain("\n（语音正在处理另一条消息，这条先发文字。）"))
            return
        try:
            async with self.voice_lock:
                emotion = event.get_extra("ada_voice_emotion")
                audio = await self.voice.synthesize(
                    text, **({"emotion": emotion} if emotion else {})
                )
                result.chain.append(
                    await self.audio_files.create(
                        audio, text, event.get_platform_name()
                    )
                )
                self.last_voice = {
                    "state": "attached_to_reply",
                    "at": time.time(),
                    "platform": event.get_platform_name(),
                    "bytes": len(audio),
                    "mode": "speech",
                }
                logger.info(
                    "Ada voice attached to reply on %s: %s bytes",
                    event.get_platform_name(),
                    len(audio),
                )
        except Exception as exc:
            code = str(exc) if isinstance(exc, VoiceError) else type(exc).__name__
            self.last_voice = {
                "state": "failed",
                "error": code,
                "at": time.time(),
            }
            logger.warning("Ada voice generation failed: %s", code)
            note = {
                "monthly_limit": "这个月的语音额度用完了，先用文字陪你聊。",
                "not_configured": "语音还在接入，先用文字回你。",
            }.get(code, "这次语音没生成成功，文字保留了。")
            result.chain.append(Plain("\n（" + note + "）"))
