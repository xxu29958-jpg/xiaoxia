"""Navigation recovery is limited to idempotent public page opens."""

import re
from urllib.parse import urlsplit

from .web_notebook import BackpackError

NETWORK_ERROR = re.compile(
    r"net::ERR_(?:CONNECTION_[A-Z_]+|TIMED_OUT|NAME_NOT_RESOLVED|PROXY_[A-Z_]+|TUNNEL_CONNECTION_FAILED|SSL_[A-Z_]+)"
)


async def navigate(browser, state, url):
    """Retry a failed page GET once on the independent mainland route."""
    state["navigation_recovery"] = ""
    try:
        return await browser._call(state, "browser_navigate", {"url": url})
    except BackpackError as exc:
        gateway = state["gateway"]
        if not NETWORK_ERROR.search(str(exc)) or not gateway.prefer_fallback(
            urlsplit(url).hostname
        ):
            raise
        state["navigation_recovery"] = (
            "Retried this page open through the mainland route after a network failure."
        )
        return await browser._call(state, "browser_navigate", {"url": url})
