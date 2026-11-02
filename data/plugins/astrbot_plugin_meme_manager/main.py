import asyncio
import copy
import json

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star

from . import config as pack_paths
from .backend.auto_collect import AutoCollectManager
from .backend.packs.categories import CategoryManager
from .backend.semantic.preview import visual_candidates
from .backend.semantic.query import (
    candidate_records,
    dumps_result,
    remember_candidates,
    search_memes,
)
from .backend.semantic.task import SemanticTaskManager
from .main_image_host import MemeSenderImageHost
from .main_personas import MemeSenderPersonas
from .main_prompts import MemeSenderPrompts
from .main_semantic import MemeSenderSemantic
from .main_support import MEME_PROMPT_MARKER_END as MEME_PROMPT_MARKER_END
from .main_support import MEME_PROMPT_MARKER_START as MEME_PROMPT_MARKER_START
from .main_support import PLUGIN_NAME as PLUGIN_NAME
from .main_support import SEMANTIC_PROMPT_MARKER_END as SEMANTIC_PROMPT_MARKER_END
from .main_support import SEMANTIC_PROMPT_MARKER_START as SEMANTIC_PROMPT_MARKER_START
from .main_support import WEBUI_LOG_PREFIX as WEBUI_LOG_PREFIX
from .meme_usage import MemeUsage, event_scope
from .mixins.commands import CommandMixin
from .mixins.event_handlers import EventHandlerMixin, normalize_trigger_scope
from .mixins.web_api import WebAPIMixin
from .visual_selection import MemeVisualSelection


class MemeSender(
    MemeVisualSelection,
    MemeSenderImageHost,
    MemeSenderPrompts,
    MemeSenderSemantic,
    MemeSenderPersonas,
    Star,
    WebAPIMixin,
    CommandMixin,
    EventHandlerMixin,
):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self._normalize_mixin_handler_module_paths()
        self.config = config if config is not None else {}
        self.meme_usage = MemeUsage(pack_paths.PLUGIN_DATA_DIR / "meme_usage.sqlite3")
        schema = getattr(self.config, "schema", None)
        if schema:
            previous = copy.deepcopy(dict(self.config))
            pending = [(self.config, schema)]
            while pending:
                current, items = pending.pop()
                for key in tuple(current):
                    if key not in items:
                        del current[key]
                        continue
                    spec = items[key]
                    if spec["type"] == "object" and isinstance(current[key], dict):
                        pending.append((current[key], spec["items"]))
                    elif spec.get("options") and current[key] not in spec["options"]:
                        current[key] = copy.deepcopy(spec["default"])
            if self.config != previous:
                # 使用 AstrBot 的原子写入器保存仅包含配置结构的设置。
                try:
                    self.config.save_config()
                except Exception:
                    self.config.clear()
                    self.config.update(previous)
                    raise

        # 只有执行语义操作时才需要模型。
        self.semantic_enabled = bool(
            self._read_config_value(
                ("semantic", "enabled"),
                default=False,
            )
        )
        self.semantic_vision_provider_id = str(
            self._read_config_value(
                ("semantic", "vision_provider_id"),
                default="",
            )
            or ""
        )
        self.semantic_embedding_provider_id = str(
            self._read_config_value(
                ("semantic", "embedding_provider_id"),
                default="",
            )
            or ""
        )
        self.semantic_top_k = int(
            self._read_config_value(("semantic", "top_k"), default=5) or 5
        )
        self.semantic_min_score = float(
            self._read_config_value(("semantic", "min_score"), default=0.25)
        )
        self.semantic_task_manager = SemanticTaskManager(
            pack_paths.PLUGIN_DATA_DIR,
            context=context,
            config={
                "vision_provider_id": self.semantic_vision_provider_id,
                "embedding_provider_id": self.semantic_embedding_provider_id,
            },
        )

        # 初始化类别管理器
        self.category_manager = CategoryManager()

        # 图床初始化
        self.img_sync = None
        self.img_sync_config = None
        self.img_sync_provider_type = None
        self._img_sync_pack_id = ""
        self._last_img_host_sync_task_status = None
        self._img_host_sync_monitor = None
        self._community_install_jobs = {}
        self._community_install_tasks = set()
        image_host_type = self._get_image_host_type()

        if image_host_type == "stardots":
            stardots_config = self._get_provider_config("stardots")
            if stardots_config.get("key") and stardots_config.get("secret"):
                stardots_config["provider"] = "stardots"
                self.img_sync_config = {
                    "key": stardots_config["key"],
                    "secret": stardots_config["secret"],
                    "space": stardots_config.get("space", "memes"),
                    "provider": "stardots",
                }
                self.img_sync_provider_type = "stardots"
        elif image_host_type == "cloudflare_r2":
            r2_config = self._get_provider_config("cloudflare_r2")
            required_fields = [
                "account_id",
                "access_key_id",
                "secret_access_key",
                "bucket_name",
            ]
            if all(r2_config.get(field) for field in required_fields):
                if r2_config.get("public_url"):
                    r2_config["public_url"] = r2_config["public_url"].rstrip("/")
                r2_config["provider"] = "cloudflare_r2"
                self.img_sync_config = dict(r2_config)
                self.img_sync_provider_type = "cloudflare_r2"
                self._r2_bucket_name = r2_config.get("bucket_name")
        elif image_host_type == "lsky":
            lsky_config = self._get_provider_config("lsky")
            if lsky_config.get("url") and lsky_config.get("token"):
                self.img_sync_config = dict(lsky_config)
                self.img_sync_provider_type = "lsky"
        elif image_host_type == "webdav":
            webdav_config = self._get_provider_config("webdav")
            required_fields = ["url", "username", "password"]
            if all(webdav_config.get(field) for field in required_fields):
                if webdav_config.get("url"):
                    webdav_config["url"] = str(webdav_config["url"]).rstrip("/")
                if webdav_config.get("public_url"):
                    webdav_config["public_url"] = str(
                        webdav_config["public_url"]
                    ).rstrip("/")
                webdav_config["provider"] = "webdav"
                self.img_sync_config = dict(webdav_config)
                self.img_sync_provider_type = "webdav"
                self._webdav_url = webdav_config.get("url")
            else:
                missing_fields = [
                    field for field in required_fields if not webdav_config.get(field)
                ]
                logger.warning(
                    "WebDAV 图床未初始化，缺少必要配置项: %s。当前已读取字段: %s",
                    ", ".join(missing_fields),
                    ", ".join(sorted(webdav_config.keys())) or "无",
                )

        # 图床客户端按当前默认/目标表情包动态构建，避免固定绑定插件启动时目录。
        if self.img_sync_config and self.img_sync_provider_type:
            self._ensure_img_sync_for_pack()

        # 上传与待发送状态
        self.upload_states = {}
        self.pending_images = {}
        auto_collect_config = self._read_path(("auto_collect",), {})
        self.auto_collect_manager = AutoCollectManager(
            self,
            auto_collect_config if isinstance(auto_collect_config, dict) else {},
        )

        # 配置项
        self.prompt_head = self._read_config_value(
            ("generation", "prompt", "head"),
            default="",
        )
        self.prompt_tail = self._read_config_value(
            ("generation", "prompt", "tail"), default=""
        )
        self.prompt_examples = [
            (
                self._read_config_value(
                    ("generation", "prompt", f"{name}_enabled"), default=False
                ),
                self._read_config_value(("generation", "prompt", name), default=""),
            )
            for name in ("category_example", "reply_example")
        ]
        self.max_memes_per_message = self._read_config_value(
            ("generation", "emotion", "max_memes_per_message"), default=-1
        )
        self.emotions_probability = self._read_config_value(
            ("generation", "emotion", "probability"),
            default=100,
        )
        self.emotion_llm_enabled = self._read_config_value(
            ("generation", "emotion", "llm", "enabled"),
            default=False,
        )
        self.emotion_llm_provider_id = self._read_config_value(
            ("generation", "emotion", "llm", "provider_id"),
            default="",
        )
        emotion_llm_context_turns = self._read_config_value(
            ("generation", "emotion", "llm", "context_turns"),
            default=0,
        )
        try:
            emotion_llm_context_turns = int(emotion_llm_context_turns)
        except (TypeError, ValueError):
            emotion_llm_context_turns = 0
        self.emotion_llm_context_turns = max(0, min(20, emotion_llm_context_turns))
        self.emotion_llm_inject_persona = bool(
            self._read_config_value(
                ("generation", "emotion", "llm", "inject_persona"),
                default=False,
            )
        )
        self.enable_mixed_message = self._read_config_value(
            ("generation", "message", "enable_mixed"),
            default=False,
        )
        self.mixed_message_probability = self._read_config_value(
            ("generation", "message", "mixed_probability"),
            default=50,
        )
        self.remove_invalid_alternative_markup = self._read_config_value(
            ("generation", "markup", "remove_invalid_alternative"),
            default=False,
        )
        self.filter_all_tags = self._read_config_value(
            ("generation", "markup", "filter_all_tags"), default=False
        )
        self.convert_static_to_gif = self._read_config_value(
            ("generation", "message", "convert_static_to_gif"),
            default=False,
        )
        self.streaming_compatibility = self._read_config_value(
            ("generation", "message", "streaming_compatibility"),
            default=True,
        )
        self.send_image_as_base64 = self._read_config_value(
            ("generation", "message", "send_image_as_base64"),
            default=False,
        )
        self.content_cleanup_rule = self._read_config_value(
            ("generation", "message", "content_cleanup_rule"),
            default="&&[a-zA-Z]*&&",
        )
        # 表情附加触发范围：仅普通聊天 / 普通聊天及插件触发的 LLM
        self.trigger_scope = normalize_trigger_scope(
            self._read_config_value(
                ("generation", "trigger", "scope"),
                default="only_chat_llm",
            )
        )

        # 构建表情包提示词
        self.sys_prompt_add = ""
        self.persona_base_prompts = {}
        self._reload_personas()

        # 注册 WebUI API
        self._register_web_apis()

        self._semantic_initial_rebuild_task = None

    async def initialize(self) -> None:
        """Start collection on plugin reload as well as initial startup."""
        await self.auto_collect_manager.start()

    @filter.on_astrbot_loaded()
    async def _schedule_semantic_initial_rebuild(self):
        """等 AstrBot 核心和所有 Provider 加载完成后再安排首次静默重建。"""
        await self.auto_collect_manager.start()
        if not self.semantic_enabled:
            return
        task = self._semantic_initial_rebuild_task
        if task and not task.done():
            return
        self._semantic_initial_rebuild_task = asyncio.create_task(
            self._auto_rebuild_initial_pack()
        )

    @classmethod
    def _normalize_mixin_handler_module_paths(cls):
        """兼容尚未原生支持 Mixin 指令处理器的 AstrBot 版本。"""
        try:
            from astrbot.core.star.star_handler import star_handlers_registry
        except (ImportError, AttributeError):
            return

        plugin_package = cls.__module__.rsplit(".", 1)[0]
        mixin_module_prefix = f"{plugin_package}.mixins."
        adjusted_count = 0
        for handler_metadata in star_handlers_registry:
            if not handler_metadata.handler_module_path.startswith(
                mixin_module_prefix,
            ):
                continue
            handler_metadata.handler_module_path = cls.__module__
            adjusted_count += 1

        if adjusted_count:
            logger.info(f"已将 {adjusted_count} 个 Mixin 指令处理器绑定到插件主模块。")

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_upload_image(self, event: AstrMessageEvent):
        user_key = f"{event.session_id}_{event.get_sender_id()}"
        manual_upload_pending = user_key in self.upload_states
        async for result in self._handle_upload_image_impl(event):
            yield result
        if not manual_upload_pending:
            await self.auto_collect_manager.submit(event)

    @filter.on_waiting_llm_request(priority=99999)
    async def mark_llm_request_origin(self, event: AstrMessageEvent):
        return await self._mark_llm_request_origin_impl(event)

    @filter.on_llm_request(priority=99999)
    async def inject_meme_prompt(self, event: AstrMessageEvent, req: ProviderRequest):
        return await self._inject_meme_prompt_impl(event, req)

    @filter.on_llm_response(priority=99999)
    async def resp(self, event: AstrMessageEvent, response: LLMResponse):
        return await self._resp_impl(event, response)

    @filter.on_decorating_result(priority=99999)
    async def on_decorating_result(self, event: AstrMessageEvent):
        return await self._on_decorating_result_impl(event)

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        return await self._after_message_sent_impl(event)

    @llm_tool(name="search_memes")
    async def search_memes_tool(self, event: AstrMessageEvent, query: str):
        """Find optional meme images for the assistant's intended expression.

        Args:
            query(string): A short description of the assistant's intended emotion,
                attitude or action, including who it refers to. Search at most once
                per reply and only when an image would help.

        Returns:
            Candidates with IDs and actual image previews. Inspect the previews,
            not just their fallible captions. Search does not send images to the
            user; emit a selected candidate's complete ID between && for delivery.
        """
        if (
            not self._semantic_mode_active(event)
            or str(event.get_extra("meme_manager_semantic_mode") or "") != "tool"
        ):
            return dumps_result({"ok": False, "reason": "语义查询未启用或索引不可用"})
        if bool(event.get_extra("meme_manager_semantic_search_completed")):
            return dumps_result(
                {
                    "ok": False,
                    "reason": "本轮已经完成唯一一次搜索，请直接根据上次候选完成最终回复",
                }
            )
        event.set_extra("meme_manager_semantic_search_completed", True)
        provider_request = event.get_extra("provider_request")
        if provider_request is not None:
            self._remove_semantic_tool(provider_request)
        context = self._resolve_runtime_pack_context(event=event)
        if str(context.get("pack_id") or "") != str(
            event.get_extra("meme_manager_semantic_verified_pack_id") or ""
        ):
            return dumps_result({"ok": False, "reason": "当前语义图包已经变化"})
        try:
            result = await search_memes(
                context["pack_dir"],
                pack_paths.PLUGIN_DATA_DIR,
                str(context["pack_id"]),
                query,
                self._resolve_embedding_provider(str(context["pack_id"])),
                top_k=self.semantic_top_k,
                min_score=self.semantic_min_score,
                _verified_complete=True,
                exclude_hashes=self.meme_usage.exclusions(event_scope(event)),
            )
            records = candidate_records(
                context["pack_dir"], result.get("candidates") or []
            )
            event.set_extra("meme_manager_semantic_query", str(query or ""))
            visual_result = await asyncio.to_thread(
                visual_candidates, context["pack_dir"], records
            )
            visible_ids = {
                item["id"]
                for item in json.loads(visual_result.content[0].text)["candidates"]
            }
            remember_candidates(
                event, [item for item in records if item["id"] in visible_ids]
            )
            return visual_result
        except Exception as exc:
            logger.error("语义表情查询失败: %s", exc, exc_info=True)
            return dumps_result({"ok": False, "reason": "语义查询失败"})

    async def terminate(self):
        if getattr(self, "auto_collect_manager", None):
            await self.auto_collect_manager.close()
        install_tasks = list(getattr(self, "_community_install_tasks", set()))
        install_tasks.extend(getattr(self, "_community_update_tasks", set()))
        for task in install_tasks:
            task.cancel()
        if install_tasks:
            await asyncio.gather(*install_tasks, return_exceptions=True)
        initial_task = getattr(self, "_semantic_initial_rebuild_task", None)
        if initial_task and not initial_task.done():
            initial_task.cancel()
            await asyncio.gather(initial_task, return_exceptions=True)
        if self.img_sync:
            await asyncio.to_thread(self.img_sync.close)
        sync_monitor = getattr(self, "_img_host_sync_monitor", None)
        if sync_monitor:
            await asyncio.gather(sync_monitor, return_exceptions=True)
        if getattr(self, "semantic_task_manager", None):
            await self.semantic_task_manager.close()
        personas = self.context.provider_manager.personas
        self._sync_persona_base_prompts(personas)
        for index, persona in enumerate(personas):
            key = self._get_persona_key(persona, index)
            persona["prompt"] = self.persona_base_prompts[key]
