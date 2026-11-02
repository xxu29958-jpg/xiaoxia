from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

"""Independent Codex OAuth provider adapted from AstrBot production source."""


oauth_provider_stat_kind: ContextVar[str] = ContextVar(
    "oauth_provider_stat_kind",
    default="text",
)


IMAGE_WEBSOCKET_MAX_MESSAGE_BYTES = 64 * 1024 * 1024


IMAGE_WEBSOCKET_MAX_TRANSCRIPT_BYTES = 128 * 1024 * 1024


IMAGE_WEBSOCKET_MAX_EVENTS = 16384


OAUTH_IMAGE_MODEL_ALIASES = frozenset(
    {"gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"}
)


class OpenAIOAuthImageStreamError(RuntimeError):
    """Sanitized image transport failure with a safe retry indicator."""

    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(f"Codex image WebSocket request ended: {reason_code}.")
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class OAuthPlugImageResult:
    path: str
    mime_type: str = "image/png"
    revised_prompt: str = ""
    raw: dict[str, Any] | None = None
