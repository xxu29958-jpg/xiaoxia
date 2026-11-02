from __future__ import annotations

from typing import Any

from .bridge_capability_authority import MemoryCompanionBridgeCapabilityAuthority
from .bridge_capability_probes import MemoryCompanionBridgeCapabilityProbes
from .bridge_capture import MemoryCompanionBridgeCapture
from .bridge_emotion_delivery import MemoryCompanionBridgeEmotionDelivery
from .bridge_memory_access import MemoryCompanionBridgeMemoryAccess
from .bridge_profiles import MemoryCompanionBridgeProfiles
from .bridge_support import (
    _COMPANION_EXPRESSION_BEHAVIORS as _COMPANION_EXPRESSION_BEHAVIORS,
)
from .bridge_support import (
    _COMPANION_EXPRESSION_BLOCKERS as _COMPANION_EXPRESSION_BLOCKERS,
)
from .bridge_support import (
    _COMPANION_EXPRESSION_CONTRACTS as _COMPANION_EXPRESSION_CONTRACTS,
)
from .bridge_support import (
    _COMPANION_EXPRESSION_DIRECTNESS as _COMPANION_EXPRESSION_DIRECTNESS,
)
from .bridge_support import (
    _COMPANION_EXPRESSION_DISCLOSURE as _COMPANION_EXPRESSION_DISCLOSURE,
)
from .bridge_support import _COMPANION_EXPRESSION_HUMOR as _COMPANION_EXPRESSION_HUMOR
from .bridge_support import _COMPANION_EXPRESSION_PACING as _COMPANION_EXPRESSION_PACING
from .bridge_support import (
    _COMPANION_EXPRESSION_REASON_CODES as _COMPANION_EXPRESSION_REASON_CODES,
)
from .bridge_support import (
    _COMPANION_EXPRESSION_SAFETY_MODES as _COMPANION_EXPRESSION_SAFETY_MODES,
)
from .bridge_support import _COMPANION_EXPRESSION_TOPIC as _COMPANION_EXPRESSION_TOPIC
from .bridge_support import (
    _COMPANION_EXPRESSION_VALIDATION as _COMPANION_EXPRESSION_VALIDATION,
)
from .bridge_support import _COMPANION_INTERACTION_BANDS as _COMPANION_INTERACTION_BANDS
from .bridge_support import _COMPANION_PROJECTION_SECRET as _COMPANION_PROJECTION_SECRET
from .bridge_support import (
    _COMPANION_RELATIONSHIP_PHASES as _COMPANION_RELATIONSHIP_PHASES,
)
from .bridge_support import _EMOTION_DELIVERY_CONSUMER as _EMOTION_DELIVERY_CONSUMER
from .bridge_support import _EMOTION_INGRESS_ORIGINS as _EMOTION_INGRESS_ORIGINS
from .bridge_support import _PRIVATE_COMPANION_NAMES as _PRIVATE_COMPANION_NAMES
from .bridge_support import _PRIVATE_COMPANION_ROOT as _PRIVATE_COMPANION_ROOT
from .bridge_support import LOCAL_TZ as LOCAL_TZ
from .bridge_support import (
    _AuthenticatedCompanionProjection as _AuthenticatedCompanionProjection,
)
from .bridge_support import (
    _companion_projection_signature as _companion_projection_signature,
)
from .bridge_support import _EmotionAdminContext as _EmotionAdminContext
from .bridge_support import _EmotionDeliveryContext as _EmotionDeliveryContext
from .bridge_support import _EmotionPageAdminCapability as _EmotionPageAdminCapability
from .bridge_support import _EmotionProducerCapability as _EmotionProducerCapability
from .bridge_support import _EmotionProducerContext as _EmotionProducerContext
from .bridge_support import _local_time_label as _local_time_label
from .bridge_support import (
    consume_authenticated_companion_projection as consume_authenticated_companion_projection,
)
from .bridge_support import (
    sanitize_companion_expression_decision as sanitize_companion_expression_decision,
)
from .bridge_support import (
    sanitize_companion_relationship_projection as sanitize_companion_relationship_projection,
)
from .bridge_support import serialize_memory as serialize_memory
from .capability_probe import (
    CapabilityCache,
)
from .scoped_store import ScopedStore


class MemoryCompanionBridge(
    MemoryCompanionBridgeCapabilityAuthority,
    MemoryCompanionBridgeCapture,
    MemoryCompanionBridgeProfiles,
    MemoryCompanionBridgeMemoryAccess,
    MemoryCompanionBridgeCapabilityProbes,
    MemoryCompanionBridgeEmotionDelivery,
):
    """Public bridge for other plugins.

    The bridge intentionally accepts structured fields. A caller should say
    whether something is a bot action, a persona-life fragment, a real user
    fact, or an imported summary instead of handing over vague prose.
    """

    def __init__(self, plugin: Any):
        self._plugin = plugin
        self.__scoped_store: ScopedStore | None = None
        self.__scoped_store_resolved = False
        self._capability_cache = CapabilityCache()
        self._emotion_producer_token = object()
        self._emotion_page_admin_token = object()
        self._active = True

    def bind_cache_invalidation(self, store: Any) -> bool:
        """Wire the capability cache into the store's invalidation broadcast.

        Called by the plugin assembly once the service and its store exist.
        It stays out of ``__init__`` on purpose: constructing a bridge and
        probing the bot-personal contract are pure contract work that must not
        touch the plugin, its services or its database.
        """
        register = getattr(store, "register_invalidation", None)
        if not callable(register):
            return False
        register("bridge_capability", self._capability_cache.clear)
        return True

    @property
    def _scoped_store(self) -> ScopedStore | None:
        """Resolve the namespace store only when a namespace API is used.

        Bot-personal capability probes are pure contract probes and must not
        touch plugin services or databases merely because a Bridge object was
        constructed.
        """
        if not self.__scoped_store_resolved:
            candidate = getattr(self._plugin, "scoped_store", None)
            self.__scoped_store = (
                candidate if isinstance(candidate, ScopedStore) else None
            )
            self.__scoped_store_resolved = True
        return self.__scoped_store

    def bridge_lifecycle_status(self) -> dict[str, Any]:
        """Expose only whether this in-process bridge can still serve calls."""
        return {"active": self._active}

    def deactivate(self) -> None:
        """Revoke all issued capabilities before plugin service shutdown."""
        self._active = False
        self._emotion_producer_token = object()
        self._emotion_page_admin_token = object()
