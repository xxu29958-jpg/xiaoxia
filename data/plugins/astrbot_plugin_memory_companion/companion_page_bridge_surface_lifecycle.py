from __future__ import annotations

import inspect
from typing import Any

from .companion_page_bridge_support import (
    _COMPANION_PAGE_RUNTIME,
    _KNOWN_PRODUCER_CODES,
    MEMORY_PAGE_FORMAL_METHODS,
    CompanionPageBridgeError,
    _fail,
    _ResolvedAPI,
)


class CompanionPageBridgeSurfaceLifecycle:
    """Surface lifecycle; shared runtime resources belong to the facade."""

    @staticmethod
    def _formal_surface(api: Any) -> bool:
        missing = object()
        markers = [
            inspect.getattr_static(api, name, missing) is not missing
            for name in MEMORY_PAGE_FORMAL_METHODS
        ]
        return any(markers)

    def _select_surface(self, resolved: _ResolvedAPI) -> str:
        formal = self._formal_surface(resolved.api)
        with _COMPANION_PAGE_RUNTIME.lock:
            if formal:
                _COMPANION_PAGE_RUNTIME.formal_seen = True
            formal_seen = _COMPANION_PAGE_RUNTIME.formal_seen is True
        if formal:
            self._retire_legacy()
            return "formal"
        if formal_seen:
            self._retire_legacy()
            _fail("memory_page_contract_downgrade")
        return "legacy"

    def _retire_legacy(self) -> None:
        with self._lock:
            adapter = self._legacy_adapter
            self._legacy_api = None
            self._legacy_adapter = None
        if adapter is not None:
            adapter.close()

    @staticmethod
    def _formal_methods(api: Any) -> tuple[Any, Any, Any]:
        methods: list[Any] = []
        for name in MEMORY_PAGE_FORMAL_METHODS:
            try:
                method = getattr(api, name)
            except Exception:
                _fail("memory_page_contract_malformed")
            if not callable(method):
                _fail("memory_page_contract_malformed")
            methods.append(method)
        return methods[0], methods[1], methods[2]

    @staticmethod
    def _producer_error(exc: Exception, fallback: str) -> CompanionPageBridgeError:
        code = getattr(exc, "code", None)
        if type(code) is str and code in _KNOWN_PRODUCER_CODES:
            return CompanionPageBridgeError(code)
        return CompanionPageBridgeError(fallback)

    def _legacy_for(self, api: Any, *, create: bool) -> Any:
        from .companion_page_legacy import LegacyCompanionPageAdapter

        adapter: Any | None = None
        previous: Any | None = None
        error = ""
        with _COMPANION_PAGE_RUNTIME.lock:
            with self._lock:
                if _COMPANION_PAGE_RUNTIME.formal_seen is True:
                    previous = self._legacy_adapter
                    self._legacy_api = None
                    self._legacy_adapter = None
                    error = "memory_page_contract_downgrade"
                elif self._legacy_api is api and self._legacy_adapter is not None:
                    try:
                        self._legacy_adapter.assert_live()
                    except CompanionPageBridgeError as exc:
                        if exc.code == "memory_page_service_closed":
                            adapter = self._legacy_adapter
                        else:
                            error = exc.code
                    else:
                        if LegacyCompanionPageAdapter.matches(api):
                            adapter = self._legacy_adapter
                        else:
                            previous = self._legacy_adapter
                            self._legacy_api = None
                            self._legacy_adapter = None
                            error = "memory_page_legacy_unsupported"
                else:
                    previous = self._legacy_adapter
                    self._legacy_api = None
                    self._legacy_adapter = None
                    if not LegacyCompanionPageAdapter.matches(api):
                        error = "memory_page_legacy_unsupported"
                    elif not create:
                        error = "memory_page_photo_ref_stale"
                    else:
                        adapter = LegacyCompanionPageAdapter(api)
                        self._legacy_api = api
                        self._legacy_adapter = adapter
        if previous is not None and previous is not adapter:
            previous.close()
        if error:
            _fail(error)
        return adapter
