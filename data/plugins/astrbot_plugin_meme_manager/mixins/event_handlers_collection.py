import io
import os
import ssl
import time
from pathlib import Path

import aiohttp
from PIL import Image as PILImage

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Plain

from ..backend.semantic.storage import invalidate_semantic_metadata


class EventHandlerMixinCollection:
    """Collection; shared runtime resources belong to the facade."""

    async def _handle_upload_image_impl(self, event: AstrMessageEvent):
        user_key = f"{event.session_id}_{event.get_sender_id()}"
        upload_state = self.upload_states.get(user_key)
        if not upload_state or time.time() > upload_state["expire_time"]:
            if user_key in self.upload_states:
                del self.upload_states[user_key]
            return
        images = [c for c in event.message_obj.message if isinstance(c, Image)]
        if not images:
            yield event.plain_result("请发送图片文件来进行上传哦。")
            return
        category = upload_state["category"]
        pack_id = str(upload_state.get("pack_id") or "").strip()
        memes_dir = str(upload_state.get("memes_dir") or "").strip()
        if not pack_id or not memes_dir:
            pack_context = self._resolve_runtime_pack_context(event=event)
            pack_id = str(pack_context.get("pack_id") or "").strip()
            memes_dir = str(pack_context.get("memes_dir") or "").strip()
        current_pack_id = str(
            self._resolve_runtime_pack_context(event=event).get("pack_id") or ""
        ).strip()
        if current_pack_id != pack_id:
            del self.upload_states[user_key]
            yield event.plain_result("默认资源包已切换，请重新执行添加表情命令。")
            return
        try:
            self.semantic_task_manager.begin_external_pack_operation(
                pack_id, "接收并保存表情图片"
            )
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
            return
        save_dir = os.path.join(memes_dir, category)
        try:
            os.makedirs(save_dir, exist_ok=True)
            saved_files = []
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            for idx, img in enumerate(images, 1):
                timestamp = int(time.time())
                try:
                    if "multimedia.nt.qq.com.cn" in img.url:
                        insecure_url = img.url.replace("https://", "http://", 1)
                        async with aiohttp.ClientSession() as session:
                            async with session.get(insecure_url) as resp:
                                content = await resp.read()
                    else:
                        async with aiohttp.ClientSession(
                            connector=aiohttp.TCPConnector(ssl=ssl_context)
                        ) as session:
                            async with session.get(img.url) as resp:
                                content = await resp.read()
                    try:
                        with PILImage.open(io.BytesIO(content)) as pil_img:
                            file_type = pil_img.format.lower()
                    except Exception:
                        file_type = "unknown"
                    ext_mapping = {
                        "jpeg": ".jpg",
                        "png": ".png",
                        "gif": ".gif",
                        "webp": ".webp",
                    }
                    ext = ext_mapping.get(file_type, ".bin")
                    filename = f"{timestamp}_{idx}{ext}"
                    save_path = os.path.join(save_dir, filename)
                    with open(save_path, "wb") as f:
                        f.write(content)
                    saved_files.append(filename)
                except Exception as e:
                    logger.error(f"下载图片失败: {str(e)}")
                    yield event.plain_result(f"文件 {img.url} 下载失败啦: {str(e)}")
                    continue
            del self.upload_states[user_key]
            result_msg = [
                Plain(
                    f"✅ 已经成功收录了 {len(saved_files)} 张新表情到「{category}」图库！"
                )
            ]
            if self.img_sync:
                result_msg.append(
                    Plain("\n☁️ 检测到已配置图床，如需同步到云端请使用命令：同步到云端")
                )
            yield event.chain_result(result_msg)
            await self.reload_emotions()
            if saved_files:
                invalidate_semantic_metadata(Path(memes_dir).parent)
        except Exception as e:
            yield event.plain_result(f"保存失败了：{str(e)}")
        finally:
            self.semantic_task_manager.end_external_pack_operation(pack_id)
