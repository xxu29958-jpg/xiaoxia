from typing import Any

TRIGGER_SCOPE_CHAT_ONLY = "only_chat_llm"


TRIGGER_SCOPE_CHAT_AND_PLUGIN = "chat_and_plugin_llm"


LLM_REQUEST_ORIGIN_EXTRA_KEY = "meme_manager_llm_request_origin"


LLM_REQUEST_ORIGIN_CHAT = "chat"


LLM_REQUEST_ORIGIN_PLUGIN = "plugin"


def normalize_trigger_scope(value: Any) -> str:
    """Resolve a supported trigger scope, defaulting to ordinary chat.

    Args:
        value: The configured LLM trigger scope.

    Returns:
        One of the two supported trigger scopes.
    """
    scope = str(value or TRIGGER_SCOPE_CHAT_ONLY).strip().lower()
    if scope == TRIGGER_SCOPE_CHAT_AND_PLUGIN:
        return scope
    return TRIGGER_SCOPE_CHAT_ONLY
