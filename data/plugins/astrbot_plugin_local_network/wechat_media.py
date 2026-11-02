"""Preserve native media decoding and report unsupported envelopes honestly."""

import json

from astrbot.api import logger
from astrbot.api.message_components import Plain

UNSUPPORTED_MESSAGE_NOTICE = (
    "[接收状态：微信只传来了不支持的消息占位，没有原始内容可供查看。"
    "请简短自然地说明没看到；如果对方发的是表情包，请改发普通图片或截图。"
    "不要猜测内容，不要复述这段状态说明或技术字段。]"
)


def media_resolver(original_media_resolver):
    async def resolve_wechat_media(adapter, item):
        """Keep a failed image visible to the conversation as an explicit failure.

        Args:
            adapter: The active WeChat adapter.
            item: An original inbound item from the official channel.

        Returns:
            The native component, or an honest missing-image text component.
        """
        item_type = int(item.get("type") or 0)
        if item_type == 8 and isinstance(item.get("unsupported_item"), dict):
            return Plain(UNSUPPORTED_MESSAGE_NOTICE)
        if item_type != adapter.IMAGE_ITEM_TYPE:
            return await original_media_resolver(adapter, item)
        try:
            component = await original_media_resolver(adapter, item)
            if component is not None:
                return component
        except Exception as exc:
            logger.warning(
                "WeChat inbound image is unavailable after download (%s).",
                type(exc).__name__,
            )
        return Plain(
            "[系统提示：本条微信图片未能下载，当前没有可看的图片内容。"
            "请如实说明图片没收到，并请对方重发；不要猜图中内容。]"
        )

    return resolve_wechat_media


def message_text(original_message_text):
    def describe_wechat_items(adapter, item_list, *, include_ref_text=False):
        """Keep unsupported-message notices visible in the native text summary.

        Args:
            adapter: The active WeChat adapter.
            item_list: Original items; neither items nor raw messages are changed.
            include_ref_text: Whether to include quoted-message text.

        Returns:
            Native text with an explicit notice for the observed type-8 envelope.
        """
        normalized = [
            {
                **item,
                "type": 1,
                "text_item": {"text": UNSUPPORTED_MESSAGE_NOTICE},
            }
            if int(item.get("type") or 0) == 8
            and isinstance(item.get("unsupported_item"), dict)
            else item
            for item in item_list or []
        ]
        return original_message_text(
            adapter, normalized, include_ref_text=include_ref_text
        )

    return describe_wechat_items


def inbound_handler(original_inbound_handler):
    async def inspect_wechat_message(adapter, message):
        """Log the shape, never credentials or contents, of unrecognized messages.

        Args:
            adapter: The active WeChat adapter.
            message: An unmodified message received from the official API.
        """
        items = message.get("item_list") or []
        if not items or any(item.get("type") not in (1, 2, 3, 4, 5) for item in items):
            shape = {
                "message_type": message.get("message_type"),
                "message_state": message.get("message_state"),
                "fields": sorted(message.keys()),
                "items": [
                    {
                        "type": item.get("type"),
                        "fields": sorted(item.keys()),
                        "nested_fields": {
                            key: sorted(value.keys())
                            for key, value in item.items()
                            if isinstance(value, dict)
                        },
                    }
                    for item in items
                ],
            }
            logger.warning("WeChat unreadable message shape: %s", json.dumps(shape))
        return await original_inbound_handler(adapter, message)

    return inspect_wechat_message
