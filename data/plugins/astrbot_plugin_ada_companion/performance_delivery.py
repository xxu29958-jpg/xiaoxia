"""Resolve the original authorized chat and retain only the receipt actually returned."""

import asyncio
import hashlib

from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Plain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .performance_files import verify
from .performance_store import PerformanceError


class PerformanceDelivery:
    def __init__(self, context, owners):
        self.context, self.owners = context, owners

    def destination(self, request):
        destination = request["destination"]
        origin, platform = destination["origin"], destination["platform"]
        if origin in self.owners.owners:
            expected = self.owners.shared_scope
        elif platform == "webchat":
            expected = hashlib.sha256(("admin:" + origin).encode()).hexdigest()[:24]
        else:
            raise PerformanceError("performance_authorization_revoked")
        if request["owner"] != expected:
            raise PerformanceError("performance_authorization_revoked")
        parts = origin.split(":", 2)
        if len(parts) != 3 or parts[1] != "FriendMessage":
            raise PerformanceError("performance_destination_invalid")
        adapter = self.context.get_platform_inst(parts[0])
        if adapter is None or adapter.meta().name != platform:
            raise PerformanceError("performance_channel_unavailable")
        status = getattr(adapter, "status", None)
        if status is not None and status.value != "running":
            raise PerformanceError("performance_channel_unavailable")
        return adapter, parts[2]

    async def prepare(self, request, artifact):
        adapter, user_id = self.destination(request)
        if artifact["format"] == "text":
            component = Plain(artifact["text"])
        else:
            file = await asyncio.to_thread(verify, artifact)
            component = File(name=f"小夏 - {request['title'][:64]}.mp3", file=str(file))
        if request["destination"]["platform"] == "aiocqhttp":
            if not user_id.isdigit():
                raise PerformanceError("performance_destination_invalid")
            segment = await AiocqhttpMessageEvent._from_segment_to_dict(component)

            async def send_qq():
                self.destination(request)
                result = await adapter.bot.send_private_msg(
                    user_id=int(user_id), message=[segment]
                )
                message_id = (
                    result.get("message_id") if isinstance(result, dict) else None
                )
                if (
                    not isinstance(message_id, bool)
                    and str(message_id).lstrip("-").isdigit()
                ):
                    return {
                        "kind": "server_accepted",
                        "protocol": "onebot-v11",
                        "message_id": str(message_id),
                        "played": False,
                    }
                return {"kind": "unknown", "reason": "missing_native_receipt"}

            return send_qq
        else:

            async def send_other():
                self.destination(request)
                matched = await self.context.send_message(
                    request["destination"]["origin"], MessageChain([component])
                )
                return {
                    "kind": "adapter_returned" if matched else "unknown",
                    "protocol": request["destination"]["platform"],
                    "reason": "native_receipt_not_exposed",
                    "played": False,
                }

            return send_other
