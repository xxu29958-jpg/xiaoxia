"""Authenticated operational inspection and non-delivering continuity probes."""

import hashlib
import json
import time
from datetime import datetime
from types import SimpleNamespace

from astrbot.core.astr_main_agent import _ensure_persona_and_skills
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.provider.entities import ProviderRequest

from .policy import LOCAL_TZ
from .shared_history import read_shared_history


class AdminApi:
    def __init__(self, plugin):
        self.plugin = plugin

    async def status(self):
        now = time.time()
        working = await self.plugin.dialogue.snapshot(self.plugin.target)
        enabled = bool(
            self.plugin.config.get("proactive_enabled", False)
            and self.plugin.policy.healthy
            and self.plugin.policy.data["enabled"]
            and self.plugin.policy.data["channels"]
        )
        gate = self.plugin.policy.gate(now)
        if self.plugin.policy.healthy and not self.plugin.config.get(
            "proactive_enabled", False
        ):
            gate = "config_disabled"
        return {
            "status": "ok",
            "data": {
                "enabled": enabled,
                "state_healthy": self.plugin.policy.healthy,
                "gate": gate,
                "today_count": self.plugin.policy.daily_count(now),
                "unanswered": self.plugin.policy.data["unanswered"],
                "next_due": datetime.fromtimestamp(
                    self.plugin.policy.data["next_due"], LOCAL_TZ
                ).isoformat(),
                "quiet_hours": "23:00-09:00 Asia/Shanghai",
                "max_per_day": 4,
                "channel": ", ".join(self.plugin.policy.data["channels"]),
                "channels": list(self.plugin.policy.data["channels"]),
                "channel_revision": self.plugin.policy.data["channel_revision"],
                "memory_connected": self.plugin.services.memory() is not None,
                "linked_memory": self.plugin.owner_memory.last,
                "dialogue_healthy": working["ok"],
                "open_threads": working.get("total"),
                "working_state_error": working.get("error"),
                "voice_enabled": bool(self.plugin.config.get("voice_enabled", False)),
                "cloud_voice": self.plugin.cloud_voice.status(),
                "local_voice": self.plugin.local_voice.status(),
                "last_check": self.plugin.initiative.last_check,
                "last_voice": self.plugin.media.last_voice,
                "chat_replyer": {
                    "enabled": bool(
                        self.plugin.config.get("chat_replyer_enabled", False)
                    ),
                    **self.plugin.replyer.last,
                },
                "online_memes": self.plugin.online_memes.status(),
                "public_browser": self.plugin.public_browser.status(),
                "web_search_connected": self.plugin.services.plugin(
                    "astrbot_plugin_anysearch"
                )
                is not None,
                "deliveries": [
                    {
                        k: x.get(k)
                        for k in (
                            "id",
                            "at",
                            "text",
                            "status",
                            "memory_id",
                            "history_saved",
                            "target",
                        )
                    }
                    for x in self.plugin.policy.data["deliveries"][-5:]
                ],
            },
        }

    async def preview_proactive(self):
        return {
            "status": "ok",
            "data": await self.plugin.initiative.proactive(preview=True),
        }

    async def preview_linked_memory(self):
        results = {}
        bridge = self.plugin.services.memory()
        for origin in self.plugin.owner_memory.owners:
            results[origin.split(":", 1)[0]] = await self.plugin.owner_memory.compose(
                origin,
                "最近聊过的事情、称呼、偏好和约定",
                self.plugin.services.memory(),
                self.plugin.context.conversation_manager,
                include_current=True,
            )
            results[origin.split(":", 1)[0]]["archive"] = await read_shared_history(
                self.plugin.owner_memory,
                origin,
                getattr(getattr(bridge, "_plugin", None), "store", None),
            )
        return {"status": "ok", "data": results}

    async def _continuity_reply(self, origin, question, persona, history):
        """Exercise a fresh request context and real shared-memory tool per turn.

        Args:
            origin: One configured private owner channel.
            question: Current turn text, used for retrieval and prompt preparation.
            persona: The resolved native persona.
            history: Prior conversation messages; never saved by this probe.

        Returns:
            The actual provider reply, without delivery or persistence.
        """
        extras = {"ada_topics_reviewed": True}
        event = SimpleNamespace(
            unified_msg_origin=origin,
            is_private_chat=lambda: True,
            get_message_str=lambda: question,
            get_platform_name=lambda: self.plugin.owner_memory.contexts[origin][
                "platform"
            ],
            set_extra=lambda key, value: extras.update({key: value}),
            get_extra=lambda key, default=None: extras.get(key, default),
            get_sender_id=lambda: origin.split(":", 2)[-1],
        )
        req = SimpleNamespace(system_prompt=persona.system_prompt)
        await self.plugin.prompt.prepare(event, req)
        tool_result = await self.plugin.memory.recall_other_channel(event, question)
        # Rebuild the context for every question, including follow-ups. Reusing
        # the first question's prompt would not exercise a real second turn.
        history = [
            *history,
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_shared_memory_probe",
                        "type": "function",
                        "function": {
                            "name": "ada_recall_other_channel",
                            "arguments": json.dumps({"query": question}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_shared_memory_probe",
                "content": tool_result,
            },
        ]
        from importlib import import_module

        service = self.plugin.services.memory()._plugin
        cleanup = import_module(
            type(service).__module__.rsplit(".", 1)[0] + ".astrbot_compat"
        )
        cleanup_req = SimpleNamespace(
            contexts=history, prompt=question, extra_user_content_parts=[]
        )
        cleanup.remove_temp_text(
            cleanup_req, "<MemoryCompanion-Context>", "</MemoryCompanion-Context>"
        )
        cleanup.sanitize_request_history(cleanup_req)
        response = await self.plugin.context.llm_generate(
            chat_provider_id=self.plugin.config.get("provider_id", "chatgpt-companion"),
            prompt=question,
            system_prompt=req.system_prompt,
            contexts=cleanup_req.contexts,
        )
        return response.completion_text

    async def verify_continuity(self):
        results = []
        persona = await self.plugin.context.persona_manager.get_persona("Ada")
        cm = self.plugin.context.conversation_manager
        for origin in self.plugin.owner_memory.owners:
            if origin.split(":", 1)[0] not in {
                "qq-personal-companion",
                "wechat-companion",
            }:
                continue
            cid = await cm.get_curr_conversation_id(origin)
            conversation = await cm.get_conversation(origin, cid) if cid else None
            history = json.loads(conversation.history or "[]") if conversation else []
            question = "我们说好一起玩哪个游戏，怎么一起玩？还有你现在叫什么？"
            first = await self._continuity_reply(origin, question, persona, history)
            followup = await self._continuity_reply(
                origin,
                "那我想让你在哪个平台主动找我？",
                persona,
                [
                    *history,
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": first},
                ],
            )
            results.append(
                {
                    "channel": origin.split(":", 1)[0],
                    "reply": first,
                    "followup": followup,
                }
            )
        return {
            "status": "ok",
            "data": {"sent": False, "saved": False, "results": results},
        }

    async def shared_state(self):
        """Inspect real persona/tool resolution without generating or delivering text."""
        results = []
        context = self.plugin.context
        cm = context.conversation_manager
        for origin in self.plugin.owner_memory.owners:
            if origin.split(":", 1)[0] not in {
                "wechat-companion",
                "qq-personal-companion",
            }:
                continue
            identity = self.plugin.owner_memory.contexts[origin]
            event = inspection_event(origin, identity)
            cid = await cm.get_curr_conversation_id(origin)
            conversation = await cm.get_conversation(origin, cid)
            req = ProviderRequest(conversation=conversation, system_prompt="")
            cfg = context.get_config(origin)
            (
                persona_id,
                _,
                _,
                _,
            ) = await context.persona_manager.resolve_selected_persona(
                umo=origin,
                conversation_persona_id=conversation.persona_id,
                platform_name=identity["platform"],
                provider_settings=cfg["provider_settings"],
            )
            await _ensure_persona_and_skills(
                req, cfg["provider_settings"], context, event
            )
            await self.plugin.tool_policy.game_tool_scope(event, req)
            core = await self.plugin.memory.core.execute(
                origin, self.plugin.services.memory()._plugin, self.plugin.target
            )
            core_json = json.dumps(core, ensure_ascii=False, sort_keys=True)
            results.append(
                {
                    "channel": origin.split(":", 1)[0],
                    "persona": persona_id,
                    "tools": sorted(req.func_tool.names()) if req.func_tool else [],
                    "tool_scope": self.plugin.owner_memory.scope(event),
                    "core_ok": core["ok"],
                    "core_blocks": len(core.get("blocks", [])),
                    "core_digest": hashlib.sha256(core_json.encode()).hexdigest(),
                    "core_errors": core.get("errors", []),
                    "shared_activity": self.plugin.policy.data["last_user"],
                }
            )
        return {"status": "ok", "data": results}


def inspection_event(origin, identity):
    """A non-delivering native event for the authenticated tool/persona probe."""
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = identity["bot_id"]
    message.sender = MessageMember(identity["user_id"], "Owner")
    message.message_str = "继续聊我们刚才的事"
    message.message = [Plain(message.message_str)]
    message.message_id = "management-read-only-probe"
    return AstrMessageEvent(
        message.message_str,
        message,
        PlatformMetadata(identity["platform"], "inspection", origin.split(":", 1)[0]),
        origin.split(":", 2)[-1],
    )
