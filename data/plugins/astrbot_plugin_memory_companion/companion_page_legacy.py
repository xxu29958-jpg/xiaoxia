from __future__ import annotations

import inspect
import re
import secrets
import threading
from collections import OrderedDict
from typing import Any

from .companion_page_bridge import (
    COMPANION_MODULE_NAMES,
    COMPANION_PLUGIN_ID,
)
from .companion_page_legacy_photo_access import LegacyCompanionPageAdapterPhotoAccess
from .companion_page_legacy_projection import LegacyCompanionPageAdapterProjection
from .companion_page_legacy_support import (
    _LEGACY_REQUIRED_API_METHODS as _LEGACY_REQUIRED_API_METHODS,
)
from .companion_page_legacy_support import _MAX_PHOTO_REFS as _MAX_PHOTO_REFS
from .companion_page_legacy_support import _PHOTO_REF_RE as _PHOTO_REF_RE
from .companion_page_legacy_support import _PHOTO_TTL_SECONDS as _PHOTO_TTL_SECONDS
from .companion_page_legacy_support import _SAFE_CODE_RE as _SAFE_CODE_RE
from .companion_page_legacy_support import _available_dates as _available_dates
from .companion_page_legacy_support import (
    _bounded_data_snapshot as _bounded_data_snapshot,
)
from .companion_page_legacy_support import _date as _date
from .companion_page_legacy_support import _date_from_timestamp as _date_from_timestamp
from .companion_page_legacy_support import _details_for_date as _details_for_date
from .companion_page_legacy_support import _diaries_for_date as _diaries_for_date
from .companion_page_legacy_support import _event_list as _event_list
from .companion_page_legacy_support import _fail as _fail
from .companion_page_legacy_support import _history_samples as _history_samples
from .companion_page_legacy_support import _LegacyPhoto as _LegacyPhoto
from .companion_page_legacy_support import _opaque_identifier as _opaque_identifier
from .companion_page_legacy_support import _plan as _plan
from .companion_page_legacy_support import _plan_for_date as _plan_for_date
from .companion_page_legacy_support import _plan_item as _plan_item
from .companion_page_legacy_support import _safe_code as _safe_code
from .companion_page_legacy_support import _string_list as _string_list
from .companion_page_legacy_support import _text as _text
from .companion_page_legacy_support import _timestamp as _timestamp


class LegacyCompanionPageAdapter(
    LegacyCompanionPageAdapterPhotoAccess, LegacyCompanionPageAdapterProjection
):
    """Project one exact known N-1 facade into the formal wire."""

    def __init__(self, api: Any) -> None:
        if not self.matches(api):
            _fail("memory_page_legacy_unsupported")
        self._api = api
        self._plugin = api._plugin
        self._data_lock = self._plugin._data_lock
        self._current_item_getter_static = inspect.getattr_static(
            self._plugin,
            "_get_current_plan_item",
        )
        self._host_generation = api._story_migration_generation
        self.instance_generation = secrets.token_hex(16)
        self._secret = secrets.token_bytes(32)
        self._lock = threading.RLock()
        self._closed = False
        self._photos: OrderedDict[str, _LegacyPhoto] = OrderedDict()

    @classmethod
    def matches(cls, api: Any) -> bool:
        try:
            if (
                api is None
                or api.__class__.__name__ != "PrivateCompanionExtensionAPI"
                or api.__class__.__module__ not in COMPANION_MODULE_NAMES
            ):
                return False
            missing = object()
            if any(
                inspect.getattr_static(api, name, missing) is not missing
                for name in (
                    "memory_page_capabilities",
                    "export_memory_page_snapshot",
                    "read_memory_page_photo",
                )
            ):
                return False
            methods = {
                name: getattr(api, name, None) for name in _LEGACY_REQUIRED_API_METHODS
            }
            if any(not callable(method) for method in methods.values()):
                return False
            if any(
                inspect.signature(methods[name]).parameters
                for name in (
                    "get_p6_readonly_status",
                    "story_migration_capabilities",
                    "get_bot_identity",
                )
            ):
                return False
            export_method = methods["export_story_migration_snapshot"]
            export_parameters = list(
                inspect.signature(export_method).parameters.values()
            )
            if (
                inspect.iscoroutinefunction(export_method) is not True
                or len(export_parameters) != 1
                or export_parameters[0].name != "lease_token"
                or export_parameters[0].kind is not inspect.Parameter.KEYWORD_ONLY
                or export_parameters[0].default != ""
            ):
                return False
            if any(
                inspect.iscoroutinefunction(methods[name])
                for name in (
                    "get_p6_readonly_status",
                    "story_migration_capabilities",
                    "get_bot_identity",
                )
            ):
                return False
            generation = getattr(api, "_story_migration_generation", None)
            lifecycle = getattr(api, "_story_migration_state", None)
            if (
                not isinstance(generation, str)
                or re.fullmatch(r"[0-9a-f]{32}", generation) is None
                or lifecycle != "ready"
            ):
                return False
            plugin = getattr(api, "_plugin", None)
            data_lock = getattr(plugin, "_data_lock", None)
            identity = getattr(plugin, "plugin_identity", None)
            current_item_getter = getattr(plugin, "_get_current_plan_item", None)
            current_parameters = (
                list(inspect.signature(current_item_getter).parameters.values())
                if callable(current_item_getter)
                else []
            )
            return bool(
                plugin is not None
                and plugin.__class__.__name__ == "PrivateCompanionPlugin"
                and plugin.__class__.__module__ in COMPANION_MODULE_NAMES
                and type(identity) is dict
                and identity.get("plugin_id") == COMPANION_PLUGIN_ID
                and identity.get("version") == "6.4.1"
                and type(getattr(plugin, "data", None)) is dict
                and data_lock is not None
                and hasattr(data_lock, "__aenter__")
                and hasattr(data_lock, "__aexit__")
                and callable(current_item_getter)
                and not inspect.iscoroutinefunction(current_item_getter)
                and len(current_parameters) == 1
                and current_parameters[0].name == "plan"
                and current_parameters[0].kind
                is inspect.Parameter.POSITIONAL_OR_KEYWORD
            )
        except Exception:
            return False

    def _live_plugin(self) -> Any:
        with self._lock:
            if self._closed:
                _fail("memory_page_service_closed")
        identity = getattr(self._plugin, "plugin_identity", None)
        if (
            getattr(self._api, "_plugin", None) is not self._plugin
            or getattr(self._plugin, "_data_lock", None) is not self._data_lock
            or inspect.getattr_static(
                self._plugin,
                "_get_current_plan_item",
                None,
            )
            is not self._current_item_getter_static
            or type(identity) is not dict
            or identity.get("plugin_id") != COMPANION_PLUGIN_ID
            or identity.get("version") != "6.4.1"
            or getattr(self._api, "_story_migration_generation", None)
            != self._host_generation
            or getattr(self._api, "_story_migration_state", None) != "ready"
        ):
            _fail("memory_page_service_closed")
        return self._plugin

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._photos.clear()

    def assert_live(self) -> None:
        self._live_plugin()

    def _coordination(self, plugin: Any) -> dict[str, Any]:
        getter = getattr(plugin, "_memory_companion_coordination_status", None)
        if not callable(getter):
            return {
                "available": False,
                "state": "unavailable",
                "reason_code": "legacy_bridge_status_unavailable",
            }
        try:
            value = getter()
        except Exception:
            return {
                "available": False,
                "state": "degraded",
                "reason_code": "legacy_bridge_status_unreadable",
            }
        raw = value if isinstance(value, dict) else {}
        state_value = _safe_code(raw.get("state"), "degraded")
        if state_value not in {
            "ready",
            "degraded",
            "local_only",
            "disabled",
            "inactive",
            "unavailable",
        }:
            state_value = "degraded"
        available = bool(
            raw.get("available")
            and state_value
            not in {"degraded", "local_only", "disabled", "inactive", "unavailable"}
            and not raw.get("degraded")
        )
        reason = _safe_code(
            raw.get("reason") or raw.get("error_code"),
            "" if available else "legacy_bridge_degraded",
        )
        return {"available": available, "state": state_value, "reason_code": reason}


__all__ = ["LegacyCompanionPageAdapter"]
