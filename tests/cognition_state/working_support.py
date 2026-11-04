"""The same real native store, events and owner tools used by working-state tests."""

import json

from astrbot_plugin_ada_companion.dialogue import DialogueState
from support import QQ, Harness


class WorkingHarness(Harness):
    def __init__(self, test):
        super().__init__(test)
        self.dialogue = DialogueState(
            self.folder / "dialogue.json", self.links, self.tools.services
        )
        setattr(self.tools, "dialogue", self.dialogue)

    async def working(self, source=None, **kwargs):
        read = kwargs.pop("read", True)
        source = source or self.first
        self.links.observe(source)
        if read:
            await self.dialogue.snapshot(
                source.unified_msg_origin, event=source, include_closed=True
            )
        return json.loads(await self.tools.threads(source, **kwargs))

    async def items(self, **kwargs):
        result = await self.dialogue.snapshot(QQ, **kwargs)
        if not result["ok"]:
            raise AssertionError(result)
        return result["items"]


def legacy_record(item_id, title="后续话题"):
    return {
        "id": item_id,
        "kind": "topic",
        "title": title,
        "content": "旧的后续约定",
        "evidence_quote": "下次继续聊",
        "created_at": 100,
        "updated_at": 101,
        "expires_at": 9999999999,
        "status": "open",
    }
