"""Apply owner commands to shared preferences and activity."""

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .contact_channels import CHANNEL_LABELS, contact_request


class ChatControls:
    def __init__(self, config, owners, policy, contacts=None):
        self.config = config
        self.owner_memory = owners
        self.policy = policy
        self.target = policy.target
        self.contacts = contacts

    async def receive(self, event: AstrMessageEvent):
        if not event.is_private_chat():
            return
        self.owner_memory.observe(event)
        origin = event.unified_msg_origin
        if origin in self.owner_memory.owners:
            try:
                self.policy.on_user(self.target, time.time(), event.get_message_str())
            except OSError as exc:
                # Stop scheduling while current activity cannot be persisted.
                self.policy.healthy = False
                logger.warning(
                    "Ada activity persistence failed: %s", type(exc).__name__
                )
        if origin not in self.config.get("owner_sessions", []):
            return
        text = event.get_message_str().strip().lower().rstrip("。.!！")
        request = contact_request(text)
        if request and self.contacts:
            await self.contacts.apply(
                event,
                *request,
                configured=bool(self.config.get("proactive_enabled", False)),
            )
            return
        channels = "和".join(
            CHANNEL_LABELS[name] for name in self.policy.data["channels"]
        )
        if text in (
            "/ada quiet",
            "/ada off",
            "停止主动消息",
            "别主动发消息了",
            "不要主动发消息",
            "暂停主动聊天",
            "暂停主动消息",
            "别主动找我了",
            "先别主动找我",
            "先别主动发消息",
            "pause check-ins",
            "stop checking in",
        ):
            self.policy.data["enabled"] = False
            if self.policy.healthy:
                self.policy.save()
            await event.send(
                event.plain_result(
                    "好，主动消息已暂停。你发消息我仍会回复；说“恢复主动聊天”就能开启。"
                )
            )
            event.stop_event()
        elif text in (
            "/ada resume",
            "/ada on",
            "恢复主动聊天",
            "开启主动聊天",
            "恢复主动消息",
            "继续主动聊天",
            "resume check-ins",
        ):
            if not self.policy.healthy or not self.config.get(
                "proactive_enabled", False
            ):
                await event.send(
                    event.plain_result(
                        "还没有恢复发送：主动聊天的总开关关闭了，或状态需要修复。普通聊天照常。"
                    )
                )
                event.stop_event()
                return
            self.policy.data["enabled"] = True
            self.policy.data["unanswered"] = 0
            self.policy.data["closing_until"] = 0
            self.policy.data["next_due"] = time.time() + 150 * 60
            self.policy.save()
            await event.send(
                event.plain_result(
                    f"主动聊天已恢复，联系渠道：{channels or '尚未选择'}。两边合计每天最多 4 次，23:00–09:00 不打扰。连续两次没回应会先停下来。"
                )
            )
            event.stop_event()
        elif text in ("/ada status", "主动聊天状态", "现在主动聊天开着吗"):
            data = self.policy.status(bool(self.config.get("proactive_enabled", False)))
            reason = {
                "ready": "等待下一轮判断",
                "quiet_hours": "免打扰时段",
                "cooldown": "等待下次合适时机",
                "recent_conversation": "刚聊过，先不打扰",
                "conversation_closed": "你说先忙或休息，晚些再聊",
                "awaiting_reply": "连续两次没回应，等你回复",
                "daily_limit": "今天已到上限",
                "paused": "你已暂停",
                "config_disabled": "总开关关闭",
                "state_unreadable": "状态待修复",
                "no_channels": "还没有开启联系渠道",
            }.get(data["gate"], "暂不发送")
            await event.send(
                event.plain_result(
                    f"主动聊天：{'开启' if data['enabled'] else '暂停'}；{reason}。今天合计 {data['today_count']}/4 次。渠道：{channels or '未选择'}，23:00–09:00 免打扰。"
                )
            )
            event.stop_event()
        elif text in ("/ada help", "ada帮助", "ada 帮助", "机器人怎么用"):
            await event.send(
                event.plain_result(
                    "平时直接聊就好。\n"
                    "记忆：说“记住我的这个偏好……”或“查看/修改我的核心偏好”。\n"
                    "聊天：可以告诉我“以后别老反问我”“这个梗以后就叫……”“这件事先别再提”。\n"
                    "表情：说“发张开心的表情包”，或者“这条只回文字”。\n"
                    "联网：说“查一下……”；新表情可以说“去网上找张……表情，收藏起来”。\n"
                    "网页：说“打开这个网站”“点这里”“一起玩这个小游戏”；说“停止浏览”可结束。\n"
                    "B站：说“刷一轮B站，找科普视频”“读一下这个BV视频的字幕”“B站阅读状态”；无字幕会说明，不会假装看过。自动发现可在伴聊插件配置中开启。\n"
                    "电脑：说“打开计算器”“看看 QQ 窗口”“查看我和你的QQ聊天”；说“停止操作”立即停止。\n"
                    "主动聊天：说“暂停主动消息”“恢复主动聊天”“主动聊天状态”。\n"
                    "联系渠道：说“QQ也找我”“以后只在QQ找我”或“别在QQ主动找我了”；微信也一样。\n"
                    "模型：说“当前模型”“切换模型 DeepSeek”或“切换模型 ChatGPT”，两边共用选择。\n"
                    "语音：说“用语音说……”，或“开启/关闭语音回复”。"
                    + (
                        ""
                        if self.config.get("voice_enabled", False)
                        else "当前语音服务还未开通。"
                    )
                )
            )
            event.stop_event()
        elif text in ("/voice on", "/voice off", "开启语音回复", "关闭语音回复"):
            enabled = text in ("/voice on", "开启语音回复")
            self.policy.data["voice_sessions"]["owner"] = enabled
            self.policy.save()
            if enabled and not self.config.get("voice_enabled", False):
                reply = "已记下语音回复偏好；语音服务还未开通，目前先回文字。"
            else:
                reply = (
                    "已开启语音回复，文字也保留。"
                    if enabled
                    else "已关闭自动语音；想听时说“用语音说”即可。"
                )
            await event.send(event.plain_result(reply))
            event.stop_event()
