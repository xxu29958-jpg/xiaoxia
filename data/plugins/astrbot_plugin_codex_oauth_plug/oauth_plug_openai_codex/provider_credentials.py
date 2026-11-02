from datetime import datetime, timedelta, timezone
from typing import Any

from .oauth import refresh_access_token
from .openai_oauth_shared_state import OpenAIOAuthSharedState
from .service import OAUTH_PLACEHOLDER_KEY


class ProviderOAuthPlugOpenAICodexCredentials:
    """Credentials; shared runtime resources belong to the facade."""

    def _parse_oauth_expires_at(self) -> datetime | None:
        value = (self.provider_config.get("oauth_expires_at") or "").strip()
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

    def _oauth_expiring_soon(self) -> bool:
        self._sync_oauth_credentials_from_shared()
        expires_at = self._parse_oauth_expires_at()
        if expires_at is None:
            return False
        refresh_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._oauth_refresh_skew_seconds
        )
        return expires_at <= refresh_at

    def _sync_oauth_credentials_from_shared(self) -> None:
        if not hasattr(self, "_oauth_shared_state"):
            source_id = str(
                self.provider_config.get("provider_source_id")
                or self.provider_config.get("id")
                or "oauth_plug_openai_codex"
            )
            self._oauth_shared_state = OpenAIOAuthSharedState(
                source_id,
                self.provider_config,
            )
            self._oauth_refresh_lock = self._oauth_shared_state.refresh_lock
        self.provider_config.update(self._oauth_shared_state.snapshot())
        self.account_id = str(
            self.provider_config.get("oauth_account_id")
            or self.provider_config.get("account_id")
            or ""
        ).strip()
        self.api_keys = [OAUTH_PLACEHOLDER_KEY]
        self.chosen_api_key = ""
        self.client.api_key = OAUTH_PLACEHOLDER_KEY

    def _apply_oauth_token_to_runtime(self, token: dict[str, Any]) -> None:
        access_token = str(token.get("access_token") or "").strip()
        refresh_token = str(token.get("refresh_token") or "").strip()
        patch: dict[str, Any] = {}
        if access_token:
            patch["oauth_access_token"] = access_token
        if refresh_token:
            patch["oauth_refresh_token"] = refresh_token
        patch["oauth_expires_at"] = str(token.get("expires_at") or "")
        patch["oauth_account_email"] = str(
            token.get("email") or ""
        ) or self.provider_config.get("oauth_account_email", "")
        patch["oauth_account_id"] = str(
            token.get("account_id") or ""
        ) or self.provider_config.get("oauth_account_id", "")
        self._oauth_shared_state.apply(patch)
        self._sync_oauth_credentials_from_shared()

    async def _refresh_oauth_token(self) -> bool:
        self._sync_oauth_credentials_from_shared()
        refresh_version, credentials = self._oauth_shared_state.versioned_snapshot()
        refresh_token_value = str(credentials.get("oauth_refresh_token") or "").strip()
        if not refresh_token_value:
            return False

        service = getattr(self, "_oauth_service", None)
        if service is not None:
            token = await service.refresh(refresh_version)
        else:
            token = await refresh_access_token(
                refresh_token_value,
                self.provider_config.get("proxy", ""),
            )
        if self._oauth_shared_state.version != refresh_version:
            self._sync_oauth_credentials_from_shared()
            return True
        self._apply_oauth_token_to_runtime(token)

        if service is not None:
            await service.persist_token(token)
        return True

    async def _ensure_fresh_oauth_token(self) -> None:
        self._sync_oauth_credentials_from_shared()
        if not self._oauth_expiring_soon():
            return
        if getattr(self, "_oauth_service", None) is not None:
            await self._refresh_oauth_token()
            return
        async with self._oauth_refresh_lock:
            self._sync_oauth_credentials_from_shared()
            if not self._oauth_expiring_soon():
                return
            await self._refresh_oauth_token()

    async def _refresh_after_auth_failure(self, attempted_version: int) -> bool:
        if getattr(self, "_oauth_service", None) is not None:
            self._sync_oauth_credentials_from_shared()
            if self._oauth_shared_state.version != attempted_version:
                return True
            return await self._refresh_oauth_token()
        async with self._oauth_refresh_lock:
            self._sync_oauth_credentials_from_shared()
            if self._oauth_shared_state.version != attempted_version:
                return True
            return await self._refresh_oauth_token()
