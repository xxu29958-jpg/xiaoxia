from __future__ import annotations

import hashlib
import io
import json
import math
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from .auto_collect_support import (
    AUTO_COLLECT_PROMPT_VERSION,
    AUTO_COLLECT_SYSTEM_PROMPT,
    FORMAT_EXTENSIONS,
    MAX_DECISION_CACHE_ITEMS,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
)
from .semantic.caption import (
    _structured_output_is_unsupported,
    prepare_visual_inputs,
)
from .semantic.models import PROMPT_VERSION


class AutoCollectManagerClassification:
    """Classification; shared runtime resources belong to the facade."""

    @staticmethod
    def _validate_image(content: bytes) -> str:
        """校验图片的实际内容，并返回可信的扩展名。

        Args:
            content: 原始图片字节。

        Returns:
            可信的小写文件扩展名。

        Raises:
            ValueError: 图片为空、过大、格式不受支持或内容不安全。
        """
        if not content:
            raise ValueError("图片内容为空")
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError("图片超过 20 MiB 限制")
        try:
            with PILImage.open(io.BytesIO(content)) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片尺寸无效或过大")
                image.verify()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"图片格式不受支持或文件已损坏：{exc}") from exc
        extension = FORMAT_EXTENSIONS.get(image_format)
        if not extension:
            raise ValueError(f"不支持的图片格式：{image_format or '未知'}")
        return extension

    def _daily_call_available(self) -> bool:
        """在配额可用时占用一次当天的识图次数。

        Returns:
            视觉识别请求可以继续时返回 True。
        """
        today = datetime.now(timezone.utc).date().isoformat()
        if str(self._state.get("recognition_date") or "") != today:
            self._state["recognition_date"] = today
            self._state["recognition_count"] = 0
        count = max(0, int(self._state.get("recognition_count", 0) or 0))
        if self.daily_recognition_limit and count >= self.daily_recognition_limit:
            return False
        self._state["recognition_count"] = count + 1
        self._save_json(self.paths.state, self._state)
        return True

    def _cache_key(
        self, digest: str, target_pack_id: str, categories: dict[str, str]
    ) -> str:
        """生成会随目标分类变化的缓存键。

        Args:
            digest: 图片的 SHA-256 摘要。
            target_pack_id: 分类目标表情包 ID。
            categories: 当前分类描述。

        Returns:
            稳定的识别结果缓存键。
        """
        catalog = json.dumps(categories, ensure_ascii=False, sort_keys=True)
        catalog_hash = hashlib.sha256(catalog.encode("utf-8")).hexdigest()[:16]
        provider = (
            self.plugin.context.get_provider_by_id(self.vision_provider_id)
            if hasattr(self.plugin, "context")
            else None
        )
        provider_config = getattr(provider, "provider_config", {})
        signature = hashlib.sha256(
            json.dumps(provider_config, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        return f"{AUTO_COLLECT_PROMPT_VERSION}:{PROMPT_VERSION}:{self.vision_provider_id}:{signature}:{target_pack_id}:{catalog_hash}:{digest}"

    def _remember_decision(self, key: str, decision: dict[str, Any]) -> None:
        """保存一次成功的分类结果，并限制历史记录数量。

        Args:
            key: 识别结果缓存键。
            decision: 已规范化的分类结果。
        """
        decisions = self._state.setdefault("decisions", {})
        if not isinstance(decisions, dict):
            decisions = {}
            self._state["decisions"] = decisions
        decisions[key] = {
            **decision,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        while len(decisions) > MAX_DECISION_CACHE_ITEMS:
            decisions.pop(next(iter(decisions)))
        self._save_json(self.paths.state, self._state)

    async def _classify(
        self,
        content: bytes,
        extension: str,
        categories: dict[str, str],
    ) -> dict[str, Any]:
        """调用已配置的视觉模型，并规范化其 JSON 结果。

        Args:
            content: 已通过校验的图片字节。
            extension: 可信的图片扩展名。
            categories: 允许使用的分类及其描述。

        Returns:
            已规范化的分类结果。
        """
        self.paths.temporary.mkdir(parents=True, exist_ok=True)
        source_file = tempfile.NamedTemporaryFile(
            prefix="incoming_",
            suffix=extension,
            dir=self.paths.temporary,
            delete=False,
        )
        try:
            source_file.write(content)
            source_file.close()
            visual_paths, frame_paths = prepare_visual_inputs(source_file.name)
            prompt = (
                "可选分类及用途如下：\n"
                + json.dumps(categories, ensure_ascii=False, indent=2)
                + "\n请返回："
                + '{"is_meme":true,"meme_confidence":0.0,"category":"",'
                + '"category_confidence":0.0,"reason":"",'
                + '"caption":"","tags":[],"visible_text":""}'
            )
            request_data = {
                "chat_provider_id": self.vision_provider_id,
                "prompt": prompt,
                "image_urls": visual_paths,
                "system_prompt": AUTO_COLLECT_SYSTEM_PROMPT,
                "temperature": 0,
                "max_tokens": 1400,
                "response_format": {"type": "json_object"},
            }
            try:
                response = await self.plugin.context.llm_generate(**request_data)
            except Exception as exc:
                if not _structured_output_is_unsupported(exc):
                    raise
                request_data.pop("response_format", None)
                response = await self.plugin.context.llm_generate(**request_data)
            finally:
                for frame_path in frame_paths:
                    Path(frame_path).unlink(missing_ok=True)
        finally:
            source_file.close()
            Path(source_file.name).unlink(missing_ok=True)

        raw_text = str(getattr(response, "completion_text", "") or "").strip()
        try:
            payload = json.loads(raw_text)
        except (TypeError, ValueError):
            match = re.search(r"\{[\s\S]*\}", raw_text)
            if not match:
                raise ValueError("视觉模型未返回 JSON 对象")
            payload = json.loads(match.group(0))
        if not isinstance(payload, dict):
            raise ValueError("视觉模型结果必须是 JSON 对象")

        raw_is_meme = payload.get("is_meme", False)
        is_meme = (
            raw_is_meme
            if isinstance(raw_is_meme, bool)
            else str(raw_is_meme).strip().lower() in {"true", "1", "yes"}
        )
        confidences = {}
        for key in ("meme_confidence", "category_confidence"):
            try:
                value = float(payload.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            confidences[key] = (
                max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0
            )
        meme_confidence = confidences["meme_confidence"]
        category_confidence = confidences["category_confidence"]
        requested_category = str(payload.get("category") or "").strip()
        folded_categories = {name.casefold(): name for name in categories}
        category = (
            requested_category
            if requested_category in categories
            else folded_categories.get(requested_category.casefold(), "")
        )
        tags = payload.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        return {
            "semantic_prompt_version": PROMPT_VERSION,
            "semantic_category": category,
            "vision_model": self.vision_provider_id,
            "is_meme": bool(is_meme),
            "meme_confidence": meme_confidence,
            "category": category,
            "category_confidence": category_confidence,
            "reason": str(payload.get("reason") or "").strip()[:500],
            "caption": str(payload.get("caption") or "").strip()[:1000],
            "tags": [str(tag).strip()[:100] for tag in tags[:12] if str(tag).strip()],
            "visible_text": str(payload.get("visible_text") or "").strip()[:1000],
        }
