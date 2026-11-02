from .event_handlers_collection import EventHandlerMixinCollection
from .event_handlers_compatibility import EventHandlerMixinCompatibility
from .event_handlers_decoration import EventHandlerMixinDecoration
from .event_handlers_emotion import EventHandlerMixinEmotion
from .event_handlers_media import EventHandlerMixinMedia
from .event_handlers_parsing import EventHandlerMixinParsing
from .event_handlers_prompt import EventHandlerMixinPrompt
from .event_handlers_response import EventHandlerMixinResponse
from .event_handlers_scope import EventHandlerMixinScope
from .event_handlers_selection import EventHandlerMixinSelection
from .event_handlers_support import LLM_REQUEST_ORIGIN_CHAT as LLM_REQUEST_ORIGIN_CHAT
from .event_handlers_support import (
    LLM_REQUEST_ORIGIN_EXTRA_KEY as LLM_REQUEST_ORIGIN_EXTRA_KEY,
)
from .event_handlers_support import (
    LLM_REQUEST_ORIGIN_PLUGIN as LLM_REQUEST_ORIGIN_PLUGIN,
)
from .event_handlers_support import (
    TRIGGER_SCOPE_CHAT_AND_PLUGIN as TRIGGER_SCOPE_CHAT_AND_PLUGIN,
)
from .event_handlers_support import TRIGGER_SCOPE_CHAT_ONLY as TRIGGER_SCOPE_CHAT_ONLY
from .event_handlers_support import normalize_trigger_scope as normalize_trigger_scope


class EventHandlerMixin(
    EventHandlerMixinScope,
    EventHandlerMixinEmotion,
    EventHandlerMixinParsing,
    EventHandlerMixinCompatibility,
    EventHandlerMixinCollection,
    EventHandlerMixinPrompt,
    EventHandlerMixinSelection,
    EventHandlerMixinResponse,
    EventHandlerMixinDecoration,
    EventHandlerMixinMedia,
):
    """处理图片上传、LLM 响应解析、消息装饰等事件"""
