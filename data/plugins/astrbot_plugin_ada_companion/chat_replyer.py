"""Compose speech, continuity and reaction intent in the native model turn."""

import json
import re
import time

from astrbot.api import logger
from astrbot.core.agent.message import TextPart

from .appraisals import APPRAISAL_UPDATES_SCHEMA, EventUnderstanding

REPLY_STYLE = """
[本轮消息格式]
需要工具时照常调用。最终输出 JSON（工具调用不受此格式约束）：
{"reply":"实际要发的话"}。
reply 保留实际工具结果及选中的表情标记，可以包含代码或长回答。
有近期未完事项需要延续时才加 topic_updates，最多两项：
{"action":"save|resolve|cancel","kind":"topic|goal","item_id":"已有id或空","title":"主题",
"content":"事情及进展，相对日期按当前时间记清","evidence_quote":"本轮对方原话"}。
新事 save，更新沿用 id，明确办完则 resolve，取消则 cancel；普通感想不用记一项。
只有明确要持续推进的目标才用 goal；目标不因沉默到期。关闭只记录对方报告，不证明工具已经执行。
确认已保存前优先调用 ada_threads 并检查 ok/current；不要把生成计划说成已执行。
对方描述会影响后续互动的具体处境、边界、关切或原因时，用 appraisal_updates 建立事件理解，最多两项。
首次出现也要记录，不把这类信息只留在本轮口头回应中；新信息改变已有理解时修订原事件：
{"item_id":"已有事件id或空","title":"事件短名","understanding":"现在的理解及适用情境",
"response_implication":"这会怎样影响相关的接话选择","uncertainty":"仍不确定的部分或空",
"status":"active|resolved","evidence_quote":"本轮对方原话的连续引用"}。
这是小夏的可修订解释，不是给对方贴标签。同一歌曲、人物或话题不等于同一事件：先核对时间、行为和原话所指。
原话没有把当前反馈关联到旧事件时，保留旧解释，新事件 id 留空；明确解释同一件事时沿用快照中的 id，
保留原事件，不再另记一次；一条来信最多建立一个新事件。只根据本轮原话修订，不据沉默、旧回忆或自己说的话学习。
修订时保留尚未被推翻的具体适用情境和边界，不因新一轮换了说法而丢掉原有限制。
普通问候、即时知识问答不用记；不知道就保留不确定。解释不会自动执行行动，也不证明长期偏好或外部结果。
反馈按本轮原话指向的事情和适用情境理解：时机反馈改互动时机，声音反馈改表达，任务结果改相应方法。
不要把“谢谢”、普通附和或一次不想聊，当作对全部记忆的事实确认或永久喜恶；先区别对方在评价什么。
有具体对象且影响后续时才写 appraisal_updates；事实内容确实改正时使用原事实纠正入口，不能用情绪分数代替。
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
        self.appraisals = EventUnderstanding(dialogue)
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
                        "properties": {
                            "reply": {"type": "string"},
                            "appraisal_updates": APPRAISAL_UPDATES_SCHEMA,
                        },
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
            receipts = await self.dialogue.apply_updates(
                event, decision.get("topic_updates", [])
            )
            if any(not r.get("ok") or not r.get("current") for r in receipts):
                text += "\n这次后续事项没有保存成功，之后还需要重新确认。"
                response.completion_text = text
                event.set_extra("ada_spoken_reply", text)
            appraisal_receipts = await self.appraisals.apply_updates(
                event, decision.get("appraisal_updates", [])
            )
            appraisal_failed = any(
                not r.get("ok") or not r.get("current") for r in appraisal_receipts
            )
            self.last = {
                "ok": True,
                "mode": "native_single_pass",
                "provider": event.get_extra("selected_provider"),
                "seconds": round(
                    time.monotonic() - event.get_extra("ada_reply_started"), 2
                ),
                "extra_model_calls": 0,
                "appraisal_save_failed": appraisal_failed,
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
