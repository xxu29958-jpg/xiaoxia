from __future__ import annotations

import sys
import threading
from typing import Any

from .companion_page_bridge_photos import CompanionPageBridgePhotos
from .companion_page_bridge_snapshots import CompanionPageBridgeSnapshots
from .companion_page_bridge_support import (
    _COMPANION_PAGE_RUNTIME as _COMPANION_PAGE_RUNTIME,
)
from .companion_page_bridge_support import (
    _COMPANION_PAGE_RUNTIME_KEY as _COMPANION_PAGE_RUNTIME_KEY,
)
from .companion_page_bridge_support import _COORDINATION_FIELDS as _COORDINATION_FIELDS
from .companion_page_bridge_support import _COORDINATION_STATES as _COORDINATION_STATES
from .companion_page_bridge_support import _DAILY_STATE_FIELDS as _DAILY_STATE_FIELDS
from .companion_page_bridge_support import _DAY_FIELDS as _DAY_FIELDS
from .companion_page_bridge_support import _DESCRIPTOR_FIELDS as _DESCRIPTOR_FIELDS
from .companion_page_bridge_support import _DETAIL_FIELDS as _DETAIL_FIELDS
from .companion_page_bridge_support import _DETAIL_STATES as _DETAIL_STATES
from .companion_page_bridge_support import _DIARY_FIELDS as _DIARY_FIELDS
from .companion_page_bridge_support import _FEATURE_FIELDS as _FEATURE_FIELDS
from .companion_page_bridge_support import _GENERATION_RE as _GENERATION_RE
from .companion_page_bridge_support import (
    _KNOWN_PRODUCER_CODES as _KNOWN_PRODUCER_CODES,
)
from .companion_page_bridge_support import _LIFECYCLE_STATES as _LIFECYCLE_STATES
from .companion_page_bridge_support import _OPAQUE_ID_RE as _OPAQUE_ID_RE
from .companion_page_bridge_support import _PHOTO_FIELDS as _PHOTO_FIELDS
from .companion_page_bridge_support import _PHOTO_KINDS as _PHOTO_KINDS
from .companion_page_bridge_support import _PHOTO_REF_RE as _PHOTO_REF_RE
from .companion_page_bridge_support import _PHOTO_RESULT_FIELDS as _PHOTO_RESULT_FIELDS
from .companion_page_bridge_support import _PLAN_FIELDS as _PLAN_FIELDS
from .companion_page_bridge_support import _PLAN_ITEM_FIELDS as _PLAN_ITEM_FIELDS
from .companion_page_bridge_support import _PLAN_SOURCES as _PLAN_SOURCES
from .companion_page_bridge_support import _REASON_RE as _REASON_RE
from .companion_page_bridge_support import _SHA256_RE as _SHA256_RE
from .companion_page_bridge_support import _SNAPSHOT_FIELDS as _SNAPSHOT_FIELDS
from .companion_page_bridge_support import (
    _SNAPSHOT_HASHED_FIELDS as _SNAPSHOT_HASHED_FIELDS,
)
from .companion_page_bridge_support import _SNAPSHOT_ID_RE as _SNAPSHOT_ID_RE
from .companion_page_bridge_support import (
    COMPANION_MODULE_NAMES as COMPANION_MODULE_NAMES,
)
from .companion_page_bridge_support import COMPANION_PLUGIN_ID as COMPANION_PLUGIN_ID
from .companion_page_bridge_support import (
    MEMORY_PAGE_API_FAMILY as MEMORY_PAGE_API_FAMILY,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_API_VERSION as MEMORY_PAGE_API_VERSION,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_FORMAL_METHODS as MEMORY_PAGE_FORMAL_METHODS,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_PHOTO_BASE64_MAX_CHARS as MEMORY_PAGE_PHOTO_BASE64_MAX_CHARS,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_PHOTO_MAX_BYTES as MEMORY_PAGE_PHOTO_MAX_BYTES,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES as MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_PHOTO_VERSION as MEMORY_PAGE_PHOTO_VERSION,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_REQUIRED_CAPABILITIES as MEMORY_PAGE_REQUIRED_CAPABILITIES,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_SNAPSHOT_MAX_BYTES as MEMORY_PAGE_SNAPSHOT_MAX_BYTES,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_SNAPSHOT_VERSION as MEMORY_PAGE_SNAPSHOT_VERSION,
)
from .companion_page_bridge_support import (
    MEMORY_PAGE_SUPPORTED_TASK_VERSIONS as MEMORY_PAGE_SUPPORTED_TASK_VERSIONS,
)
from .companion_page_bridge_support import MEMORY_PLUGIN_ID as MEMORY_PLUGIN_ID
from .companion_page_bridge_support import (
    CompanionPageBridgeError as CompanionPageBridgeError,
)
from .companion_page_bridge_support import CompanionPagePhoto as CompanionPagePhoto
from .companion_page_bridge_support import (
    CompanionPageSnapshot as CompanionPageSnapshot,
)
from .companion_page_bridge_support import _canonical_json as _canonical_json
from .companion_page_bridge_support import _exact_mapping as _exact_mapping
from .companion_page_bridge_support import _fail as _fail
from .companion_page_bridge_support import _GenerationChanged as _GenerationChanged
from .companion_page_bridge_support import (
    _install_companion_page_runtime as _install_companion_page_runtime,
)
from .companion_page_bridge_support import _integer as _integer
from .companion_page_bridge_support import _iso_date as _iso_date
from .companion_page_bridge_support import _photo_magic as _photo_magic
from .companion_page_bridge_support import _ResolvedAPI as _ResolvedAPI
from .companion_page_bridge_support import _text as _text
from .companion_page_bridge_support import _text_list as _text_list
from .companion_page_bridge_support import _validate_detail as _validate_detail
from .companion_page_bridge_support import _validate_diary as _validate_diary
from .companion_page_bridge_support import _validate_plan_item as _validate_plan_item
from .companion_page_bridge_support import (
    _validate_snapshot_photo as _validate_snapshot_photo,
)
from .companion_page_bridge_support import (
    seal_memory_page_snapshot as seal_memory_page_snapshot,
)
from .companion_page_bridge_support import (
    snapshot_payload_sha256 as snapshot_payload_sha256,
)
from .companion_page_bridge_support import (
    validate_memory_page_descriptor as validate_memory_page_descriptor,
)
from .companion_page_bridge_support import (
    validate_memory_page_photo as validate_memory_page_photo,
)
from .companion_page_bridge_support import (
    validate_memory_page_snapshot as validate_memory_page_snapshot,
)
from .companion_page_bridge_surface_lifecycle import CompanionPageBridgeSurfaceLifecycle


class CompanionPageBridge(
    CompanionPageBridgeSurfaceLifecycle,
    CompanionPageBridgeSnapshots,
    CompanionPageBridgePhotos,
):
    """Resolve and consume the current Companion facade without retaining it."""

    def __init__(self, owner: Any | None = None) -> None:
        self._owner = owner
        self._lock = threading.RLock()
        self._legacy_api: Any | None = None
        self._legacy_adapter: Any | None = None

    @property
    def formal_seen(self) -> bool:
        with _COMPANION_PAGE_RUNTIME.lock:
            return _COMPANION_PAGE_RUNTIME.formal_seen is True

    @staticmethod
    def _registry_identity_matches(metadata: Any) -> bool:
        expected = COMPANION_PLUGIN_ID.casefold().replace("-", "_")
        values = (
            getattr(metadata, "name", ""),
            getattr(metadata, "root_dir_name", ""),
            getattr(metadata, "module_path", ""),
            getattr(getattr(metadata, "module", None), "__name__", ""),
            getattr(getattr(metadata, "star_cls", None), "plugin_id", ""),
            getattr(type(getattr(metadata, "star_cls", None)), "__module__", ""),
        )
        for value in values:
            try:
                text = str(value or "").casefold().replace("-", "_").replace("/", ".")
            except Exception:
                continue
            if expected in {part for part in text.split(".") if part}:
                return True
        return False

    def _resolve_api(self) -> _ResolvedAPI:
        candidates: list[_ResolvedAPI] = []
        getter_failed = False
        getter_returned_none = False

        def add_candidate(api: Any, source: str) -> None:
            if api is not None and all(
                candidate.api is not api for candidate in candidates
            ):
                candidates.append(_ResolvedAPI(api=api, module_name=source))

        context = getattr(self._owner, "context", None)
        get_all = getattr(context, "get_all_stars", None)
        get_one = getattr(context, "get_registered_star", None)
        registry_available = callable(get_all) or callable(get_one)
        registry_entries: list[tuple[Any, bool]] = []
        positions: dict[int, int] = {}
        if callable(get_all):
            try:
                stars = list(get_all() or [])
            except Exception:
                getter_failed = True
                stars = []
            for metadata in stars:
                if metadata is None or id(metadata) in positions:
                    continue
                positions[id(metadata)] = len(registry_entries)
                registry_entries.append((metadata, False))
        if callable(get_one):
            try:
                metadata = get_one(COMPANION_PLUGIN_ID)
            except Exception:
                getter_failed = True
                metadata = None
            if metadata is not None:
                position = positions.get(id(metadata))
                if position is None:
                    registry_entries.append((metadata, True))
                else:
                    registry_entries[position] = (metadata, True)

        for metadata, exact_match in registry_entries:
            if not bool(getattr(metadata, "activated", True)):
                continue
            if not exact_match and not self._registry_identity_matches(metadata):
                continue
            module = getattr(metadata, "module", None)
            if module is not None:
                try:
                    getter = getattr(module, "get_private_companion_api", None)
                except Exception:
                    getter_failed = True
                else:
                    if callable(getter):
                        try:
                            api = getter()
                        except Exception:
                            getter_failed = True
                        else:
                            if api is None:
                                getter_returned_none = True
                            else:
                                add_candidate(
                                    api, str(getattr(module, "__name__", "registry"))
                                )
            instance = getattr(metadata, "star_cls", None)
            if instance is None and exact_match and hasattr(metadata, "extension_api"):
                instance = metadata
            try:
                instance_api = getattr(instance, "extension_api", None)
            except Exception:
                getter_failed = True
            else:
                if instance_api is not None:
                    add_candidate(instance_api, "registry")

        # A current-plugin registry is authoritative. Module aliases can
        # survive unload/reload and must never resurrect an old generation.
        if not registry_available:
            for module_name in COMPANION_MODULE_NAMES:
                module = sys.modules.get(module_name)
                if module is None:
                    continue
                try:
                    getter = getattr(module, "get_private_companion_api", None)
                except Exception:
                    getter_failed = True
                    continue
                if not callable(getter):
                    getter_failed = True
                    continue
                try:
                    api = getter()
                except Exception:
                    getter_failed = True
                    continue
                if api is None:
                    getter_returned_none = True
                    continue
                add_candidate(api, module_name)
        if len(candidates) > 1:
            if any(
                CompanionPageBridge._formal_surface(candidate.api)
                for candidate in candidates
            ):
                with _COMPANION_PAGE_RUNTIME.lock:
                    _COMPANION_PAGE_RUNTIME.formal_seen = True
            _fail("memory_page_companion_ambiguous")
        if getter_failed or (candidates and getter_returned_none):
            if candidates and CompanionPageBridge._formal_surface(candidates[0].api):
                with _COMPANION_PAGE_RUNTIME.lock:
                    _COMPANION_PAGE_RUNTIME.formal_seen = True
            _fail("memory_page_companion_unreadable")
        if candidates:
            return candidates[0]
        _fail("memory_page_companion_unavailable")


__all__ = [
    "COMPANION_MODULE_NAMES",
    "COMPANION_PLUGIN_ID",
    "CompanionPageBridge",
    "CompanionPageBridgeError",
    "CompanionPagePhoto",
    "CompanionPageSnapshot",
    "MEMORY_PAGE_API_FAMILY",
    "MEMORY_PAGE_API_VERSION",
    "MEMORY_PAGE_PHOTO_MAX_BYTES",
    "MEMORY_PAGE_PHOTO_VERSION",
    "MEMORY_PAGE_SNAPSHOT_VERSION",
    "MEMORY_PAGE_SUPPORTED_TASK_VERSIONS",
    "MEMORY_PLUGIN_ID",
    "seal_memory_page_snapshot",
    "snapshot_payload_sha256",
    "validate_memory_page_descriptor",
    "validate_memory_page_photo",
    "validate_memory_page_snapshot",
]
