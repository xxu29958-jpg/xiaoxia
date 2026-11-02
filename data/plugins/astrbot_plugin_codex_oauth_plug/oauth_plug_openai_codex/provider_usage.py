from typing import Any

from astrbot import logger
from astrbot.core.provider.entities import TokenUsage

from .provider_runtime_compat import (
    db_helper,
    record_provider_usage,
)


class ProviderOAuthPlugOpenAICodexUsage:
    """Usage; shared runtime resources belong to the facade."""

    def _extract_response_usage(self, usage: Any) -> TokenUsage | None:
        if usage is None:
            return None
        getter = (
            usage.get
            if isinstance(usage, dict)
            else lambda key: getattr(usage, key, None)
        )
        known_input = getter("input_tokens") is not None
        known_output = getter("output_tokens") is not None
        if not known_input and not known_output:
            return None
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            details = usage.get("input_tokens_details") or {}
            cached_tokens = int(details.get("cached_tokens", 0) or 0)
        else:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            details = getattr(usage, "input_tokens_details", None)
            cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)
        values = {
            "input_other": max(0, input_tokens - cached_tokens),
            "input_cached": cached_tokens,
            "output": output_tokens,
        }
        try:
            return TokenUsage(
                **values,
                is_partial=not (known_input and known_output),
            )
        except TypeError:
            return TokenUsage(**values)

    async def _record_provider_stat(
        self,
        *,
        request_kind: str,
        status: str,
        usage: TokenUsage | None,
        start_time: float,
        end_time: float,
        model: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Persist one OAuth provider call without affecting its caller.

        Args:
            request_kind: Logical call type used by the synthetic UMO.
            status: Provider call status stored in the database.
            usage: Parsed token usage, or None when the backend omitted it.
            start_time: Epoch time immediately before the public call.
            end_time: Epoch time immediately after the public call.
            model: Explicit request model when supplied.
            session_id: Session identifier when supplied by the caller.
        """
        if getattr(self, "_usage_recording_enabled", False):
            recorded = await record_provider_usage(
                self,
                self._usage_recording_db,
                usage=usage,
                status=status,
                start_time=start_time,
                end_time=end_time,
                request_kind=request_kind,
                model=model,
            )
            if recorded:
                return
        try:
            if db_helper is None:
                return
            provider_id = str(self.provider_config.get("id") or "").strip()
            if not provider_id:
                try:
                    provider_id = str(self.meta().id).strip()
                except Exception:
                    return
            if not provider_id:
                return
            await db_helper.insert_provider_stat(
                umo=session_id or f"provider:{provider_id}:{request_kind}",
                provider_id=provider_id,
                provider_model=model or self.get_model(),
                status=status,
                stats={
                    "token_usage": {
                        "input_other": usage.input_other if usage else 0,
                        "input_cached": usage.input_cached if usage else 0,
                        "output": usage.output if usage else 0,
                    },
                    "start_time": start_time,
                    "end_time": end_time,
                    "time_to_first_token": 0.0,
                },
                agent_type="test" if request_kind == "test" else "provider",
            )
        except Exception as exc:
            logger.warning(
                "Failed to record OAuth_plug provider statistics (%s).",
                type(exc).__name__,
            )
