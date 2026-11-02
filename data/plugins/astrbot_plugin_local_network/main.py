"""Install and restore owned network/media hooks as one lifecycle unit."""

import aiohttp.connector

from astrbot.api import logger
from astrbot.api.star import Context, Star
from astrbot.core.platform.sources.weixin_oc.weixin_oc_adapter import WeixinOCAdapter
from astrbot.core.platform.sources.weixin_oc.weixin_oc_client import WeixinOCClient

from .network_routes import (
    download_wechat_media,
    ensure_wechat_proxy_session,
    prefer_ipv6_resolver,
)
from .wechat_api import request_wechat_json
from .wechat_media import inbound_handler, media_resolver, message_text


class LocalNetworkPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.patches = []

    def _install(self, owner, name, replacement):
        original = getattr(owner, name)
        setattr(owner, name, replacement)
        self.patches.append((owner, name, original, replacement))

    async def initialize(self):
        self.patches = []
        try:
            self._install(
                aiohttp.connector,
                "DefaultResolver",
                prefer_ipv6_resolver(aiohttp.connector.DefaultResolver),
            )
            self._install(
                WeixinOCClient, "ensure_http_session", ensure_wechat_proxy_session
            )
            self._install(WeixinOCClient, "request_json", request_wechat_json)
            self._install(WeixinOCClient, "download_cdn_bytes", download_wechat_media)
            self._install(
                WeixinOCAdapter,
                "_resolve_inbound_media_component",
                media_resolver(WeixinOCAdapter._resolve_inbound_media_component),
            )
            self._install(
                WeixinOCAdapter,
                "_message_text_from_item_list",
                message_text(WeixinOCAdapter._message_text_from_item_list),
            )
            self._install(
                WeixinOCAdapter,
                "_handle_inbound_message",
                inbound_handler(WeixinOCAdapter._handle_inbound_message),
            )
        except BaseException:
            await self.terminate()
            raise
        logger.info(
            "Local network hooks enabled: IPv6 preferred; WeChat proxy and media recovery active."
        )

    async def terminate(self):
        for owner, name, original, replacement in reversed(self.patches):
            if getattr(owner, name) is replacement:
                setattr(owner, name, original)
        self.patches.clear()
