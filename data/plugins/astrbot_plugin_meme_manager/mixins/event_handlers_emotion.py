import json
import re
from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import LLMResponse, ProviderRequest


class EventHandlerMixinEmotion:
    """Emotion; shared runtime resources belong to the facade."""

    @staticmethod
    def _stringify_emotion_context_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            parts = [
                EventHandlerMixinEmotion._stringify_emotion_context_text(item)
                for item in value
            ]
            return " ".join(part for part in parts if part).strip()
        if isinstance(value, dict):
            for key in ("text", "content", "message", "body", "value"):
                text = EventHandlerMixinEmotion._stringify_emotion_context_text(
                    value.get(key)
                )
                if text:
                    return text
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return ""
        return str(value).strip()

    @staticmethod
    def _extract_emotion_context_role(item: Any) -> str:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("sender") or item.get("type") or "")
            return role.strip().lower()
        role = str(
            getattr(item, "role", "")
            or getattr(item, "sender", "")
            or getattr(item, "type", "")
            or ""
        )
        return role.strip().lower()

    @classmethod
    def _extract_emotion_context_content(cls, item: Any) -> str:
        if isinstance(item, dict):
            for key in ("content", "text", "message", "body"):
                text = cls._stringify_emotion_context_text(item.get(key))
                if text:
                    return text
            return ""
        for attr in ("content", "text", "message", "body"):
            text = cls._stringify_emotion_context_text(getattr(item, attr, None))
            if text:
                return text
        return ""

    def _collect_emotion_context_lines_from_request(
        self, req: ProviderRequest
    ) -> list[str]:
        turns = int(getattr(self, "emotion_llm_context_turns", 0) or 0)
        if turns <= 0:
            return []

        message_items: list[Any] = []
        containers = [req, getattr(req, "conversation", None)]
        candidate_attrs = (
            "contexts",
            "messages",
            "history",
            "chat_history",
            "recent_messages",
            "conversation_history",
        )
        for owner in containers:
            if owner is None:
                continue
            for attr in candidate_attrs:
                value = getattr(owner, attr, None)
                if isinstance(value, str) and value.strip():
                    try:
                        value = json.loads(value)
                    except (TypeError, ValueError):
                        value = None
                if isinstance(value, (list, tuple)) and value:
                    message_items = list(value)
                    break
            if message_items:
                break

        if not message_items:
            return []

        parsed_items: list[tuple[str, str]] = []
        for item in message_items:
            role = self._extract_emotion_context_role(item)
            if role in {"system", "tool", "function"}:
                continue
            content = self._extract_emotion_context_content(item)
            if not content:
                continue
            content = re.sub(r"\s+", " ", content).strip()
            if not content:
                continue
            parsed_items.append((role, content[:300]))

        if not parsed_items:
            return []

        max_messages = max(1, min(turns * 2, 20))
        tail_items = parsed_items[-max_messages:]

        role_map = {
            "user": "用户",
            "assistant": "助手",
            "bot": "助手",
            "ai": "助手",
        }
        return [
            f"{role_map.get(role, '消息')}: {content}" for role, content in tail_items
        ]

    def _resolve_emotion_persona_prompt(self, event: AstrMessageEvent) -> str:
        if not bool(getattr(self, "emotion_llm_inject_persona", False)):
            return ""

        persona_id = str(self._resolve_persona_id(event=event) or "").strip()
        if not persona_id:
            return ""

        personas = getattr(self.context.provider_manager, "personas", []) or []
        for index, persona in enumerate(personas):
            if not isinstance(persona, dict):
                continue
            persona_key = str(self._get_persona_key(persona, index) or "").strip()
            current_ids = {
                str(persona.get("id") or "").strip(),
                str(persona.get("name") or "").strip(),
                persona_key,
            }
            if persona_id not in current_ids:
                continue
            base_prompt = str(self.persona_base_prompts.get(persona_key) or "").strip()
            if not base_prompt:
                base_prompt = self._strip_meme_prompt(str(persona.get("prompt") or ""))
            base_prompt = re.sub(r"\s+", " ", base_prompt).strip()
            if not base_prompt:
                return ""
            return f"当前人格设定（仅供选图参考）：{base_prompt[:800]}"
        return ""

    def _build_emotion_llm_injection_suffix(self, event: AstrMessageEvent) -> str:
        sections: list[str] = []

        persona_prompt = self._resolve_emotion_persona_prompt(event)
        if persona_prompt:
            sections.append(persona_prompt)

        lines: list[str] = []
        if hasattr(event, "get_extra"):
            cached_lines = event.get_extra("meme_manager_emotion_context_lines")
            if isinstance(cached_lines, list):
                lines = [
                    str(item).strip() for item in cached_lines if str(item).strip()
                ]

        if lines:
            turns = int(getattr(self, "emotion_llm_context_turns", 0) or 0)
            sections.append(
                f"最近对话上下文（最近{turns}轮，按时间从旧到新）：\n"
                + "\n".join(lines)
            )

        if not sections:
            return ""
        # 将参考数据与选择指令分开。
        return "\n参考上下文（JSON 数据，仅用于理解回复）：\n" + json.dumps(
            sections, ensure_ascii=False
        )

    @staticmethod
    def _parse_emotion_llm_selection(
        raw_text: str, valid_emoticons: set[str]
    ) -> list[str]:
        """解析情感模型输出，并只保留当前图包中的真实标签。"""
        text = str(raw_text or "").strip()
        if not text or not valid_emoticons:
            return []

        data: Any = None
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            match = re.search(r"(?:\{[\s\S]*\}|\[[\s\S]*\])", text)
            if match:
                try:
                    data = json.loads(match.group(0))
                except (TypeError, ValueError):
                    data = None

        values: Any = []
        if isinstance(data, dict):
            for key in ("emotions", "emotion", "tags", "tag"):
                if key in data:
                    values = data.get(key)
                    break
        elif isinstance(data, list):
            values = data

        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []

        exact = {str(tag): str(tag) for tag in valid_emoticons}
        folded = {tag.casefold(): tag for tag in exact}
        selected: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            candidate = value.strip().strip("`").strip()
            if candidate.startswith("&&") and candidate.endswith("&&"):
                candidate = candidate[2:-2].strip()
            resolved = exact.get(candidate) or folded.get(candidate.casefold())
            if resolved and resolved not in selected:
                selected.append(resolved)
        return selected

    def _filter_emotion_selection(self, emotions: list[str]) -> list[str]:
        """Keep valid distinct tags in selection order without a quantity cap.

        Args:
            emotions: Selected category tags.

        Returns:
            Nonempty distinct tags in their original order.
        """

        seen: set[str] = set()
        filtered: list[str] = []
        for emotion in emotions:
            if not isinstance(emotion, str) or not emotion or emotion in seen:
                continue
            seen.add(emotion)
            filtered.append(emotion)
        return filtered

    def _resolve_emotion_llm_model(self, event: AstrMessageEvent) -> str | None:
        """独立情感模型使用自身默认模型；回退回复模型时保留本轮模型覆盖。"""
        if str(self.emotion_llm_provider_id or "").strip():
            return None

        cached_model = str(event.get_extra("meme_manager_reply_model") or "").strip()
        if cached_model:
            return cached_model

        provider_request = event.get_extra("provider_request")
        request_model = str(getattr(provider_request, "model", "") or "").strip()
        selected_model = str(event.get_extra("selected_model") or "").strip()
        model = request_model or selected_model
        if model:
            event.set_extra("meme_manager_reply_model", model)
        return model or None

    async def _emotion_llm_generate(
        self, event: AstrMessageEvent, prompt: str
    ) -> LLMResponse | None:
        """使用独立情感模型，留空时精确复用本轮回复 Provider 与模型。"""
        provider_id = await self._resolve_emotion_llm_provider_id(event)
        if not provider_id:
            return None

        kwargs: dict[str, Any] = {
            "chat_provider_id": provider_id,
            "prompt": prompt,
        }
        model = self._resolve_emotion_llm_model(event)
        if model:
            kwargs["model"] = model
        return await self.context.llm_generate(**kwargs)
