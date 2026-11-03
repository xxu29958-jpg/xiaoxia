"""Construct one companion context from shared evidence and current platform capabilities."""

import json
import re
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.message import TextPart

from .conversation_style import CHAT_STYLE, rhythm_context
from .meme_controls import meme_feedback
from .persona_rules import PLATFORM_RULES
from .policy import LOCAL_TZ, wants_voice
from .shared_history import read_shared_history
from .temper import temper_context
from .thread_review import ThreadReview


class ContextBuilder:
    def __init__(self, config, owners, dialogue, policy, context, services):
        self.config = config
        self.owner_memory = owners
        self.dialogue = dialogue
        self.policy = policy
        self.context = context
        self.services = services
        self.target = policy.target
        self.review = ThreadReview(dialogue, owners, policy)
        self.materials = None

    async def prepare(self, event: AstrMessageEvent, req):
        if not event.is_private_chat():
            return
        event.set_extra("ada_context_prepared", True)
        voice = self._response_intent(event)
        runtime = self._runtime_facts(voice, bool(event.get_extra("voice_input_files")))
        if event.get_extra("voice_input", False):
            runtime += "\n[这轮来信]\n他刚发来的是语音，当前消息就是这段语音的内容。\n"
        event.set_extra("ada_runtime_context", runtime)
        instructions = PLATFORM_RULES + runtime
        if event.get_extra("ada_meme_feedback"):
            instructions += "\n[Applied sticker preference] " + event.get_extra(
                "ada_meme_feedback"
            )
        history = getattr(req, "contexts", [])
        instructions += await self._conversation_evidence(event, history)
        instructions += CHAT_STYLE
        self._apply_temper(event, req, history)
        # Replace only the exact block this request owns; preserve the card and peers.
        previous = getattr(req, "_ada_companion_context", "")
        current = req.system_prompt or ""
        req.system_prompt = (
            current.replace(previous, instructions, 1)
            if previous and previous in current
            else current + instructions
        )
        req._ada_companion_context = instructions

    def _apply_temper(self, event, req, history):
        """Keep one provider-facing expression note, never persisted to history.

        Args:
            event: The current private conversation turn.
            req: The native request being assembled.
            history: Visible recent conversation records used only for observation.
        """
        previous = getattr(req, "_ada_temper_part", None)
        parts = getattr(req, "extra_user_content_parts", None)
        if isinstance(parts, list) and previous is not None:
            parts[:] = [part for part in parts if part is not previous]
        req._ada_temper_part = None
        if not self.config.get("temper_enabled", True):
            return
        text = event.get_extra("turnflow_user_text")
        if text is None:
            text = event.get_message_str()
        cue = temper_context(history, text or "")
        if not cue or not isinstance(parts, list):
            return
        part = TextPart(text=cue).mark_as_temp()
        parts.append(part)
        req._ada_temper_part = part

    def _response_intent(self, event):
        text = event.get_extra("turnflow_user_text")
        if text is None:
            text = event.get_message_str()
        event.set_extra(
            "ada_meme_only",
            len(text) <= 160
            and not meme_feedback(text)
            and not event.get_extra("meme_manager_suppress")
            and bool(
                re.search(
                    r"(?:来|发|给|整|丢|要|想看|换).{0,16}(?:表情|反应图|梗图)|斗图|只发图",
                    text,
                )
            )
            and not bool(re.search(r"解释|介绍|分析|为什么|怎么|同时|顺便|然后", text)),
        )
        origin = event.unified_msg_origin
        voice = wants_voice(
            text,
            self.policy.data["voice_sessions"].get(
                "owner", self.policy.data["voice_sessions"].get(origin, False)
            )
            or event.get_extra("voice_input", False),
        )
        voice = voice and (
            origin in self.config.get("owner_sessions", [])
            or (
                event.get_platform_name() == "webchat"
                and str(event.get_sender_id()) == "astrbot"
            )
        )
        event.set_extra("ada_voice_requested", voice)
        if voice:
            logger.info("Ada voice request detected on %s", event.get_platform_name())
        return voice

    def _runtime_facts(self, voice, has_audio=False):
        instructions = ""
        instructions += (
            "[当前能力状态，相关时再提]\n"
            + json.dumps(
                {
                    "proactive_enabled": bool(
                        self.config.get("proactive_enabled", False)
                        and self.policy.healthy
                        and self.policy.data["enabled"]
                        and self.policy.data["channels"]
                    ),
                    "proactive_wait_reason": self.policy.gate(time.time()),
                    "unanswered_checkins": self.policy.data["unanswered"],
                    "voice_synthesis_enabled": bool(
                        self.config.get("voice_enabled", False)
                    ),
                    "singing_enabled": bool(self.config.get("voice_enabled", False)),
                    "singing_source_recording_available": has_audio,
                    "web_search_available": self.services.plugin(
                        "astrbot_plugin_anysearch"
                    )
                    is not None,
                    "online_meme_collection_enabled": bool(
                        self.config.get("online_meme_enabled", True)
                    ),
                    "public_browser_config_enabled": bool(
                        self.config.get("browser_enabled", True)
                    ),
                    "proactive_channels": self.policy.data["channels"],
                    "proactive_delivery_scope": "configured owner's enabled private channels; one destination per check-in, shared four/day cap",
                }
            )
            + "\n这是当前设置；他的最新愿望看聊天记录。修改设置要实际调用工具。Ada帮助可查看控制方式。\n"
        )
        if voice:
            instructions += (
                "[Voice requested]\n这一轮用语音回他，沿用平常的聊天口吻。"
                "日常接话一句就好；解释时先说清重点，中文90字或英文55词以内。"
                "只写要说的话，音频随后生成。\n"
            )
            if not self.config.get("voice_enabled", False):
                instructions += (
                    "Voice synthesis is currently disabled while its API connection is being configured. "
                    "Give the short intended reply in text; the platform appends a brief availability note.\n"
                )
        return instructions

    async def _conversation_evidence(self, event, history):
        origin = event.unified_msg_origin
        text = event.get_message_str()
        instructions = ""
        social = {}
        event.set_extra("ada_social_context", social)
        if origin in self.owner_memory.owners:
            actual = [
                x for x in self.policy.data["deliveries"][-3:] if x["status"] == "sent"
            ]
            if actual:
                social["主动消息"] = [item["text"] for item in actual]
                instructions += "[你最近实际发出的主动消息]\n" + json.dumps(
                    [
                        {
                            "at": datetime.fromtimestamp(x["at"], LOCAL_TZ).isoformat(),
                            "text": x["text"],
                            "channel": x.get("target", self.target).split(":", 1)[0],
                        }
                        for x in actual
                    ],
                    ensure_ascii=False,
                )
        if origin in self.owner_memory.owners:
            if self.materials:
                discoveries = self.materials.candidates(include_shared=True)[:2]
                if discoveries:
                    social["实际阅读"] = discoveries
                    instructions += (
                        "\n[你实际读过的内容和随手记]\n"
                        + json.dumps(discoveries, ensure_ascii=False)
                        + "\n聊到相关处可以接着说，挑一个你感兴趣的细节。\n"
                    )
            bridge = self.services.memory()
            linked = await self.owner_memory.compose(
                origin,
                text,
                bridge,
                self.context.conversation_manager,
                include_current=True,
            )
            linked["archive"] = await read_shared_history(
                self.owner_memory,
                origin,
                getattr(getattr(bridge, "_plugin", None), "store", None),
                budget=6000,
            )
            if linked["archive"].get("ok") and linked["archive"].get("items"):
                history = linked["archive"]["items"]
            social["跨端记忆"] = linked
            instructions += (
                "\n[你们的跨端往事]\n"
                "下面 QQ 和微信记录来自同一个人。按说话者和时间理解，当前更正优先；"
                "自然用得上的记忆再提。需要更早原话可用 ada_read_shared_history 翻页。"
                "没查到表示暂缺资料。\n" + json.dumps(linked, ensure_ascii=False)
            )
        if not self.config.get("chat_replyer_enabled", False):
            await self.review.process(event, history)
        if closed := event.get_extra("ada_topics_resolved"):
            instructions += (
                "\n[这轮已经聊完的话题]\n"
                + json.dumps(closed, ensure_ascii=False)
                + "\n已经处理好了，接着聊当前的话。\n"
            )
        active_threads = self.dialogue.visible(origin)
        if active_threads:
            social["未完话题"] = active_threads
            instructions += (
                "\n[还没聊完的事]\n"
                + json.dumps(active_threads, ensure_ascii=False)
                + "\n有新进展再续；已经解决或不想再聊的事可按他的原话收尾。\n"
            )
        if self.config.get("temper_enabled", True):
            instructions += rhythm_context(history)
        return instructions
