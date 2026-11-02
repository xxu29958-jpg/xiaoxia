import asyncio
import os
import random

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain, ResultContentType

from ..backend.packs.categories import resolve_safe_category_directory
from ..backend.packs.images import IMAGE_EXTENSIONS
from ..backend.semantic.models import (
    REVIEW_CATEGORY,
)
from ..backend.semantic.query import (
    validate_selected_id,
)
from ..utils import probability_hit


class EventHandlerMixinDecoration:
    """Decoration; shared runtime resources belong to the facade."""

    async def _on_decorating_result_impl(self, event: AstrMessageEvent):
        """在消息发送前清理文本中的表情标签，并添加表情图片"""
        logger.debug("[meme_manager] on_decorating_result 开始处理")

        result = event.get_result()
        if not result:
            return

        if result.result_content_type == ResultContentType.STREAMING_RESULT:
            return

        try:
            # 第一步：获取并清理原始消息链中的文本
            original_chain = result.chain
            cleaned_components = []

            if original_chain:
                for component in self._normalize_outgoing_message_components(
                    original_chain
                ):
                    if isinstance(component, Plain):
                        cleaned = self._clean_outgoing_plain_text(component.text)
                        if cleaned.strip():
                            cleaned_components.append(Plain(cleaned.strip()))
                    else:
                        cleaned_components.append(component)

            # 流式结果同样需要最后一道 Plain 文本清理。
            if result.result_content_type == ResultContentType.STREAMING_FINISH:
                if isinstance(original_chain, (str, MessageChain, list)):
                    result.chain = cleaned_components
                if (
                    self.streaming_compatibility
                    or event.get_platform_name() == "webchat"
                    or event.get_extra("meme_manager_image_only_reply")
                ):
                    await self._send_memes_streaming(event)
                return

            # 第二步：语义模式按候选 ID 精确取图，不走概率、分类目录和 random.choice。
            # 触发范围门控：仅在配置允许的消息类型上附加表情图片；
            # 文本标记清理（第一步）不受此门控影响，始终执行。
            scope_allows_attach = self._should_attach_for_result(event, result)
            semantic_selected_ids = (
                event.get_extra("meme_manager_semantic_selected_ids") or []
            )
            if (
                scope_allows_attach
                and semantic_selected_ids
                and self._semantic_mode_active(event)
            ):
                memes_root = self._get_runtime_memes_dir_for_event(event)
                pack_context = self._resolve_runtime_pack_context(event=event)
                semantic_images = []
                semantic_temp_files = []
                for selected_id in self._limit_meme_selection(semantic_selected_ids):
                    image_path = validate_selected_id(
                        event, selected_id, pack_context.get("pack_dir")
                    )
                    if image_path is None:
                        continue
                    try:
                        final_path = await asyncio.to_thread(
                            self._convert_to_gif, str(image_path)
                        )
                        if final_path != str(image_path):
                            semantic_temp_files.append(final_path)
                        usage = getattr(self, "meme_usage", None)
                        if usage and not usage.claim_event(event, selected_id):
                            continue
                        semantic_images.append(Image.fromFileSystem(final_path))
                    except Exception as exc:
                        logger.error("构建语义表情图片失败: %s", exc)
                if semantic_temp_files:
                    event.set_extra("meme_manager_temp_files", semantic_temp_files)
                if semantic_images:
                    # 语义模式不再随机丢弃模型已经选择的候选；
                    # 发送方式（合并/分离）按 mixed_message 概率决定，与旧版路径一致。
                    use_mixed_message = not cleaned_components
                    if cleaned_components and self.enable_mixed_message:
                        use_mixed_message = (
                            random.randint(1, 100) <= self.mixed_message_probability
                        )
                    if use_mixed_message and self.send_image_as_base64:
                        normalized_images = []
                        for image in semantic_images:
                            normalized_images.append(
                                await self._ensure_image_send_format(image)
                            )
                        semantic_images = normalized_images
                    if use_mixed_message:
                        cleaned_components = self._merge_components_with_images(
                            cleaned_components, semantic_images
                        )
                    else:
                        event.set_extra("meme_manager_pending_images", semantic_images)
            # 第三步：旧模式添加表情图片（如果有找到的表情）
            found_emotions = event.get_extra("found_emotions") or []
            if scope_allows_attach and found_emotions and not semantic_selected_ids:
                memes_root = self._get_runtime_memes_dir_for_event(event)
                # 概率判定复用 _resp_impl 阶段的预检结果（避免二次掷骰）；
                # extra 缺失（异常路径）时回退本地掷骰，保持旧行为。
                legacy_probability_hit = event.get_extra(
                    "meme_manager_legacy_probability_hit"
                )
                if legacy_probability_hit is None:
                    legacy_probability_hit = probability_hit(self.emotions_probability)
                if legacy_probability_hit:
                    # 创建表情图片列表
                    emotion_images = []
                    temp_files = []  # 记录临时文件路径
                    for emotion in self._limit_meme_selection(found_emotions):
                        if not emotion or emotion == REVIEW_CATEGORY:
                            continue

                        try:
                            emotion_path = resolve_safe_category_directory(
                                memes_root, emotion
                            )
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
                            # 转换静态图为 GIF（如果配置开启）
                            final_meme_file = await asyncio.to_thread(
                                self._convert_to_gif, meme_file
                            )
                            if final_meme_file != meme_file:
                                temp_files.append(final_meme_file)
                            usage = getattr(self, "meme_usage", None)
                            if usage and not usage.claim_event(event, path=meme_file):
                                continue
                            emotion_images.append(Image.fromFileSystem(final_meme_file))
                        except Exception as e:
                            logger.error(f"添加表情图片失败: {e}")

                    if temp_files:
                        existing_temp_files = (
                            event.get_extra("meme_manager_temp_files") or []
                        )
                        event.set_extra(
                            "meme_manager_temp_files", existing_temp_files + temp_files
                        )

                    if emotion_images:
                        use_mixed_message = not cleaned_components
                        if cleaned_components and self.enable_mixed_message:
                            use_mixed_message = (
                                random.randint(1, 100) <= self.mixed_message_probability
                            )

                        if use_mixed_message and self.send_image_as_base64:
                            normalized_images = []
                            for image in emotion_images:
                                normalized_images.append(
                                    await self._ensure_image_send_format(image)
                                )
                            emotion_images = normalized_images

                        if use_mixed_message:
                            cleaned_components = self._merge_components_with_images(
                                cleaned_components, emotion_images
                            )
                        else:
                            event.set_extra(
                                "meme_manager_pending_images", emotion_images
                            )
                    else:
                        pass

            # 清空当前事件已处理的表情列表
            event.set_extra("found_emotions", None)
            event.set_extra("meme_manager_semantic_selected_ids", None)

            # 第三步：更新消息链
            if isinstance(original_chain, (str, MessageChain, list)):
                # 始终写回安全处理后的组件列表，空列表也不能回退到原始脏文本。
                result.chain = cleaned_components

            logger.debug("[meme_manager] on_decorating_result 处理完成")

        except Exception as e:
            logger.exception("处理消息装饰失败：%s", e)

    @filter.after_message_sent()
    async def _after_message_sent_impl(self, event: AstrMessageEvent):
        """消息发送后处理。用于发送未混合的表情图片。"""
        pending_images = event.get_extra("meme_manager_pending_images")

        try:
            if pending_images:
                for image in pending_images:
                    await self._send_meme_image(event, image)
            usage = getattr(self, "meme_usage", None)
            if usage:
                for token in event.get_extra("meme_manager_delivery_tokens") or []:
                    usage.finish(token, True)
        except Exception as e:
            logger.exception("发送表情图片失败：%s", e)
        finally:
            event.set_extra("meme_manager_pending_images", None)
            event.set_extra("meme_manager_delivery_tokens", None)

            # 清理临时文件
            temp_files = event.get_extra("meme_manager_temp_files")
            if temp_files:
                for temp_file in temp_files:
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                            logger.debug(f"[meme_manager] 已清理临时文件: {temp_file}")
                    except Exception as e:
                        logger.error(f"[meme_manager] 清理临时文件失败: {e}")
                event.set_extra("meme_manager_temp_files", None)
