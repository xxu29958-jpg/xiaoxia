import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

from .openai_oauth_audio import OpenAIOAuthAudioMixin
from .openai_oauth_shared_state import OpenAIOAuthSharedState
from .provider_credentials import ProviderOAuthPlugOpenAICodexCredentials
from .provider_image import ProviderOAuthPlugOpenAICodexImage
from .provider_image_transport import ProviderOAuthPlugOpenAICodexImageTransport
from .provider_input import ProviderOAuthPlugOpenAICodexInput
from .provider_legacy_response import ProviderOAuthPlugOpenAICodexLegacyResponse
from .provider_request import ProviderOAuthPlugOpenAICodexRequest
from .provider_response import ProviderOAuthPlugOpenAICodexResponse
from .provider_runtime_compat import (
    provider_stats_managed_by_agent,
)
from .provider_stream import ProviderOAuthPlugOpenAICodexStream
from .provider_support import IMAGE_WEBSOCKET_MAX_EVENTS as IMAGE_WEBSOCKET_MAX_EVENTS
from .provider_support import (
    IMAGE_WEBSOCKET_MAX_MESSAGE_BYTES as IMAGE_WEBSOCKET_MAX_MESSAGE_BYTES,
)
from .provider_support import (
    IMAGE_WEBSOCKET_MAX_TRANSCRIPT_BYTES as IMAGE_WEBSOCKET_MAX_TRANSCRIPT_BYTES,
)
from .provider_support import OAUTH_IMAGE_MODEL_ALIASES as OAUTH_IMAGE_MODEL_ALIASES
from .provider_support import OAuthPlugImageResult as OAuthPlugImageResult
from .provider_support import OpenAIOAuthImageStreamError as OpenAIOAuthImageStreamError
from .provider_support import oauth_provider_stat_kind as oauth_provider_stat_kind
from .provider_tools import ProviderOAuthPlugOpenAICodexTools
from .provider_transport import ProviderOAuthPlugOpenAICodexTransport
from .provider_usage import ProviderOAuthPlugOpenAICodexUsage
from .service import OAUTH_PLACEHOLDER_KEY, get_service


class ProviderOAuthPlugOpenAICodex(
    ProviderOAuthPlugOpenAICodexCredentials,
    ProviderOAuthPlugOpenAICodexTransport,
    ProviderOAuthPlugOpenAICodexImageTransport,
    ProviderOAuthPlugOpenAICodexLegacyResponse,
    ProviderOAuthPlugOpenAICodexInput,
    ProviderOAuthPlugOpenAICodexUsage,
    ProviderOAuthPlugOpenAICodexTools,
    ProviderOAuthPlugOpenAICodexRequest,
    ProviderOAuthPlugOpenAICodexResponse,
    ProviderOAuthPlugOpenAICodexStream,
    ProviderOAuthPlugOpenAICodexImage,
    OpenAIOAuthAudioMixin,
    ProviderOpenAIOfficial,
):
    capabilities = {
        "chat": True,
        "stream": True,
        "vision_input": True,
        "function_call": True,
        "reasoning": True,
        "image_generate": True,
        "image_edit": True,
        "image_websocket": True,
    }

    model_capabilities = {
        "gpt-6.1-sol": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        },
        "gpt-6-astra": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        },
        "gpt-6-sol": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        },
        "gpt-6-luna": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        },
        "gpt-5.6-sol": {
            "default_reasoning_effort": "low",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        },
        "gpt-5.6-terra": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        },
        "gpt-5.6-luna": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ),
        },
        "gpt-5.5": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
            ),
        },
        "gpt-5.4": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
            ),
        },
        "gpt-5.4-mini": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
            ),
        },
        "gpt-5.3-codex-spark": {
            "default_reasoning_effort": "high",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
            ),
        },
        "gpt-5.3-codex": {
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": (
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
            ),
        },
    }

    def __init__(self, provider_config, provider_settings) -> None:
        service = get_service()
        self._oauth_service = service
        provider_config = (
            service.build_provider_config(provider_config)
            if service is not None
            else dict(provider_config)
        )
        patched_config = dict(provider_config)
        patched_config.pop("oauth_shared_state", None)
        patched_config["key"] = [OAUTH_PLACEHOLDER_KEY]
        super().__init__(patched_config, provider_settings)
        self.provider_config = dict(provider_config)
        shared_state = self.provider_config.pop("oauth_shared_state", None)
        if isinstance(shared_state, OpenAIOAuthSharedState):
            self._oauth_shared_state = shared_state
        else:
            source_id = str(
                self.provider_config.get("provider_source_id")
                or self.provider_config.get("id")
                or "openai_oauth"
            )
            self._oauth_shared_state = OpenAIOAuthSharedState(
                source_id,
                self.provider_config,
            )
        self.provider_config["key"] = [OAUTH_PLACEHOLDER_KEY]
        self.api_keys = [OAUTH_PLACEHOLDER_KEY]
        self.chosen_api_key = ""
        self.account_id = (
            self.provider_config.get("oauth_account_id")
            or self.provider_config.get("account_id")
            or ""
        ).strip()
        self.base_url = (
            self.provider_config.get("api_base")
            or "https://chatgpt.com/backend-api/codex"
        ).rstrip("/")
        self._oauth_refresh_lock = self._oauth_shared_state.refresh_lock
        self._oauth_refresh_skew_seconds = int(
            self.provider_config.get("oauth_refresh_skew_seconds") or 300
        )
        self._sync_oauth_credentials_from_shared()
        if service is not None:
            service.register_provider(self)

    async def get_models(self):
        service = getattr(self, "_oauth_service", None) or get_service()
        if service is not None:
            return service.get_models()
        configured_model = str(self.provider_config.get("model") or "").strip()
        return [configured_model] if configured_model else list(self.model_capabilities)

    async def _prepare_chat_payload(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Preserve per-request reasoning controls for Codex Responses.

        Args:
            *args: Positional arguments forwarded to the OpenAI payload builder.
            **kwargs: Keyword arguments forwarded to the OpenAI payload builder.

        Returns:
            The prepared request payload and normalized message context.
        """
        payloads, context_query = await super()._prepare_chat_payload(
            *args,
            **kwargs,
        )
        for key in ("reasoning_effort", "reasoning", "response_format"):
            if kwargs.get(key) is not None:
                payloads[key] = kwargs[key]
        if kwargs.get("oauth_web_search") is not None:
            payloads["_oauth_web_search"] = kwargs["oauth_web_search"]
        if kwargs.get("retry_rate_limits") is not None:
            payloads["_oauth_retry_rate_limits"] = kwargs["retry_rate_limits"]
        return payloads, context_query

    async def text_chat(
        self,
        prompt=None,
        session_id=None,
        image_urls=None,
        audio_urls=None,
        func_tool=None,
        contexts=None,
        system_prompt=None,
        tool_calls_result=None,
        model=None,
        extra_user_content_parts=None,
        tool_choice="auto",
        request_max_retries=None,
        retry_rate_limits=None,
        oauth_web_search=None,
        **kwargs,
    ) -> LLMResponse:
        """Run an OAuth chat request and account for direct provider usage.

        Args:
            prompt: User prompt for a new conversation turn.
            session_id: Session identifier used by provider statistics.
            image_urls: Image inputs attached to the user message.
            audio_urls: Audio inputs attached to the user message.
            func_tool: Tools available to the model.
            contexts: Existing conversation messages.
            system_prompt: System instruction for the request.
            tool_calls_result: Results returned from earlier tool calls.
            model: Explicit model override.
            extra_user_content_parts: Additional user message content parts.
            tool_choice: Tool selection policy.
            request_max_retries: Maximum attempts for retryable backend requests.
            retry_rate_limits: Whether HTTP 429 responses may be retried.
            oauth_web_search: Request-level managed web search mode.
            **kwargs: Additional provider request options.

        Returns:
            Parsed model response from the inherited OpenAI provider.

        Raises:
            Exception: Re-raises the original provider exception unchanged.
        """
        managed_by_agent = provider_stats_managed_by_agent.get()
        request_kind = oauth_provider_stat_kind.get()
        start_time = time.time()
        kwargs["_oauth_tool_choice"] = tool_choice
        try:
            response = await super().text_chat(
                prompt=prompt,
                session_id=session_id,
                image_urls=image_urls,
                audio_urls=audio_urls,
                func_tool=func_tool,
                contexts=contexts,
                system_prompt=system_prompt,
                tool_calls_result=tool_calls_result,
                model=model,
                extra_user_content_parts=extra_user_content_parts,
                tool_choice=tool_choice,
                request_max_retries=request_max_retries,
                retry_rate_limits=retry_rate_limits,
                oauth_web_search=oauth_web_search,
                **kwargs,
            )
        except asyncio.CancelledError:
            if not managed_by_agent:
                await self._record_provider_stat(
                    request_kind=request_kind,
                    status="error",
                    usage=None,
                    start_time=start_time,
                    end_time=time.time(),
                    model=model,
                    session_id=session_id,
                )
            raise
        except Exception as exc:
            if not managed_by_agent:
                await self._record_provider_stat(
                    request_kind=request_kind,
                    status="error",
                    usage=getattr(exc, "_astrbot_token_usage", None),
                    start_time=start_time,
                    end_time=time.time(),
                    model=model,
                    session_id=session_id,
                )
            raise

        if not managed_by_agent:
            await self._record_provider_stat(
                request_kind=request_kind,
                status="error" if response.role == "err" else "completed",
                usage=response.usage,
                start_time=start_time,
                end_time=time.time(),
                model=model,
                session_id=session_id,
            )
        return response

    async def test(self, timeout: float = 45.0) -> None:
        token = oauth_provider_stat_kind.set("test")
        try:
            await super().test(timeout)
        finally:
            oauth_provider_stat_kind.reset(token)

    async def text_chat_stream(
        self,
        prompt=None,
        session_id=None,
        image_urls=None,
        audio_urls=None,
        func_tool=None,
        contexts=None,
        system_prompt=None,
        tool_calls_result=None,
        model=None,
        tool_choice="auto",
        request_max_retries=None,
        retry_rate_limits=None,
        oauth_web_search=None,
        **kwargs,
    ) -> AsyncGenerator[LLMResponse, None]:
        extra_user_content_parts = kwargs.pop("extra_user_content_parts", None)
        conversation_id = kwargs.pop("conversation_id", None)
        payloads, _context_query = await self._prepare_chat_payload(
            prompt,
            image_urls,
            audio_urls,
            contexts,
            system_prompt,
            tool_calls_result,
            model=model,
            extra_user_content_parts=extra_user_content_parts,
            **kwargs,
        )
        if (func_tool and not func_tool.empty()) or tool_choice != "auto":
            payloads["tool_choice"] = tool_choice

        managed_by_agent = provider_stats_managed_by_agent.get()
        request_kind = oauth_provider_stat_kind.get()
        start_time = time.time()
        final_response: LLMResponse | None = None
        try:
            async with aclosing(
                self._query_stream(
                    payloads,
                    func_tool,
                    request_max_retries=request_max_retries,
                    conversation_id=conversation_id,
                    retry_rate_limits=retry_rate_limits,
                    oauth_web_search=oauth_web_search,
                )
            ) as response_stream:
                async for response in response_stream:
                    if not response.is_chunk:
                        final_response = response
                    yield response
        except (asyncio.CancelledError, GeneratorExit):
            if not managed_by_agent:
                await self._record_provider_stat(
                    request_kind=request_kind,
                    status="error",
                    usage=None,
                    start_time=start_time,
                    end_time=time.time(),
                    model=model,
                    session_id=session_id,
                )
            raise
        except Exception as exc:
            if not managed_by_agent:
                await self._record_provider_stat(
                    request_kind=request_kind,
                    status="error",
                    usage=getattr(exc, "_astrbot_token_usage", None),
                    start_time=start_time,
                    end_time=time.time(),
                    model=model,
                    session_id=session_id,
                )
            raise

        if final_response is None:
            raise Exception("Codex backend stream did not produce a final response")
        if not managed_by_agent:
            await self._record_provider_stat(
                request_kind=request_kind,
                status="error" if final_response.role == "err" else "completed",
                usage=final_response.usage,
                start_time=start_time,
                end_time=time.time(),
                model=model,
                session_id=session_id,
            )
