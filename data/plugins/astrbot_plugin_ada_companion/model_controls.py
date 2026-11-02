"""One durable model preference for the same owner on every private channel."""

import asyncio
import json
import re
from pathlib import Path

MODELS = {
    "chatgpt": ("chatgpt-companion", "ChatGPT"),
    "快速": ("chatgpt-fast", "ChatGPT 快速"),
    "深度": ("chatgpt-deep", "ChatGPT 深度"),
    "deepseek": ("deepseek-companion", "DeepSeek Pro"),
    "deepseek快速": ("deepseek-backup", "DeepSeek 快速"),
}


class ModelControls:
    def __init__(self, folder: Path, owners, context):
        self.path = folder / "model-choice.json"
        self.owners = owners
        self.context = context
        self.lock = asyncio.Lock()

    def selected(self):
        """Read the committed preference; never overwrite an unreadable file."""
        if not self.path.exists():
            return "chatgpt-companion"
        choice = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(choice, dict) or choice.get("provider_id") not in {
            v[0] for v in MODELS.values()
        }:
            raise ValueError("Invalid shared model preference")
        return choice["provider_id"]

    def available(self, provider_id):
        return self.context.get_provider_by_id(provider_id) is not None

    async def receive(self, event):
        """Handle phone controls before any paid model call.

        Args:
            event: The incoming native event, never model-generated text.

        Returns:
            Whether this message was consumed as a model command.
        """
        if (
            not event.is_private_chat()
            or event.unified_msg_origin not in self.owners.owners
        ):
            return False
        text = event.get_message_str().strip().lower().rstrip("。！!")
        switch = re.fullmatch(r"(?:切换模型\s*|/model\s+)(\S+)", text)
        command = bool(switch) or text in ("当前模型", "模型列表", "/model", "切换模型")
        try:
            async with self.lock:
                selected = self.selected()
                if switch:
                    name = switch[1]
                    if name not in MODELS:
                        reply = "可选：ChatGPT、DeepSeek、快速、深度、DeepSeek快速。例如：切换模型 DeepSeek。"
                    elif not self.available(MODELS[name][0]):
                        reply = f"{MODELS[name][1]} 还没有配置好，当前模型保持不变。"
                    else:
                        selected = MODELS[name][0]
                        temp = self.path.with_suffix(".tmp")
                        temp.write_text(
                            json.dumps({"provider_id": selected}), encoding="utf-8"
                        )
                        temp.replace(self.path)
                        reply = f"已切换到 {MODELS[name][1]}，QQ 和微信共用这个选择，记忆不变。"
                elif command:
                    names = {value[0]: value[1] for value in MODELS.values()}
                    available = "、".join(
                        label for pid, label in MODELS.values() if self.available(pid)
                    )
                    backup = (
                        "已配置 DeepSeek 快速自动备用"
                        if self.available("deepseek-backup")
                        else "DeepSeek 备用尚未开通"
                    )
                    reply = f"当前首选：{names[selected]}；{backup}。\n可选：{available}。\n说“切换模型 DeepSeek”或“切换模型 ChatGPT”即可；GPT 三档共用一个账号额度。"
                # The native runner uses this explicit selection before assembling history/tools.
                if self.available(selected):
                    event.set_extra("selected_provider", selected)
                elif self.available("deepseek-backup"):
                    event.set_extra("selected_provider", "deepseek-backup")
                else:
                    event.set_extra("selected_provider", "chatgpt-companion")
        except (OSError, ValueError):
            if not command:
                return False
            reply = "模型偏好暂时无法保存或读取，这次没有切换。普通聊天仍使用已配置的默认模型。"
        if command:
            await event.send(event.plain_result(reply))
            event.stop_event()
        return command

    async def generate(self, **kwargs):
        """Use the shared choice and independent fallback for proactive generation."""
        selected = self.selected()
        candidates = list(
            dict.fromkeys([selected, "deepseek-backup", "chatgpt-companion"])
        )
        error = None
        for provider_id in candidates:
            if not self.available(provider_id):
                continue
            try:
                async with asyncio.timeout(70):
                    response = await self.context.llm_generate(
                        chat_provider_id=provider_id, **kwargs
                    )
                    if response.role == "err" or not response.completion_text.strip():
                        raise RuntimeError("Provider returned no usable reply")
                    return response
            except Exception as exc:
                error = exc
        raise RuntimeError(
            "No configured chat provider completed generation"
        ) from error
