"""Compose speech, continuity and reaction intent in the native model turn."""

import json
import re
import time

from astrbot.api import logger
from astrbot.core.agent.message import TextPart

REPLY_STYLE = """
[本轮消息格式]
需要工具时照常调用。最终输出 JSON（工具调用不受此格式约束）：
{"reply":"实际要发的话"}。
reply 保留实际工具结果及选中的表情标记，可以包含代码或长回答。
有近期未完事项需要延续时才加 topic_updates，最多两项：
{"action":"save|resolve","item_id":"已有id或空","title":"主题",
"content":"事情及进展，相对日期按当前时间记清","evidence_quote":"本轮对方原话"}。
新事 save，更新沿用 id，办完、取消或不想再提则 resolve；普通感想不用记一项。
当下真想配一张表情时才加 reaction：
{"action":"react|requested","shift":true,"evidence":"当前原话或回复的短引用","query":"表情想表达什么"}。
react 表示本轮新出现的鲜明反应，requested 表示对方正在要图。
没有话题更新或新表情反应时，省略对应字段。
"""


class ChatReplyer:
    def __init__(self, context, config, owners, models, dialogue):
        self.context, self.config = context, config
        self.owners, self.models = owners, models
        self.dialogue = dialogue
        self.last = {}

    async def prepare(self, event, req):
        """Use one native generation without replacing tools or memory context.

        Args:
            event: The current private event, including its actual human wording.
            req: The native request after scoped memory and companion preparation.
        """
        if not self.config.get("chat_replyer_enabled", False):
            return
        if event.get_extra("ada_meme_only"):
            return
        try:
            self.owners.scope(event)
        except PermissionError:
            return
        (
            persona_id,
            persona,
            _,
            _,
        ) = await self.context.persona_manager.resolve_selected_persona(
            umo=event.unified_msg_origin,
            conversation_persona_id=getattr(req.conversation, "persona_id", None),
            platform_name=event.get_platform_name(),
        )
        if persona_id != "Ada" or not persona:
            return
        if not event.get_extra("ada_native_reply"):
            req.response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "ada_reply",
                    "strict": False,
                    "schema": {
                        "type": "object",
                        "properties": {"reply": {"type": "string"}},
                        "required": ["reply"],
                        "additionalProperties": True,
                    },
                },
            }
            req.system_prompt = (req.system_prompt or "") + REPLY_STYLE
            if event.get_extra("ada_voice_requested"):
                req.system_prompt += (
                    '\n本轮语音另加 "voice_emotion"：neutral、happy、angry、sad、afraid、'
                    "disgusted、melancholic、surprised 或 calm。根据前后文选你此刻说这句话的语气，"
                    "不是照抄对方的心情；普通接话用 neutral。语气字段不写进 reply。\n"
                )
            event.set_extra("ada_native_reply", True)
            event.set_extra("ada_reply_started", time.monotonic())

    async def compose(self, event, response):
        """Unpack the native final response before media and memory consumers.

        Args:
            event: An event prepared for native structured companion output.
            response: The final native model response, modified in place.
        """
        if not event.get_extra("ada_native_reply") or response.role != "assistant":
            return
        original = response.completion_text or ""
        event.set_extra("ada_reply_reaction", {"action": "skip"})
        event.set_extra("ada_voice_emotion", None)
        try:
            decision = json.loads(
                re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", original.strip())
            )
            text = decision["reply"].strip()
            if not text:
                raise ValueError("Native reply contained no spoken text")
            response.completion_text = text
            event.set_extra("ada_spoken_reply", text)
            emotion = decision.get("voice_emotion")
            if isinstance(emotion, str) and emotion in {
                "happy",
                "angry",
                "sad",
                "afraid",
                "disgusted",
                "melancholic",
                "surprised",
                "calm",
            }:
                event.set_extra("ada_voice_emotion", emotion)
            event.set_extra(
                "ada_reply_reaction", decision.get("reaction") or {"action": "skip"}
            )
            updates = decision.get("topic_updates", [])
            current = event.get_extra("turnflow_user_text") or event.get_message_str()
            if isinstance(updates, list) and not event.is_stopped():
                for update in updates[:2]:
                    if not isinstance(update, dict):
                        continue
                    if update.get("action") not in {"save", "resolve"}:
                        continue
                    fields = ("item_id", "title", "content", "evidence_quote")
                    if any(not isinstance(update.get(key, ""), str) for key in fields):
                        continue
                    try:
                        self.dialogue.update(
                            event.unified_msg_origin,
                            current,
                            action=update["action"],
                            **{key: update.get(key, "") for key in fields},
                        )
                    except OSError as exc:
                        logger.warning(
                            "Ada topic update unavailable: %s", type(exc).__name__
                        )
            self.last = {
                "ok": True,
                "mode": "native_single_pass",
                "provider": event.get_extra("selected_provider"),
                "seconds": round(
                    time.monotonic() - event.get_extra("ada_reply_started"), 2
                ),
                "extra_model_calls": 0,
                "voice_emotion": event.get_extra("ada_voice_emotion") or "neutral",
            }
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            self.last = {
                "ok": False,
                "mode": "native_single_pass",
                "error": type(exc).__name__,
                "extra_model_calls": 0,
            }
            logger.warning("Ada kept native wording: %s", type(exc).__name__)

    def sync_history(self, event, run_context):
        """Persist delivered wording instead of the internal output envelope.

        Args:
            event: The event holding final wording before media parsing.
            run_context: Native agent context before conversation persistence.
        """
        spoken = event.get_extra("ada_spoken_reply")
        if not spoken or not run_context.messages:
            return
        last = run_context.messages[-1]
        if last.role == "assistant" and isinstance(last.content, list):
            last.content = [
                part for part in last.content if not isinstance(part, TextPart)
            ] + [TextPart(text=spoken)]
