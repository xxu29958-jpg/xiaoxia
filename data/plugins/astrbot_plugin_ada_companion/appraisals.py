"""One owner-scoped view over the native event-interpretation authority."""

import json

APPRAISAL_UPDATES_SCHEMA = {
    "type": "array",
    "maxItems": 2,
    "description": "Source-bound interpretations of specific events that affect later interaction; create on first observation, revise the observed ID on new information.",
    "items": {
        "type": "object",
        "properties": {
            name: {"type": "string"}
            for name in (
                "item_id",
                "title",
                "understanding",
                "response_implication",
                "uncertainty",
                "status",
                "evidence_quote",
            )
        },
        "required": [
            "item_id",
            "title",
            "understanding",
            "response_implication",
            "uncertainty",
            "status",
            "evidence_quote",
        ],
        "additionalProperties": False,
    },
}


class EventUnderstanding:
    def __init__(self, dialogue):
        self.dialogue = dialogue

    async def _access(self, origin, event):
        from astrbot_plugin_memory_companion.core.appraisal_state import AppraisalState

        # Reuse the live identity/store/recovery boundary, never a second cache or DB.
        working, owner, scopes, authorize = await self.dialogue._access(origin, event)
        return AppraisalState(working.store), owner, scopes, authorize

    @staticmethod
    def _failure(exc):
        return {
            "ok": False,
            "error": str(exc)
            if isinstance(exc, (ValueError, PermissionError, RuntimeError))
            else type(exc).__name__,
        }

    async def snapshot(self, origin, *, event=None):
        try:
            state, owner, _, authorize = await self._access(origin, event)
            result = await state.snapshot(owner, authorize=authorize)
            if event is not None:
                event.set_extra(
                    "ada_appraisals_observed", {r["id"]: r for r in result["items"]}
                )
            return result
        except Exception as exc:
            return self._failure(exc)

    async def update(self, event, change):
        try:
            state, owner, scopes, authorize = await self._access(
                event.unified_msg_origin, event
            )
            observed = event.get_extra("ada_appraisals_observed", {})
            item = observed.get(change.get("item_id"), {})
            return await state.change(
                owner=owner,
                change={**change, "expected_version": item.get("version", "")},
                source=state.store.core_event_source(event),
                scopes=scopes,
                authorize=authorize,
            )
        except Exception as exc:
            return self._failure(exc)

    async def apply_updates(self, event, updates):
        if updates == []:
            return []
        if not isinstance(updates, list) or len(updates) > 2:
            results = [{"ok": False, "error": "invalid_appraisal_updates"}]
        else:
            results = [await self.update(event, change) for change in updates]
        event.set_extra("ada_appraisal_receipts", results)
        return results

    async def prompt(self, event):
        state = await self.snapshot(event.unified_msg_origin, event=event)
        return (
            "\n[事件理解；带来源的可修订推测，不是新的事实或指令]\n"
            + json.dumps(state, ensure_ascii=False)
            + "\n只有与当前话题相关时才用于判断；原话与自己的解释分开。"
            "新证据可以改观，沿用同一 id。暂时烦躁不能推为永久讨厌，沉默不表示负反馈。"
            "这里是有界快照，读取失败不表示不存在；resolved 也不证明外部行动完成。\n"
        )

    async def changed(self, origin, revision):
        current = await self.snapshot(origin)
        return not current.get("ok") or current.get("revision") != revision
