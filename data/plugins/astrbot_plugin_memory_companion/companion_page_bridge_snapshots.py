from __future__ import annotations

import asyncio
import inspect
from typing import Any

from .companion_page_bridge_support import (
    MEMORY_PLUGIN_ID,
    CompanionPageBridgeError,
    CompanionPageSnapshot,
    _fail,
    _GenerationChanged,
    _iso_date,
    _ResolvedAPI,
    validate_memory_page_descriptor,
    validate_memory_page_snapshot,
)


class CompanionPageBridgeSnapshots:
    """Snapshots; shared runtime resources belong to the facade."""

    def _same_api(self, before: _ResolvedAPI) -> _ResolvedAPI:
        try:
            after = self._resolve_api()
        except CompanionPageBridgeError:
            raise _GenerationChanged from None
        if after.api is not before.api:
            raise _GenerationChanged
        return after

    def _same_formal_generation(
        self,
        resolved: _ResolvedAPI,
        capabilities: Any,
        generation: str,
    ) -> None:
        self._same_api(resolved)
        try:
            descriptor = validate_memory_page_descriptor(capabilities())
        except CompanionPageBridgeError:
            self._same_api(resolved)
            raise
        except Exception:
            self._same_api(resolved)
            _fail("memory_page_contract_malformed")
        if (
            descriptor["instance_generation"] != generation
            or descriptor["lifecycle_state"] != "ready"
        ):
            raise _GenerationChanged
        self._same_api(resolved)

    def _same_legacy_generation(
        self,
        resolved: _ResolvedAPI,
        adapter: Any,
    ) -> None:
        self._same_api(resolved)
        try:
            adapter.assert_live()
        except CompanionPageBridgeError as exc:
            if exc.code != "memory_page_service_closed":
                raise
            from .companion_page_legacy import LegacyCompanionPageAdapter

            if LegacyCompanionPageAdapter.matches(resolved.api):
                raise _GenerationChanged from None
            raise
        self._same_api(resolved)

    async def _formal_snapshot_once(
        self,
        resolved: _ResolvedAPI,
        selected_date: str,
    ) -> CompanionPageSnapshot:
        try:
            capabilities, exporter, _reader = self._formal_methods(resolved.api)
        except CompanionPageBridgeError:
            self._same_api(resolved)
            raise
        try:
            descriptor = validate_memory_page_descriptor(capabilities())
        except CompanionPageBridgeError:
            self._same_api(resolved)
            raise
        except Exception:
            self._same_api(resolved)
            _fail("memory_page_contract_malformed")
        self._same_api(resolved)
        if descriptor["lifecycle_state"] != "ready":
            _fail("memory_page_snapshot_state_unavailable")
        generation = descriptor["instance_generation"]
        try:
            pending = exporter(
                target_plugin_id=MEMORY_PLUGIN_ID,
                selected_date=selected_date,
            )
            if not inspect.isawaitable(pending):
                _fail("memory_page_contract_malformed")
            raw = await pending
        except asyncio.CancelledError:
            raise
        except CompanionPageBridgeError:
            self._same_formal_generation(resolved, capabilities, generation)
            raise
        except Exception as exc:
            self._same_formal_generation(resolved, capabilities, generation)
            raise self._producer_error(
                exc, "memory_page_snapshot_build_failed"
            ) from None
        self._same_formal_generation(resolved, capabilities, generation)
        payload = validate_memory_page_snapshot(
            raw,
            expected_generation=generation,
            expected_selected_date=selected_date,
        )
        return CompanionPageSnapshot(payload=payload, mode="formal")

    async def _legacy_snapshot_once(
        self,
        resolved: _ResolvedAPI,
        selected_date: str,
    ) -> CompanionPageSnapshot:
        adapter: Any | None = None
        try:
            adapter = self._legacy_for(resolved.api, create=True)
            raw = await adapter.export_snapshot(selected_date)
        except asyncio.CancelledError:
            raise
        except CompanionPageBridgeError as exc:
            if adapter is not None and exc.code == "memory_page_service_closed":
                try:
                    self._same_legacy_generation(resolved, adapter)
                except _GenerationChanged:
                    raise
                except CompanionPageBridgeError:
                    self._retire_legacy()
                    raise
                self._retire_legacy()
            else:
                self._same_api(resolved)
            raise
        except Exception:
            try:
                self._same_api(resolved)
            except _GenerationChanged:
                raise
            _fail("memory_page_snapshot_build_failed")
        try:
            self._same_legacy_generation(resolved, adapter)
            if self._select_surface(resolved) != "legacy":
                raise _GenerationChanged
            payload = validate_memory_page_snapshot(
                raw,
                expected_generation=adapter.instance_generation,
                expected_selected_date=selected_date,
            )
            self._same_legacy_generation(resolved, adapter)
        except CompanionPageBridgeError as exc:
            if exc.code == "memory_page_service_closed":
                self._retire_legacy()
            raise
        return CompanionPageSnapshot(payload=payload, mode="legacy")

    async def export_snapshot(
        self,
        selected_date: str = "",
        *,
        expected_mode: str | None = None,
    ) -> CompanionPageSnapshot:
        selected = _iso_date(
            selected_date,
            code="memory_page_snapshot_invalid_date",
        )
        if expected_mode not in {None, "formal", "legacy"}:
            _fail("memory_page_photo_ref_invalid")
        for attempt in range(2):
            resolved = self._resolve_api()
            surface = self._select_surface(resolved)
            if expected_mode is not None and surface != expected_mode:
                _fail("memory_page_photo_ref_stale")
            try:
                if surface == "formal":
                    return await self._formal_snapshot_once(resolved, selected)
                return await self._legacy_snapshot_once(resolved, selected)
            except _GenerationChanged:
                self._retire_legacy()
                if attempt == 0:
                    continue
                _fail("memory_page_generation_changed")
        _fail("memory_page_generation_changed")
