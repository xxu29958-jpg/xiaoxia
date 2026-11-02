from __future__ import annotations

import asyncio
import copy
import inspect
from typing import Any

from .companion_page_bridge_support import (
    _PHOTO_REF_RE,
    MEMORY_PLUGIN_ID,
    CompanionPageBridgeError,
    CompanionPagePhoto,
    _fail,
    _GenerationChanged,
    _ResolvedAPI,
    _text,
    validate_memory_page_descriptor,
    validate_memory_page_photo,
)


class CompanionPageBridgePhotos:
    """Photos; shared runtime resources belong to the facade."""

    async def _formal_photo(
        self,
        resolved: _ResolvedAPI,
        photo_ref: str,
    ) -> CompanionPagePhoto:
        try:
            capabilities, _exporter, reader = self._formal_methods(resolved.api)
        except CompanionPageBridgeError:
            try:
                self._same_api(resolved)
            except _GenerationChanged:
                _fail("memory_page_photo_ref_stale")
            raise
        try:
            descriptor = validate_memory_page_descriptor(capabilities())
        except CompanionPageBridgeError:
            try:
                self._same_api(resolved)
            except _GenerationChanged:
                _fail("memory_page_photo_ref_stale")
            raise
        except Exception:
            try:
                self._same_api(resolved)
            except _GenerationChanged:
                _fail("memory_page_photo_ref_stale")
            _fail("memory_page_contract_malformed")
        try:
            self._same_api(resolved)
        except _GenerationChanged:
            _fail("memory_page_photo_ref_stale")
        generation = descriptor["instance_generation"]
        match = _PHOTO_REF_RE.fullmatch(photo_ref)
        if match is None:
            _fail("memory_page_photo_ref_invalid")
        if match.group(1) != generation[:12]:
            _fail("memory_page_photo_ref_stale")
        if descriptor["lifecycle_state"] != "ready":
            _fail("memory_page_service_closed")
        try:
            pending = reader(
                target_plugin_id=MEMORY_PLUGIN_ID,
                photo_ref=photo_ref,
            )
            if not inspect.isawaitable(pending):
                _fail("memory_page_contract_malformed")
            raw = await pending
        except asyncio.CancelledError:
            raise
        except CompanionPageBridgeError:
            try:
                self._same_formal_generation(
                    resolved,
                    capabilities,
                    generation,
                )
            except _GenerationChanged:
                _fail("memory_page_photo_ref_stale")
            raise
        except Exception as exc:
            try:
                self._same_formal_generation(
                    resolved,
                    capabilities,
                    generation,
                )
            except _GenerationChanged:
                _fail("memory_page_photo_ref_stale")
            raise self._producer_error(exc, "memory_page_photo_read_failed") from None
        try:
            self._same_formal_generation(
                resolved,
                capabilities,
                generation,
            )
        except _GenerationChanged:
            _fail("memory_page_photo_ref_stale")
        return validate_memory_page_photo(
            raw,
            expected_generation=generation,
            expected_photo_ref=photo_ref,
        )

    async def read_photo(
        self,
        photo_ref: str,
        *,
        expected_mode: str | None = None,
    ) -> CompanionPagePhoto:
        reference = _text(
            photo_ref,
            48,
            code="memory_page_photo_ref_invalid",
            required=True,
        )
        if _PHOTO_REF_RE.fullmatch(reference) is None:
            _fail("memory_page_photo_ref_invalid")
        if expected_mode not in {None, "formal", "legacy"}:
            _fail("memory_page_photo_ref_invalid")
        resolved = self._resolve_api()
        surface = self._select_surface(resolved)
        if expected_mode is not None and surface != expected_mode:
            _fail("memory_page_photo_ref_stale")
        if surface == "formal":
            return await self._formal_photo(resolved, reference)
        adapter = self._legacy_for(resolved.api, create=False)
        if not reference.startswith(f"mphoto_{adapter.instance_generation[:12]}_"):
            _fail("memory_page_photo_ref_stale")
        try:
            raw = await adapter.read_photo(reference)
        except asyncio.CancelledError:
            raise
        except CompanionPageBridgeError:
            try:
                self._same_legacy_generation(resolved, adapter)
            except (CompanionPageBridgeError, _GenerationChanged):
                self._retire_legacy()
                _fail("memory_page_photo_ref_stale")
            raise
        except Exception:
            try:
                self._same_api(resolved)
            except _GenerationChanged:
                self._retire_legacy()
                _fail("memory_page_photo_ref_stale")
            _fail("memory_page_photo_read_failed")
        try:
            self._same_legacy_generation(resolved, adapter)
            if self._select_surface(resolved) != "legacy":
                _fail("memory_page_photo_ref_stale")
        except _GenerationChanged:
            self._retire_legacy()
            _fail("memory_page_photo_ref_stale")
        except CompanionPageBridgeError as exc:
            if exc.code in {
                "memory_page_contract_downgrade",
                "memory_page_service_closed",
            }:
                self._retire_legacy()
                _fail("memory_page_photo_ref_stale")
            raise
        try:
            photo = validate_memory_page_photo(
                raw,
                expected_generation=adapter.instance_generation,
                expected_photo_ref=reference,
            )
            self._same_legacy_generation(resolved, adapter)
        except _GenerationChanged:
            self._retire_legacy()
            _fail("memory_page_photo_ref_stale")
        except CompanionPageBridgeError as exc:
            if exc.code == "memory_page_service_closed":
                self._retire_legacy()
                _fail("memory_page_photo_ref_stale")
            raise
        return CompanionPagePhoto(
            photo_ref=photo.photo_ref,
            mime_type=photo.mime_type,
            size=photo.size,
            sha256=photo.sha256,
            content_base64=photo.content_base64,
            content=photo.content,
            mode="legacy",
        )

    def read_p6_status(self) -> Any:
        resolved = self._resolve_api()
        try:
            self._select_surface(resolved)
        except CompanionPageBridgeError as exc:
            if exc.code == "memory_page_contract_downgrade":
                _fail("companion_p6_producer_stale")
            raise
        try:
            getter = getattr(resolved.api, "get_p6_readonly_status")
        except Exception:
            _fail("companion_p6_producer_unavailable")
        if not callable(getter):
            _fail("companion_p6_producer_unavailable")
        try:
            value = getter()
        except Exception:
            _fail("companion_p6_producer_unreadable")
        try:
            self._same_api(resolved)
        except _GenerationChanged:
            _fail("companion_p6_producer_stale")
        try:
            return copy.deepcopy(value)
        except Exception:
            _fail("companion_p6_producer_unreadable")
