import asyncio
import base64
import math
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

from astrbot.core.provider.entities import TokenUsage
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .provider_support import (
    OAUTH_IMAGE_MODEL_ALIASES,
    OAuthPlugImageResult,
)


class ProviderOAuthPlugOpenAICodexImage:
    """Image; shared runtime resources belong to the facade."""

    async def generate_image(
        self,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        n: int = 1,
        reference_images: list[str] | None = None,
        action: str | None = None,
        timeout: float | None = None,
        transport: str = "http",
        *,
        image_model: str | None = None,
    ) -> list[OAuthPlugImageResult]:
        """Generate images and persist aggregate OAuth token usage.

        Args:
            prompt: Image generation or editing instruction.
            model: Main Responses controller model, not the image model.
            size: Requested image dimensions.
            n: Number of backend image generations.
            reference_images: Local files, URLs, or data URLs used as references.
            action: Image tool action override.
            timeout: Optional per-image deadline, including the whole HTTP request.
            transport: Image transport, either HTTP or WebSocket.
            image_model: Experimental image tool model. None inherits the source
                setting; an empty string explicitly omits the tool model.

        Returns:
            Extracted image results from all backend generations.

        Raises:
            Exception: Re-raises validation, backend, or extraction failures.
        """
        if transport not in {"http", "websocket"}:
            raise ValueError("图片传输方式必须为 http 或 websocket。")
        explicit_timeout = timeout is not None
        if timeout is None:
            request_timeout = self.timeout
        else:
            try:
                if isinstance(timeout, bool):
                    raise ValueError("timeout 必须是有限正浮点数。")
                request_timeout = float(timeout)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("timeout 必须是有限正浮点数。") from exc
            if not math.isfinite(request_timeout) or request_timeout <= 0:
                raise ValueError("timeout 必须是有限正浮点数。")
        selected_image_model = (
            self.provider_config.get("oauth_image_model", "")
            if image_model is None
            else image_model
        )
        if selected_image_model is None:
            selected_image_model = ""
        if not isinstance(selected_image_model, str):
            raise ValueError("图片模型选择无效。")
        if selected_image_model.strip():
            if selected_image_model not in OAUTH_IMAGE_MODEL_ALIASES:
                raise ValueError("图片模型选择无效。")
        else:
            selected_image_model = ""

        start_time = time.time()
        total_usage = TokenUsage()
        try:
            references = [
                str(image).strip()
                for image in reference_images or []
                if str(image).strip()
            ]
            instructions = str(prompt or "").strip()
            if not instructions:
                raise ValueError("图片生成提示词不能为空。")
            image_input = self._build_image_generation_input(instructions, references)
            image_action = (action or ("edit" if references else "generate")).strip()
            if not image_action:
                image_action = "edit" if references else "generate"
            results: list[OAuthPlugImageResult] = []
            count = max(1, int(n or 1))
            for _ in range(count):
                tool: dict[str, Any] = {
                    "type": "image_generation",
                    "action": image_action,
                }
                if size:
                    tool["size"] = size
                if selected_image_model:
                    tool["model"] = selected_image_model
                payload = {
                    "model": model or self.get_model(),
                    "input": image_input,
                    "instructions": instructions,
                    "tools": [tool],
                    "tool_choice": {"type": "image_generation"},
                    "stream": True,
                    "store": False,
                }
                image_start = time.time()
                response_usage = None
                image_status = "completed"
                response: dict[str, Any] = {}
                try:
                    if transport == "websocket":
                        response = await self._request_image_backend_websocket(
                            payload, timeout=request_timeout
                        )
                    elif explicit_timeout:
                        response = await asyncio.wait_for(
                            self._request_image_backend(payload, request_timeout),
                            timeout=request_timeout,
                        )
                    else:
                        response = await self._request_image_backend(
                            payload, request_timeout
                        )
                    response_usage = self._extract_response_usage(response.get("usage"))
                    if response_usage is not None:
                        total_usage = total_usage + response_usage
                    results.extend(await self._extract_generated_images(response))
                except asyncio.CancelledError:
                    image_status = "aborted"
                    raise
                except Exception:
                    image_status = "error"
                    raise
                finally:
                    if getattr(self, "_usage_recording_enabled", False):
                        await self._record_provider_stat(
                            request_kind="image",
                            status=image_status,
                            usage=response_usage,
                            start_time=image_start,
                            end_time=time.time(),
                            model=response.get("model") or model,
                        )
        except (Exception, asyncio.CancelledError):
            if not getattr(self, "_usage_recording_enabled", False):
                await self._record_provider_stat(
                    request_kind="image",
                    status="error",
                    usage=total_usage,
                    start_time=start_time,
                    end_time=time.time(),
                    model=model,
                )
            raise

        if not getattr(self, "_usage_recording_enabled", False):
            await self._record_provider_stat(
                request_kind="image",
                status="completed",
                usage=total_usage,
                start_time=start_time,
                end_time=time.time(),
                model=model,
            )
        return results

    def _build_image_generation_input(
        self,
        prompt: str,
        reference_images: list[str],
    ) -> list[dict[str, Any]]:
        image_parts = [
            self._reference_image_to_input_part(image)
            for image in reference_images
            if str(image or "").strip()
        ]
        return [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    },
                    *image_parts,
                ],
            }
        ]

    def _reference_image_to_input_part(self, image: str) -> dict[str, str]:
        return {
            "type": "input_image",
            "image_url": self._reference_image_to_image_url(image),
        }

    def _reference_image_to_image_url(self, image: str) -> str:
        value = str(image or "").strip()
        if not value:
            raise ValueError("参考图不能为空。")

        lower = value.lower()
        if lower.startswith("data:image/"):
            return value
        if lower.startswith(("http://", "https://")):
            return value

        path_value = value[7:] if lower.startswith("file://") else value
        path = Path(path_value).expanduser()
        if not path.is_file():
            raise ValueError(f"参考图文件不存在: {value}")

        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        if not mime_type.startswith("image/"):
            mime_type = "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime_type};base64,{encoded}"

    async def _extract_generated_images(
        self,
        response: dict[str, Any],
    ) -> list[OAuthPlugImageResult]:
        output = response.get("output") or []
        if not isinstance(output, list):
            output = []

        image_dir_value = self.provider_config.get("generated_image_dir")
        image_dir = (
            Path(str(image_dir_value))
            if image_dir_value
            else Path(get_astrbot_data_path()) / "generated" / "openai_oauth_images"
        )
        image_dir.mkdir(parents=True, exist_ok=True)

        results: list[OAuthPlugImageResult] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            image_base64 = self._extract_image_base64_from_output_item(item)
            if not image_base64:
                continue
            if "," in image_base64 and image_base64.startswith("data:"):
                image_base64 = image_base64.split(",", 1)[1]
            file_path = image_dir / f"{uuid.uuid4().hex}.png"
            file_path.write_bytes(base64.b64decode(image_base64))
            results.append(
                OAuthPlugImageResult(
                    path=str(file_path),
                    mime_type="image/png",
                    revised_prompt=str(item.get("revised_prompt") or ""),
                    raw=item,
                )
            )

        if not results:
            raise Exception(f"Codex 图像生成响应未包含可提取图片：{response}")
        return results

    def _extract_image_base64_from_output_item(self, item: dict[str, Any]) -> str:
        if item.get("type") == "image_generation_call":
            value = item.get("result")
            if value:
                return str(value)

        content = item.get("content")
        if not isinstance(content, list):
            return ""
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") not in {"output_image", "image"}:
                continue
            value = (
                part.get("image_base64")
                or part.get("b64_json")
                or part.get("data")
                or ""
            )
            if value:
                return str(value)
        return ""
