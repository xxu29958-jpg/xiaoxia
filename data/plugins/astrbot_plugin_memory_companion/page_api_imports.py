from __future__ import annotations

import asyncio
import base64

from quart import request

from .core.models import clean_text
from .page_api_support import (
    QQ_HISTORY_CAPABILITY_FIELDS,
    logger,
)


class PluginPageApiImports:
    """Imports; shared runtime resources belong to the facade."""

    async def conversation_import_upload(self):
        payload = await self._json()
        filename = clean_text(payload.get("filename"), 240) or "conversation.txt"
        encoded = str(payload.get("content_base64") or "").strip()
        if not encoded:
            return self._err("content_base64 is required", 400)
        if "," in encoded and encoded.lower().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            content = base64.b64decode(encoded, validate=True)
            result = await asyncio.to_thread(
                self.plugin.service.preview_historical_chat_upload,
                filename=filename,
                content=content,
                base_year=int(payload.get("base_year") or 0),
            )
            return self._ok({"data": result})
        except (ValueError, OSError) as exc:
            return self._err(str(exc), 400)
        except Exception as exc:
            logger.exception("历史对话预览失败")
            return self._err(f"历史对话预览失败: {exc}", 500)

    async def conversation_import_qq_capabilities(self):
        try:
            result = await self.plugin.service.qq_history_capabilities()
            return self._ok(
                {"data": self._declared_payload(result, QQ_HISTORY_CAPABILITY_FIELDS)}
            )
        except Exception as exc:
            logger.exception("QQ 历史读取能力检测失败")
            return self._err(f"QQ 历史读取能力检测失败: {exc}", 500)

    async def conversation_import_qq_preview(self):
        payload = await self._json()
        try:
            result = await self.plugin.service.preview_qq_historical_chat(payload)
            return self._ok({"data": result})
        except ValueError as exc:
            return self._err(str(exc), 400)
        except (RuntimeError, asyncio.TimeoutError) as exc:
            return self._err(str(exc), 502)
        except Exception as exc:
            logger.exception("QQ 历史读取预览失败")
            return self._err(f"QQ 历史读取预览失败: {exc}", 500)

    async def conversation_import_start(self):
        payload = await self._json()
        try:
            result = await self.plugin.service.start_historical_chat_import(payload)
            return self._ok({"data": result})
        except (ValueError, OSError) as exc:
            return self._err(str(exc), 400)
        except Exception as exc:
            logger.exception("历史对话导入启动失败")
            return self._err(f"历史对话导入启动失败: {exc}", 500)

    async def conversation_import_status(self):
        batch_id = clean_text(request.args.get("batch_id", ""), 120)
        upgrade_legacy = self._bool(request.args.get("upgrade_legacy"), True)
        try:
            result = await self.plugin.service.historical_chat_import_status(
                batch_id,
                upgrade_legacy=upgrade_legacy,
            )
            return self._ok({"data": result})
        except ValueError as exc:
            return self._err(str(exc), 404)
        except Exception as exc:
            return self._err(f"读取历史对话导入状态失败: {exc}", 500)

    async def conversation_import_targets(self):
        try:
            # 导入目标只接受真实私聊窗口：bot_personal 归档行属于内部合成会话
            # （session_id=bot_personal:<digest>），因此保持默认的排除语义。
            buckets = await self.plugin.service.store.list_memory_buckets(
                limit=None,
                include_raw_events=self.plugin.service.config.bool(
                    "memory_injection.include_raw_events",
                    False,
                ),
            )
            private_buckets = []
            for bucket in buckets:
                if clean_text(bucket.get("scope"), 40) != "private":
                    continue
                item = dict(bucket)
                item.pop("pending_count", None)
                private_buckets.append(item)
            return self._ok({"buckets": private_buckets})
        except Exception as exc:
            return self._err(f"读取历史导入目标私聊失败: {exc}", 500)

    async def conversation_import_pause(self):
        payload = await self._json()
        batch_id = clean_text(payload.get("batch_id"), 120)
        if not batch_id:
            return self._err("batch_id is required", 400)
        try:
            return self._ok(
                {
                    "data": await self.plugin.service.pause_historical_chat_import(
                        batch_id
                    )
                }
            )
        except ValueError as exc:
            return self._err(str(exc), 404)

    async def conversation_import_resume(self):
        payload = await self._json()
        batch_id = clean_text(payload.get("batch_id"), 120)
        if not batch_id:
            return self._err("batch_id is required", 400)
        try:
            return self._ok(
                {
                    "data": await self.plugin.service.resume_historical_chat_import(
                        batch_id
                    )
                }
            )
        except ValueError as exc:
            return self._err(str(exc), 404)

    async def conversation_import_rebind(self):
        payload = await self._json()
        try:
            result = await self.plugin.service.rebind_historical_chat_import(payload)
            return self._ok({"data": result})
        except ValueError as exc:
            message = str(exc)
            return self._err(message, 404 if message == "导入批次不存在" else 400)
        except Exception as exc:
            logger.exception("历史对话导入归属修正失败")
            return self._err(f"历史对话导入归属修正失败: {exc}", 500)

    async def conversation_import_rollback(self):
        payload = await self._json()
        batch_id = clean_text(payload.get("batch_id"), 120)
        if not batch_id:
            return self._err("batch_id is required", 400)
        try:
            return self._ok(
                {
                    "data": await self.plugin.service.rollback_historical_chat_import(
                        batch_id
                    )
                }
            )
        except ValueError as exc:
            return self._err(str(exc), 404)
        except Exception as exc:
            logger.exception("历史对话导入回滚失败")
            return self._err(f"历史对话导入回滚失败: {exc}", 500)
