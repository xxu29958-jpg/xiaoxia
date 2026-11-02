import asyncio
import os
import random

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from ..backend.packs.categories import resolve_safe_category_directory
from ..backend.packs.images import IMAGE_EXTENSIONS
from ..backend.semantic.models import (
    REVIEW_CATEGORY,
    runtime_category_mapping,
)


class EventHandlerMixinCompatibility:
    """Compatibility; shared runtime resources belong to the facade."""

    async def _build_emotion_images_for_event(
        self,
        event: AstrMessageEvent,
        emotions: list[str],
    ) -> tuple[list[Image], list[str]]:
        """根据表情列表构建待发送图片组件，并返回临时文件列表。"""
        if not emotions:
            return [], []

        random_value = random.randint(1, 100)
        if random_value > self.emotions_probability:
            return [], []

        memes_root = self._get_runtime_memes_dir_for_event(event)
        emotion_images: list[Image] = []
        temp_files: list[str] = []

        for emotion in self._limit_meme_selection(emotions):
            if not emotion or emotion == REVIEW_CATEGORY:
                continue

            try:
                emotion_path = resolve_safe_category_directory(memes_root, emotion)
            except ValueError:
                continue
            if not emotion_path.is_dir():
                continue

            memes = [
                f
                for f in os.listdir(emotion_path)
                if f.lower().endswith(IMAGE_EXTENSIONS)
            ]
            if not memes:
                continue

            meme = random.choice(memes)
            meme_file = str(emotion_path / meme)

            try:
                final_meme_file = await asyncio.to_thread(
                    self._convert_to_gif, meme_file
                )
                if final_meme_file != meme_file:
                    temp_files.append(final_meme_file)
                emotion_images.append(Image.fromFileSystem(final_meme_file))
            except Exception as e:
                logger.error(f"[meme_manager] 构建表情图片失败: {e}")

        return emotion_images, temp_files

    async def compat_prepare_message(
        self,
        event: AstrMessageEvent,
        message: str | list | MessageChain,
    ) -> dict:
        """对外兼容接口：清理消息中的表情标记并准备待发送表情图片。"""
        pack_context = self._resolve_runtime_pack_context(event=event)
        context_mapping = pack_context.get("category_mapping")
        active_category_mapping = (
            runtime_category_mapping(context_mapping)
            if isinstance(context_mapping, dict)
            else runtime_category_mapping(self.category_mapping)
        )
        valid_emoticons = set(active_category_mapping.keys())

        raw_components = self._normalize_outgoing_message_components(message)
        cleaned_components = []
        found_emotions: list[str] = []

        for component in raw_components:
            if isinstance(component, Plain):
                cleaned_text, extracted = self._extract_marked_emotions_from_text(
                    component.text,
                    valid_emoticons,
                )
                found_emotions.extend(extracted)
                if getattr(self, "filter_all_tags", False):
                    cleaned_text = self._clean_outgoing_plain_text(cleaned_text)
                if cleaned_text.strip():
                    cleaned_components.append(Plain(cleaned_text.strip()))
            else:
                cleaned_components.append(component)

        # 按模型选择的顺序保留分类，每个分类只保留一次。
        filtered_emotions = self._filter_emotion_selection(found_emotions)
        if filtered_emotions and getattr(self, "meme_usage", None):
            text = "\n".join(x.text for x in cleaned_components if isinstance(x, Plain))
            if not await self._decide_event_reaction(event, text):
                filtered_emotions = []

        emotion_images, temp_files = await self._build_emotion_images_for_event(
            event,
            filtered_emotions,
        )

        return {
            "cleaned_chain": MessageChain(cleaned_components),
            "emotions": filtered_emotions,
            "images": emotion_images,
            "temp_files": temp_files,
        }

    async def compat_send_message(
        self,
        event: AstrMessageEvent,
        message: str | list | MessageChain,
        *,
        send_images: bool = True,
    ) -> dict:
        """对外兼容接口：使用本插件逻辑清理后发送消息，并可附带发送表情图片。"""
        prepared = await self.compat_prepare_message(event, message)

        return await self.compat_send_prepared_message(
            event,
            prepared,
            send_images=send_images,
        )

    async def compat_send_prepared_message(
        self,
        event: AstrMessageEvent,
        prepared: dict,
        *,
        send_text: bool = True,
        send_images: bool = True,
    ) -> dict:
        """对外兼容接口：发送由 compat_prepare_message 生成的处理结果。"""
        cleaned_chain: MessageChain = prepared.get("cleaned_chain") or MessageChain([])
        emotion_images: list[Image] = prepared.get("images") or []
        temp_files: list[str] = prepared.get("temp_files") or []

        try:
            if send_text and cleaned_chain.chain:
                await event.send(cleaned_chain)

            if send_images and emotion_images:
                delivered = []
                for image in emotion_images:
                    usage = getattr(self, "meme_usage", None)
                    token = (
                        usage.claim_event(
                            event, path=await image.convert_to_file_path()
                        )
                        if usage
                        else None
                    )
                    if usage and not token:
                        continue
                    await self._send_meme_image(event, image)
                    delivered.append(image)
                    if usage:
                        usage.finish(token, True)
                emotion_images = delivered
        finally:
            for temp_file in temp_files:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except Exception as e:
                    logger.error(f"[meme_manager] 清理兼容接口临时文件失败: {e}")

        return {
            "sent_text": bool(send_text and cleaned_chain.chain),
            "sent_images_count": len(emotion_images) if send_images else 0,
            "detected_emotions": prepared.get("emotions") or [],
        }
