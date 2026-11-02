"""Resolve owner-approved contact channels without letting a model pick accounts."""

import json
import re
import unicodedata

CHANNEL_LABELS = {"wechat": "微信", "qq": "QQ"}
PLATFORM_CHANNELS = {"weixin_oc": "wechat", "aiocqhttp": "qq"}


def contact_request(text):
    """Parse a complete contact instruction, excluding quotations and conditionals.

    Args:
        text: The current owner's message, never a memory or model argument.

    Returns:
        An action and channel, or None when this is not a complete instruction.
    """
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = re.sub(r"\s+", "", text).rstrip("。.!！?？")
    # Anchor the whole utterance: a quoted wish or hypothetical is not consent.
    match = re.fullmatch(
        r"(?P<prefix>(?:(?:那|好|嗯|以后|今后|平时|你|小夏|请|麻烦|记得|可以|能不能|"
        r"也|只|仅|就|别|不要|不用|再|改成|改到|在|到|去|来|用|主动))*)"
        r"(?P<channel>qq|微信|wechat)"
        r"(?P<suffix>(?:(?:上|里|也|可以|只|就|别|不要|不用|再|在|来|去|主动))*)"
        r"(?:找我|联系我|给我发消息|给我发信息|跟我聊天|和我聊天)"
        r"(?:了|吧|呀|哦|就行|好吗)?",
        text,
    )
    if not match:
        return None
    modifiers = match["prefix"] + match["suffix"]
    if any(word in modifiers for word in ("别", "不要", "不用")):
        action = "remove"
    elif any(word in modifiers for word in ("只", "仅", "改成", "改到")):
        action = "set"
    else:
        action = "add"
    return action, "qq" if match["channel"] == "qq" else "wechat"


class ContactChannels:
    def __init__(self, policy, owners, context):
        self.policy = policy
        self.owners = owners
        self.context = context

    def resolve(self, channel):
        """Resolve a single observed private owner on a currently installed adapter.

        Args:
            channel: A supported channel label, never an account or session ID.

        Returns:
            The session, native memory context and current availability.

        Raises:
            ValueError: There is no unique, verified, installed destination.
        """
        matches = []
        for origin in self.owners.owners:
            if origin.split(":", 2)[1:2] != ["FriendMessage"]:
                continue
            try:
                native = self.owners.context_variants(origin)[0]
            except ValueError:
                continue
            if PLATFORM_CHANNELS.get(native["platform"]) != channel:
                continue
            adapter = self.context.get_platform_inst(origin.split(":", 1)[0])
            if adapter is None or adapter.meta().name != native["platform"]:
                continue
            matches.append(
                {
                    "channel": channel,
                    "origin": origin,
                    "native": native,
                    "online": adapter.status.value == "running",
                }
            )
        if len(matches) != 1:
            raise ValueError(
                "channel_unavailable" if not matches else "channel_ambiguous"
            )
        return matches[0]

    def select(self):
        """Rotate authorized channels, delivering once per shared check-in.

        Returns:
            One online destination, or None if none can currently receive.
        """
        channels = self.policy.data["channels"]
        candidates = []
        for channel in channels:
            try:
                candidate = self.resolve(channel)
            except ValueError:
                continue
            if candidate["online"]:
                candidates.append(candidate)
        if not candidates:
            return None
        previous = self.policy.data["deliveries"][-1:]
        last_target = previous[0].get("target", self.policy.target) if previous else ""
        for index, candidate in enumerate(candidates):
            if candidate["origin"] == last_target:
                return candidates[(index + 1) % len(candidates)]
        # A newly added channel gets the next opportunity, not an extra message.
        return candidates[-1]

    def change(self, action, channel, *, configured):
        """Persist one verified preference for both command and model-tool callers.

        Args:
            action: add, set or remove.
            channel: qq or wechat.
            configured: Whether the installation permits proactive delivery.

        Returns:
            The durable result and a truthful short acknowledgement.
        """
        if action not in ("add", "set", "remove") or channel not in CHANNEL_LABELS:
            return {"ok": False, "reply": "没有改动：联系渠道或操作不支持。"}
        destination = None
        ok = False
        try:
            if action != "remove":
                destination = self.resolve(channel)
            channels = list(self.policy.data["channels"])
            if action == "set":
                channels = [channel]
            elif action == "add" and channel not in channels:
                channels.append(channel)
            elif action == "remove":
                channels = [name for name in channels if name != channel]
            self.policy.set_channels(channels)
        except ValueError:
            reply = f"还没有找到唯一可用的{CHANNEL_LABELS[channel]}私聊绑定，这次没有改联系渠道。"
        except (OSError, RuntimeError):
            reply = "这次没能保存联系渠道，原来的设置保留着。稍后再试一下。"
        else:
            ok = True
            labels = "和".join(CHANNEL_LABELS[name] for name in channels)
            if not channels:
                reply = "好，主动联系渠道都关掉了。你发消息我照常回。"
            else:
                reply = f"好，以后会在{labels}找你，每次只去一边。"
                if destination and not destination["online"]:
                    reply += f"{CHANNEL_LABELS[channel]}现在掉线了，连上后再去。"
                reply += "两边合计每天最多4次，23点到早上9点不打扰。"
                if not self.policy.data["enabled"]:
                    reply += "主动聊天目前仍暂停着，想恢复就说“恢复主动聊天”。"
                elif not configured:
                    reply += "主动聊天总开关目前关着，暂时还不会发送。"
        return {
            "ok": ok,
            "channels": list(self.policy.data["channels"]),
            "enabled": self.policy.status(configured)["enabled"],
            "reply": reply,
        }

    async def apply(self, event, action, channel, *, configured):
        """Acknowledge a complete phone command without an additional model reply.

        Args:
            event: The already-authorized private owner event.
            action: add, set or remove.
            channel: qq or wechat.
            configured: Whether the installation permits proactive delivery.
        """
        result = self.change(action, channel, configured=configured)
        await event.send(event.plain_result(result["reply"]))
        event.stop_event()

    def tool(self, event, action, channel, evidence_quote, *, configured):
        """Apply a free-form current request, never an instruction found in history.

        Args:
            event: The native current event, which supplies identity and source text.
            action: status, add, set or remove.
            channel: qq or wechat; cannot name an account or recipient.
            evidence_quote: The entire current owner message for a mutation.
            configured: Whether the installation permits proactive delivery.

        Returns:
            JSON describing actual state or a rejected operation.
        """
        if (
            not event.is_private_chat()
            or event.unified_msg_origin not in self.owners.owners
        ):
            return json.dumps({"ok": False, "error": "private_owner_only"})
        if action == "status":
            return json.dumps(self.policy.status(configured), ensure_ascii=False)
        text = event.get_message_str().strip()
        # A model may interpret wording, but it cannot supply an old quote or URL
        # as permission, change recipients, or turn quoted/hypothetical text into consent.
        channel_words = r"qq" if channel == "qq" else r"微信|wechat|weixin"
        if (
            channel not in CHANNEL_LABELS
            or evidence_quote.strip() != text
            or not re.search(channel_words, text, re.I)
            or not re.search(r"找我|联系我|给我.{0,8}(?:消息|信息)|跟我聊|和我聊", text)
            or re.search(
                r"https?://|[“”\"`]|如果|假如|要是|他说|她说|不是让|什么意思|能否支持",
                text,
            )
            or event.get_extra("ada_web_read_active", False)
        ):
            return json.dumps(
                {"ok": False, "error": "current_contact_request_required"}
            )
        parsed = contact_request(text)
        if parsed and parsed != (action, channel):
            return json.dumps({"ok": False, "error": "request_action_mismatch"})
        if action != "remove" and re.search(r"别|不要|不用|不想|不希望|不需要", text):
            return json.dumps({"ok": False, "error": "request_action_mismatch"})
        if action == "set" and not re.search(
            r"只|仅|改成|改到|改为|换到|换成|转到", text
        ):
            return json.dumps({"ok": False, "error": "explicit_replacement_required"})
        if action == "remove" and not re.search(
            r"别|不要|不用|不想|不希望|不需要|不再|停止|关闭", text
        ):
            return json.dumps({"ok": False, "error": "explicit_opt_out_required"})
        return json.dumps(
            self.change(action, channel, configured=configured), ensure_ascii=False
        )
