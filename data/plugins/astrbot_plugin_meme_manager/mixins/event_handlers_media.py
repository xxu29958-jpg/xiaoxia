import asyncio
import os
import random
import tempfile
import time

from PIL import Image as PILImage

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from ..backend.packs.categories import resolve_safe_category_directory
from ..backend.packs.images import IMAGE_EXTENSIONS
from ..backend.semantic.models import (
    REVIEW_CATEGORY,
)
from ..backend.semantic.query import (
    validate_selected_id,
)
from ..utils import probability_hit


class EventHandlerMixinMedia:
    """Media; shared runtime resources belong to the facade."""

    def _convert_to_gif(self, image_path: str) -> str:
        """
        将静态图片转换为 GIF 格式。
        如果图片已经是 GIF，则返回原路径。
        如果转换成功，返回临时 GIF 文件的路径。
        """
        if not self.convert_static_to_gif:
            return image_path

        if image_path.lower().endswith(".gif"):
            return image_path

        try:
            with PILImage.open(image_path) as img:
                # 检查是否已经是 GIF (虽然后缀不是 .gif，但内容可能是)
                if img.format == "GIF":
                    return image_path

                # 创建临时文件
                temp_dir = tempfile.gettempdir()
                temp_filename = os.path.join(
                    temp_dir,
                    f"meme_{int(time.time())}_{random.randint(1000, 9999)}.gif",
                )

                # 转换为 RGB (如果是 RGBA 需要处理透明度)
                if img.mode in ("RGBA", "LA") or (
                    img.mode == "P" and "transparency" in img.info
                ):
                    # 创建白色背景
                    background = PILImage.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    background.paste(img, mask=img.split()[3])  # 第 3 个索引是透明通道
                    img = background
                else:
                    img = img.convert("RGB")

                # 保存为 GIF
                img.save(temp_filename, "GIF")
                logger.debug(f"[meme_manager] 已将静态图转换为 GIF: {temp_filename}")
                return temp_filename
        except Exception as e:
            logger.error(f"[meme_manager] 转换图片为 GIF 失败: {e}")
            return image_path

    async def _send_memes_streaming(self, event: AstrMessageEvent):
        """流式传输兼容模式：在流式消息发送完成后，主动发送表情图片作为独立消息。"""
        # 流式结果仍须通过请求来源门控，与非流式主路径保持一致。
        if not self._should_attach_for_result(event, event.get_result()):
            event.set_extra("meme_manager_semantic_selected_ids", None)
            event.set_extra("found_emotions", None)
            return
        semantic_selected_ids = (
            event.get_extra("meme_manager_semantic_selected_ids") or []
        )
        if semantic_selected_ids and self._semantic_mode_active(event):
            pack_context = self._resolve_runtime_pack_context(event=event)
            try:
                for selected_id in self._limit_meme_selection(semantic_selected_ids):
                    image_path = validate_selected_id(
                        event, selected_id, pack_context.get("pack_dir")
                    )
                    if image_path is None:
                        continue
                    usage = getattr(self, "meme_usage", None)
                    token = usage.claim_event(event, selected_id) if usage else None
                    if usage and not token:
                        continue
                    final_path = await asyncio.to_thread(
                        self._convert_to_gif, str(image_path)
                    )
                    try:
                        await self._send_meme_image(
                            event, Image.fromFileSystem(final_path)
                        )
                        if usage:
                            usage.finish(token, True)
                    finally:
                        if final_path != str(image_path) and os.path.exists(final_path):
                            os.remove(final_path)
            except Exception as exc:
                logger.error("流式语义表情发送失败: %s", exc, exc_info=True)
            finally:
                event.set_extra("meme_manager_semantic_selected_ids", None)
            return
        found_emotions = event.get_extra("found_emotions") or []
        if not found_emotions:
            return

        memes_root = self._get_runtime_memes_dir_for_event(event)

        try:
            # 概率判定复用 _resp_impl 阶段的预检结果；extra 缺失时回退本地掷骰。
            legacy_probability_hit = event.get_extra(
                "meme_manager_legacy_probability_hit"
            )
            if legacy_probability_hit is None:
                legacy_probability_hit = probability_hit(self.emotions_probability)
            if not legacy_probability_hit:
                return

            for emotion in self._limit_meme_selection(found_emotions):
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
                final_meme_file = await asyncio.to_thread(
                    self._convert_to_gif, meme_file
                )

                try:
                    usage = getattr(self, "meme_usage", None)
                    token = usage.claim_event(event, path=meme_file) if usage else None
                    if usage and not token:
                        continue
                    await self._send_meme_image(
                        event, Image.fromFileSystem(final_meme_file)
                    )
                    if usage:
                        usage.finish(token, True)
                except Exception as e:
                    logger.error(f"[meme_manager] 流式模式发送表情失败: {e}")
                finally:
                    # 清理临时文件
                    if final_meme_file != meme_file and os.path.exists(final_meme_file):
                        try:
                            os.remove(final_meme_file)
                        except Exception:
                            pass
        except Exception as e:
            logger.exception("[meme_manager] 流式模式处理表情失败：%s", e)
        finally:
            event.set_extra("found_emotions", None)

    async def _send_meme_image(self, event: AstrMessageEvent, image: Image) -> None:
        image = await self._ensure_image_send_format(image)
        if event.get_platform_name() in {"gewechat", "webchat"}:
            await event.send(MessageChain([image]))
            return
        sent = await self.context.send_message(
            event.unified_msg_origin, MessageChain([image])
        )
        if sent is False:
            raise RuntimeError("Platform did not accept the sticker message")

    async def _ensure_image_send_format(self, image: Image) -> Image:
        """根据配置规范图片发送格式。"""
        if not self.send_image_as_base64:
            return image

        image_ref = image.file or image.url or ""
        if isinstance(image_ref, str) and image_ref.startswith("base64://"):
            return image

        try:
            base64_data = await image.convert_to_base64()
            if not base64_data:
                return image
            return Image.fromBase64(base64_data)
        except Exception as e:
            logger.error(f"[meme_manager] 转换图片为 base64 失败: {e}")
            return image

    def _merge_components_with_images(self, components, images):
        """将表情图片与文本组件智能配对，支持分段回复

        Args:
            components: 清理后的消息组件列表
            images: 表情图片列表

        Returns:
            合并后的消息组件列表，图片会合理地分布在文本中
        """
        logger.debug(
            f"[meme_manager] _merge_components_with_images 输入: 组件总数={len(components)}, 图片总数={len(images)}"
        )

        if not images:
            return components

        if not components:
            # 没有文本组件，只发送图片
            return images

        # 找到所有 Plain 组件的索引
        plain_indices = [
            i for i, comp in enumerate(components) if isinstance(comp, Plain)
        ]
        logger.debug(f"[meme_manager] Plain 组件的索引位置列表: {plain_indices}")

        if not plain_indices:
            # 没有 Plain 组件，直接添加图片到末尾
            return components + images

        # 策略：将图片均匀分布在文本组件中，优先在文本后添加图片
        # 这样在分段回复时，图片更容易和对应的文本一起发送
        merged_components = components.copy()
        images_per_text = max(
            1, len(images) // len(plain_indices)
        )  # 每个文本至少配一张图片
        image_index = 0
        images_inserted_so_far = 0  # 跟踪已插入的图片数量

        for idx, plain_idx in enumerate(plain_indices):
            if image_index >= len(images):
                break

            # 计算这个文本应该配多少张图片
            if idx == len(plain_indices) - 1:
                # 最后一个文本组件，分配所有剩余图片
                images_for_this_text = len(images) - image_index
            else:
                images_for_this_text = min(images_per_text, len(images) - image_index)

            logger.debug(
                f"[meme_manager] Plain 组件 {idx} (索引={plain_idx}) 分配的图片数量: {images_for_this_text}"
            )

            # 在这个文本组件后插入图片
            # 注意：plain_idx 是在原始 components 中的位置，但由于我们已经插入了一些图片，
            # 需要考虑已插入图片对当前位置的影响
            insert_pos = plain_idx + 1 + images_inserted_so_far

            for _ in range(images_for_this_text):
                if image_index < len(images):
                    merged_components.insert(insert_pos, images[image_index])
                    image_index += 1
                    insert_pos += 1
                    images_inserted_so_far += 1

        logger.debug(
            f"[meme_manager] 合并前组件总数: {len(components)}, 合并后组件总数: {len(merged_components)}"
        )

        return merged_components
