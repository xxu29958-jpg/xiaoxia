import asyncio
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest

from .backend.packs.images import IMAGE_EXTENSIONS
from .backend.semantic.models import runtime_category_mapping
from .config import (
    DEFAULT_CATEGORY_DESCRIPTIONS,
)
from .utils import dict_to_string, load_json


class MemeSenderPersonas:
    """Personas; shared runtime resources belong to the facade."""

    def _resolve_persona_id(
        self, event: AstrMessageEvent | None = None, req: ProviderRequest | None = None
    ) -> str | None:
        """Read the effective persona cached before selecting a resource pack.

        Args:
            event: Message event carrying the resolved persona for this request.
            req: Provider request used when no resolved persona is available.

        Returns:
            The effective persona ID, or None when no persona is selected.
        """
        if event is not None and hasattr(event, "get_extra"):
            resolved_persona = event.get_extra("meme_manager_resolved_persona_id")
            if resolved_persona is not None:
                return str(resolved_persona).strip() or None
        if req and req.conversation:
            persona_id = str(getattr(req.conversation, "persona_id", "") or "").strip()
            if persona_id:
                return persona_id
        if event is None:
            return None
        persona_id = str(getattr(event, "persona_id", "") or "").strip()
        if persona_id:
            return persona_id
        if hasattr(event, "get_extra"):
            persona_id = str(event.get_extra("persona_id") or "").strip()
            if persona_id:
                return persona_id
        return None

    def _resolve_runtime_pack_context(
        self, event: AstrMessageEvent | None = None, req: ProviderRequest | None = None
    ) -> dict:
        from .backend.packs.resolver import resolve_pack_context

        session_id = ""
        if req:
            session_id = str(req.session_id or "").strip()
        if not session_id and event is not None:
            session_id = str(getattr(event, "session_id", "") or "").strip()
        persona_id = self._resolve_persona_id(event=event, req=req)
        context = resolve_pack_context(
            session_id=session_id or None, persona_id=persona_id
        )
        if event is not None and hasattr(event, "set_extra"):
            event.set_extra(
                "meme_manager_runtime_memes_dir",
                str(context["memes_dir"]),
            )
            event.set_extra(
                "meme_manager_runtime_pack_id", str(context.get("pack_id") or "")
            )
        return context

    def _get_runtime_memes_dir_for_event(self, event: AstrMessageEvent):
        if hasattr(event, "get_extra"):
            runtime_memes_dir = str(
                event.get_extra("meme_manager_runtime_memes_dir") or ""
            ).strip()
            if runtime_memes_dir:
                return runtime_memes_dir
        return str(self._resolve_runtime_pack_context(event=event)["memes_dir"])

    def _get_manageable_categories(self) -> set[str]:
        return (
            set(self.category_manager.get_descriptions())
            | self.category_manager.get_local_categories()
        )

    def _ensure_default_category_descriptions(self, categories: list[str]) -> None:
        existing_descriptions = self.category_manager.get_descriptions()
        updated = False
        for category in categories:
            if category in existing_descriptions:
                continue
            default_description = DEFAULT_CATEGORY_DESCRIPTIONS.get(category)
            if not default_description:
                continue
            if self.category_manager.update_description(category, default_description):
                existing_descriptions[category] = default_description
                updated = True
        if updated:
            self._reload_personas()

    def _get_persona_key(self, persona: dict, index: int) -> str:
        return str(persona.get("name") or persona.get("id") or index)

    def _sync_persona_base_prompts(self, personas: list[dict]) -> None:
        active_keys = set()
        for index, persona in enumerate(personas):
            key = self._get_persona_key(persona, index)
            active_keys.add(key)
            self.persona_base_prompts[key] = self._strip_meme_prompt(
                persona.get("prompt", "")
            )
        for key in set(self.persona_base_prompts) - active_keys:
            del self.persona_base_prompts[key]

    def _apply_persona_prompts(self) -> None:
        personas = self.context.provider_manager.personas
        self._sync_persona_base_prompts(personas)
        self.sys_prompt_add = ""
        for index, persona in enumerate(personas):
            key = self._get_persona_key(persona, index)
            persona["prompt"] = self.persona_base_prompts[key]

    def _apply_request_prompt(
        self, req: ProviderRequest, event: AstrMessageEvent | None = None
    ) -> None:
        semantic_mode = ""
        semantic_tool_ready = False
        if self.emotion_llm_enabled:
            if self._semantic_pack_ready(event=event, req=req):
                semantic_mode = "llm"
        else:
            semantic_tool_ready = self._semantic_pack_ready(
                event=event, req=req, require_tool=True
            )
            if semantic_tool_ready:
                semantic_mode = "tool"
        semantic_active = bool(semantic_mode)
        if event is not None and hasattr(event, "set_extra"):
            event.set_extra("meme_manager_semantic_active", semantic_active)
            event.set_extra("meme_manager_semantic_mode", semantic_mode)
            event.set_extra(
                "meme_manager_semantic_verified_pack_id",
                str(event.get_extra("meme_manager_runtime_pack_id") or "")
                if semantic_active
                else "",
            )
            event.set_extra("meme_manager_semantic_selected_ids", [])
            event.set_extra("meme_manager_semantic_candidates", {})
            event.set_extra("meme_manager_semantic_query", "")
            event.set_extra("meme_manager_semantic_search_completed", False)
            event.set_extra("meme_manager_semantic_response_processed", False)
            event.set_extra("meme_manager_image_only_reply", False)
            event.set_extra("meme_manager_stream_filtered", False)
            event.set_extra("meme_manager_reply_provider_id", "")
            event.set_extra("meme_manager_reply_model", "")
        if semantic_mode == "tool":
            req.system_prompt = (
                self._strip_meme_prompt(req.system_prompt)
                + self._semantic_system_prompt()
            )
            return
        self._remove_semantic_tool(req)
        if semantic_mode == "llm" or self.emotion_llm_enabled:
            req.system_prompt = self._strip_meme_prompt(req.system_prompt)
            req.system_prompt += (
                "\n[Automatic sticker delivery]\n"
                "A separate visual selector can attach a fitting real sticker to this reply. "
                "Most replies stay text only; spontaneous stickers require a fresh emotional "
                "beat and shared spacing, not just a friendly tone. Explicit requests still work. "
                "Reply naturally without image IDs, markers or selection narration. "
                "Do not claim an image has been sent or that image delivery is unavailable. "
                "The delivery stage decides from actual images and the user's preferences. "
                "For a simple sticker request, this stage searches the existing library "
                "automatically; do not start a web search or collection task. "
                "For an image-only request, keep any text brief; successful selection removes it.\n"
            )
            return
        pack_context = self._resolve_runtime_pack_context(event=event, req=req)
        context_mapping = pack_context.get("category_mapping")
        category_mapping = (
            runtime_category_mapping(context_mapping)
            if isinstance(context_mapping, dict)
            else runtime_category_mapping(self.category_mapping)
        )
        # 首次安装时即使没有可用图片，也可能已有分类描述。
        memes_dir = Path(pack_context["memes_dir"])
        category_mapping = {
            key: description
            for key, description in category_mapping.items()
            if (memes_dir / key).is_dir()
            and any(
                image.is_file() and image.suffix.lower() in IMAGE_EXTENSIONS
                for image in (memes_dir / key).iterdir()
            )
        }
        req.system_prompt = self._strip_meme_prompt(req.system_prompt)
        if not category_mapping:
            return
        category_mapping_string = dict_to_string(category_mapping)
        sys_prompt_add = self._build_meme_prompt(category_mapping_string)
        req.system_prompt = self._strip_meme_prompt(
            req.system_prompt
        ) + self._wrap_meme_prompt(sys_prompt_add)

    def _reload_personas(self):
        pack_context = self._resolve_runtime_pack_context()
        context_mapping = pack_context.get("category_mapping")
        self.category_mapping = runtime_category_mapping(
            context_mapping
            if isinstance(context_mapping, dict)
            else load_json(pack_context["metadata_path"], DEFAULT_CATEGORY_DESCRIPTIONS)
        )
        self.category_mapping_string = dict_to_string(self.category_mapping)
        self._apply_persona_prompts()

    async def reload_emotions(self):
        try:
            await asyncio.to_thread(self.category_manager.sync_with_filesystem)
            self._reload_personas()
        except Exception as e:
            logger.error(f"重新加载表情配置失败: {str(e)}")
