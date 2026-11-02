import asyncio
import re
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.utils.session_waiter import SessionFilter, session_waiter

from ..backend.packs.images import (
    clear_all_emojis,
    clear_category_emojis,
    get_emoji_by_category,
)
from ..backend.packs.storage import install_first_official_pack_from_index
from ..backend.semantic.storage import invalidate_semantic_metadata
from ..config import COMMUNITY_INDEX_URL
from .commands_inspection import InspectionCommandsMixin


class CategoryCreationCancelled(Exception):
    """The requesting user cancelled category creation."""


class ConfirmationCancelled(Exception):
    """The requesting user declined a destructive operation."""


class SenderScopedSessionFilter(SessionFilter):
    """Keep command confirmation within its platform, conversation and sender."""

    def filter(self, event: AstrMessageEvent) -> str:
        return f"{event.unified_msg_origin}:meme-command:{event.get_sender_id()}"


class CommandMixin(InspectionCommandsMixin):
    """表情包管理命令组及所有管理命令"""

    @filter.command_group("表情管理")
    def meme_manager(self):
        """表情包管理命令组:
        查看图库
        添加分类
        添加表情
        恢复默认表情包
        清空指定类型
        清空全部
        删除类型本身
        同步状态
        同步到云端
        从云端同步
        图库统计
        """
        pass

    # ---------- 辅助方法 ----------
    def _extract_category_description_from_command(
        self, event: AstrMessageEvent, category: str
    ) -> str:
        command_prefix = "表情管理 添加分类"
        message = re.sub(r"\s+", " ", event.get_message_str().strip())
        if not message.startswith(command_prefix):
            return ""
        remaining = message[len(command_prefix) :].strip()
        if remaining == category:
            return ""
        if not remaining.startswith(f"{category} "):
            return ""
        return remaining[len(category) :].strip()

    async def _wait_for_category_description(
        self, event: AstrMessageEvent, category: str, timeout: int = 60
    ) -> str:
        description = ""

        @session_waiter(timeout=timeout, record_history_chains=False)
        async def description_waiter(
            controller, description_event: AstrMessageEvent
        ) -> None:
            nonlocal description
            reply = (description_event.message_str or "").strip()
            if reply == "返回":
                await description_event.send(
                    description_event.plain_result("已取消创建分类。")
                )
                controller.stop(CategoryCreationCancelled())
                return
            if not reply:
                await description_event.send(
                    description_event.plain_result(
                        f"请发送分类「{category}」的描述，或发送“返回”取消创建。"
                    )
                )
                controller.keep(timeout=timeout, reset_timeout=True)
                return
            description = reply
            controller.stop()

        await description_waiter(event, SenderScopedSessionFilter())
        return description

    async def _wait_for_command_confirmation(
        self, event: AstrMessageEvent, timeout: int = 30
    ) -> bool:
        @session_waiter(timeout=timeout, record_history_chains=False)
        async def confirmation_waiter(
            controller, confirm_event: AstrMessageEvent
        ) -> None:
            reply = (confirm_event.message_str or "").strip()
            if reply in {"确认", "确定"}:
                controller.stop()
                return
            if reply in {"取消", "退出"}:
                await confirm_event.send(confirm_event.plain_result("已取消本次操作。"))
                controller.stop(ConfirmationCancelled())
                return
            await confirm_event.send(
                confirm_event.plain_result(
                    "请回复“确认”继续执行，或回复“取消”终止本次操作。"
                )
            )
            controller.keep(timeout=timeout, reset_timeout=True)

        try:
            await confirmation_waiter(event, SenderScopedSessionFilter())
            return True
        except TimeoutError:
            await event.send(event.plain_result("⌛ 等待确认超时，操作已取消。"))
            return False
        except ConfirmationCancelled:
            return False

    def _format_category_counts(
        self, category_counts: dict[str, int], limit: int = 8
    ) -> str:
        non_empty_items = [
            (c, cnt) for c, cnt in sorted(category_counts.items()) if cnt > 0
        ]
        if not non_empty_items:
            return "无可删除的表情包文件。"
        lines = [f"- {c}: {cnt} 个" for c, cnt in non_empty_items[:limit]]
        if len(non_empty_items) > limit:
            lines.append(f"- 其余 {len(non_empty_items) - limit} 个类型已省略")
        return "\n".join(lines)

    # ---------- 命令实现 ----------
    @meme_manager.command("查看图库")
    async def list_emotions(self, event: AstrMessageEvent):
        pack_context = self._resolve_runtime_pack_context(event=event)
        descriptions = pack_context.get("category_mapping") or self.category_mapping
        categories = "\n".join(
            [f"- {tag}: {desc}" for tag, desc in descriptions.items()]
        )
        yield event.plain_result(f"🖼️ 当前图库：\n{categories}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("添加分类")
    async def add_category_command(self, event: AstrMessageEvent, category: str = None):
        if not category:
            yield event.plain_result(
                "📌 若要添加分类，请按照此格式操作：\n"
                "/表情管理 添加分类 [类别名称] [描述]\n"
                "也可以只发送类别名称，随后按提示补充描述。"
            )
            return
        category = category.strip()
        available_categories = await asyncio.to_thread(self._get_manageable_categories)
        if category in available_categories:
            yield event.plain_result(f"ℹ️ 分类「{category}」已存在，无需重复创建。")
            return
        description = self._extract_category_description_from_command(event, category)
        if not description:
            yield event.plain_result(
                f"请发送分类「{category}」的描述，或发送“返回”取消创建。"
            )
            try:
                description = await self._wait_for_category_description(event, category)
            except TimeoutError:
                yield event.plain_result("⌛ 等待描述超时，已取消创建分类。")
                return
            except CategoryCreationCancelled:
                return
        try:
            created = await self._run_default_pack_mutation(
                "创建表情分类",
                lambda: self.category_manager.create_category(category, description),
            )
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
            return
        if not created:
            yield event.plain_result(f"❌ 创建分类「{category}」失败，请稍后重试。")
            return
        self._reload_personas()
        yield event.plain_result(f"✅ 已创建分类「{category}」：{description}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("添加表情")
    async def upload_meme(self, event: AstrMessageEvent, category: str = None):
        if not category:
            yield event.plain_result(
                "📌 若要添加表情，请按照此格式操作：\n/表情管理 添加表情 [类别名称]\n（输入/查看图库 可获取类别列表）"
            )
            return
        descriptions = await asyncio.to_thread(self.category_manager.get_descriptions)
        if category not in descriptions:
            yield event.plain_result(
                f"您输入的表情包类别「{category}」是无效的哦。\n可以使用/查看表情包来查看可用的类别。"
            )
            return
        user_key = f"{event.session_id}_{event.get_sender_id()}"
        pack_context = self._resolve_runtime_pack_context(event=event)
        self.upload_states[user_key] = {
            "category": category,
            "pack_id": str(pack_context.get("pack_id") or ""),
            "memes_dir": str(pack_context.get("memes_dir") or ""),
            "expire_time": time.time() + 30,
        }
        yield event.plain_result(
            f"请在30秒内发送要添加到【{category}】类别的图片（可发送多张图片）。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("恢复默认表情包")
    async def restore_default_memes_command(
        self, event: AstrMessageEvent, category: str = None
    ):
        """从社区索引安装首个官方表情包并设为默认。"""
        if category:
            yield event.plain_result(
                "ℹ️ 该命令已改为从官方仓库安装默认包，不再支持按类别恢复。"
            )

        try:
            result = await self._run_guarded_runtime_file_operation(
                "安装官方资源包",
                install_first_official_pack_from_index,
                index_url=COMMUNITY_INDEX_URL,
                overwrite=False,
                set_as_default=True,
            )
            selected_name = str(
                result.get("selected_pack_name")
                or result.get("name")
                or result.get("pack_id")
                or ""
            )
            selected_pack_id = str(
                result.get("pack_id") or result.get("selected_pack_id") or ""
            )
            self._reload_personas()
            yield event.plain_result(
                f"✅ 已从官方仓库安装默认表情包：{selected_name} ({selected_pack_id})。"
            )
        except FileExistsError:
            yield event.plain_result(
                "⚠️ 目标表情包已存在。请先在广场或管理页卸载同名包后重试。"
            )
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
        except Exception as exc:
            logger.error("从官方仓库安装默认表情包失败: %s", exc, exc_info=True)
            yield event.plain_result(f"❌ 安装默认表情包失败：{exc}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("清空指定类型")
    async def clear_category_command(
        self, event: AstrMessageEvent, category: str = None
    ):
        """清空指定类型下的所有表情包，但保留类型本身。"""
        if not category:
            yield event.plain_result(
                "📌 若要清空指定类型，请按照此格式操作：\n/表情管理 清空指定类型 [类别名称]"
            )
            return

        category = category.strip()
        available_categories = await asyncio.to_thread(self._get_manageable_categories)
        if category not in available_categories:
            yield event.plain_result(
                f"⚠️ 未找到类型「{category}」。\n可先使用 /表情管理 查看图库 查看当前类型。"
            )
            return

        emoji_count = len(await asyncio.to_thread(get_emoji_by_category, category))
        if emoji_count == 0:
            yield event.plain_result(f"📭 类型「{category}」当前没有可清空的表情包。")
            return

        yield event.plain_result(
            f"⚠️ 即将清空类型「{category}」下的 {emoji_count} 个表情包，但会保留类型本身。\n"
            "请在 30 秒内回复“确认”继续执行，或回复“取消”终止本次操作。"
        )
        if not await self._wait_for_command_confirmation(event):
            return

        def mutate():
            clear_result = clear_category_emojis(category)
            if clear_result.get("deleted_files"):
                self._invalidate_default_pack_semantics()
            return clear_result

        try:
            result = await self._run_default_pack_mutation("清空表情分类", mutate)
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
            return
        deleted_count = len(result["deleted_files"])
        yield event.plain_result(
            f"✅ 已清空类型「{category}」，共删除 {deleted_count} 个表情包。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("清空全部")
    async def clear_all_emojis_command(self, event: AstrMessageEvent):
        """清空所有类型下的表情包，但保留类型和描述配置。"""
        available_categories = sorted(
            await asyncio.to_thread(self._get_manageable_categories)
        )
        category_counts = await asyncio.to_thread(
            lambda: {
                category: len(get_emoji_by_category(category))
                for category in available_categories
            }
        )
        total_count = sum(category_counts.values())

        if total_count == 0:
            yield event.plain_result("📭 当前没有可清空的表情包文件。")
            return

        category_count = sum(1 for count in category_counts.values() if count > 0)
        summary = self._format_category_counts(category_counts)
        yield event.plain_result(
            f"⚠️ 即将清空全部表情包，共 {total_count} 个文件，涉及 {category_count} 个类型。\n"
            "该操作会保留所有类型名称和描述配置。\n"
            f"{summary}\n"
            "请在 30 秒内回复“确认”继续执行，或回复“取消”终止本次操作。"
        )
        if not await self._wait_for_command_confirmation(event):
            return

        def mutate():
            clear_result = clear_all_emojis()
            if any(clear_result.get("deleted_by_category", {}).values()):
                self._invalidate_default_pack_semantics()
            return clear_result

        try:
            result = await self._run_default_pack_mutation("清空全部表情图片", mutate)
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
            return
        deleted_total = sum(result["deleted_by_category"].values())
        yield event.plain_result(
            f"✅ 已清空全部表情包，共删除 {deleted_total} 个文件，类型配置已保留。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("删除类型本身")
    async def delete_category_command(
        self, event: AstrMessageEvent, category: str = None
    ):
        """删除指定类型本身，同时移除其描述配置和本地文件夹。"""
        if not category:
            yield event.plain_result(
                "📌 若要删除类型本身，请按照此格式操作：\n/表情管理 删除类型本身 [类别名称]"
            )
            return

        category = category.strip()
        available_categories = await asyncio.to_thread(self._get_manageable_categories)
        if category not in available_categories:
            yield event.plain_result(
                f"⚠️ 未找到类型「{category}」。\n可先使用 /表情管理 查看图库 查看当前类型。"
            )
            return

        emoji_count = len(await asyncio.to_thread(get_emoji_by_category, category))
        yield event.plain_result(
            f"⚠️ 即将删除类型「{category}」本身，并移除其描述配置"
            f"{f'，同时删除其中的 {emoji_count} 个表情包' if emoji_count > 0 else ''}。\n"
            "该操作不可恢复。\n"
            "请在 30 秒内回复“确认”继续执行，或回复“取消”终止本次操作。"
        )
        if not await self._wait_for_command_confirmation(event):
            return

        try:
            deleted = await self._run_default_pack_mutation(
                "删除表情分类",
                lambda: self.category_manager.delete_category(category),
            )
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
            return
        if not deleted:
            yield event.plain_result(f"❌ 删除类型「{category}」失败，请稍后重试。")
            return

        self._reload_personas()
        yield event.plain_result(
            f"✅ 已删除类型「{category}」"
            f"{f'，并移除 {emoji_count} 个表情包。' if emoji_count > 0 else '。'}"
        )

    # 同步相关命令
    @meme_manager.command("同步状态")
    async def check_sync_status(self, event: AstrMessageEvent, detail: str = None):
        async for result in self._check_sync_status(event):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("同步到云端")
    async def sync_to_remote(self, event: AstrMessageEvent):
        """将本地表情包同步到云端"""
        sync_client = self._ensure_img_sync_for_pack()
        if not sync_client:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        try:
            yield event.plain_result("⚡ 正在开启云端同步任务...")
            success = await sync_client.start_sync("upload")
            if success:
                yield event.plain_result("云端同步已完成！")
            else:
                yield event.plain_result("云端同步失败，请查看日志哦。")
        except Exception as e:
            logger.error(f"同步到云端失败: {str(e)}")
            yield event.plain_result(f"同步到云端失败: {str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("从云端同步")
    async def sync_from_remote(self, event: AstrMessageEvent):
        """从云端同步表情包到本地"""
        sync_client = self._ensure_img_sync_for_pack()
        if not sync_client:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        pack_id = str(
            getattr(self, "_img_sync_pack_id", "")
            or self._resolve_runtime_pack_context().get("pack_id")
        ).strip()
        try:
            self.semantic_task_manager.begin_external_pack_operation(
                pack_id, "从图床下载表情包"
            )
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
            return
        try:
            yield event.plain_result("开始从云端进行同步...")
            success = await sync_client.start_sync("download")
            if success:
                yield event.plain_result("从云端同步已完成！")
                # 重新加载表情配置
                await self.reload_emotions()
            else:
                yield event.plain_result("从云端同步失败，请查看日志哦。")
        except Exception as e:
            logger.error(f"从云端同步失败: {str(e)}")
            yield event.plain_result(f"从云端同步失败: {str(e)}")
        finally:
            try:
                pack_dir = Path(sync_client.local_dir).parent
                if (pack_dir / "semantic_metadata.json").is_file():
                    await asyncio.to_thread(invalidate_semantic_metadata, pack_dir)
            except Exception as exc:
                logger.warning("图片同步后无法使语义元数据失效：%s", exc)
            finally:
                self.semantic_task_manager.end_external_pack_operation(pack_id)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("覆盖到云端")
    async def overwrite_to_remote(self, event: AstrMessageEvent):
        """让云端完全和本地一致（会删除云端多出的图）"""
        sync_client = self._ensure_img_sync_for_pack()
        if not sync_client:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        try:
            yield event.plain_result(
                "⚠️ 正在执行覆盖到云端任务（将清理云端多余文件）..."
            )
            success = await sync_client.start_sync("overwrite_to_remote")
            if success:
                yield event.plain_result(
                    "覆盖到云端任务已完成！云端现在与本地完全一致。"
                )
            else:
                yield event.plain_result("任务失败，请查看日志。")
        except Exception as e:
            logger.error(f"覆盖到云端失败: {str(e)}")
            yield event.plain_result(f"覆盖到云端失败: {str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @meme_manager.command("从云端覆盖")
    async def overwrite_from_remote(self, event: AstrMessageEvent):
        """让本地完全和云端一致（会删除本地多出的图）"""
        sync_client = self._ensure_img_sync_for_pack()
        if not sync_client:
            yield event.plain_result(
                "图床服务尚未配置，请先在配置文件中完成图床配置哦。"
            )
            return

        pack_id = str(
            getattr(self, "_img_sync_pack_id", "")
            or self._resolve_runtime_pack_context().get("pack_id")
        ).strip()
        try:
            self.semantic_task_manager.begin_external_pack_operation(
                pack_id, "从远端覆盖本地表情包"
            )
        except RuntimeError as exc:
            yield event.plain_result(f"⚠️ {exc}")
            return
        try:
            yield event.plain_result(
                "⚠️ 正在执行从云端覆盖任务（将清理本地多余文件）..."
            )
            success = await sync_client.start_sync("overwrite_from_remote")
            if success:
                yield event.plain_result(
                    "从云端覆盖任务已完成！本地现在与云端完全一致。"
                )
            else:
                yield event.plain_result("任务失败，请查看日志。")
        except Exception as e:
            logger.error(f"从云端覆盖失败: {str(e)}")
            yield event.plain_result(f"从云端覆盖失败: {str(e)}")
        finally:
            try:
                pack_dir = Path(sync_client.local_dir).parent
                if (pack_dir / "semantic_metadata.json").is_file():
                    await asyncio.to_thread(invalidate_semantic_metadata, pack_dir)
            except Exception as exc:
                logger.warning("图片同步后无法使语义元数据失效：%s", exc)
            finally:
                self.semantic_task_manager.end_external_pack_operation(pack_id)

    @meme_manager.command("图库统计")
    async def show_library_stats(self, event: AstrMessageEvent):
        async for result in self._show_library_stats(event):
            yield result
