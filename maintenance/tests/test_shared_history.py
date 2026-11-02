"""Verify original cross-channel evidence, pagination and identity boundaries."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2] / "data/plugins/astrbot_plugin_ada_companion"
spec = importlib.util.spec_from_file_location(
    "shared_history", ROOT / "shared_history.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
read_shared_history = module.read_shared_history
owner_spec = importlib.util.spec_from_file_location(
    "owner_memory", ROOT / "owner_memory.py"
)
owner_module = importlib.util.module_from_spec(owner_spec)
owner_spec.loader.exec_module(owner_module)


class SharedHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.qq, self.wx = "qq:FriendMessage:owner", "wx:FriendMessage:owner-wx"
        contexts = {}
        for origin in (self.qq, self.wx):
            contexts[origin] = {
                "session_id": origin,
                "scope": "private",
                "platform": origin.split(":")[0],
                "user_id": origin.split(":")[-1],
                "bot_id": "companion",
            }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.links = owner_module.OwnerMemory(
            Path(self.temp.name) / "owners.json", [self.qq, self.wx]
        )
        self.links.contexts = contexts

    def row(self, origin, index, text, kind="user_message"):
        user = origin.split(":")[-1]
        return {
            "id": str(index),
            "session_id": origin,
            "scope": "private",
            "event_type": kind,
            "subject_id": user if kind == "user_message" else "companion",
            "object_id": user,
            "content": text,
            "occurred_at": f"2026-10-18T10:{index:02}:00Z",
            "metadata": json.dumps(
                {"platform": origin.split(":")[0], "owner_bot_id": "companion"}
            ),
        }

    async def test_original_game_agreement_is_read_on_both_channels(self):
        async def rows(**kw):
            if kw["session_id"] == self.wx:
                return [
                    self.row(self.wx, 1, "我们一起玩星露谷"),
                    self.row(self.wx, 2, "先看看能不能连接", "bot_response"),
                ]
            return [self.row(self.qq, 3, "换到QQ了")]

        store = SimpleNamespace(
            source_revision=AsyncMock(return_value="r1"),
            recent_timeline=AsyncMock(side_effect=rows),
        )
        for origin in self.links.owners:
            result = await read_shared_history(self.links, origin, store)
            self.assertTrue(result["complete"])
            self.assertTrue(
                any(
                    x["text"] == "我们一起玩星露谷" and x["role"] == "user"
                    for x in result["items"]
                )
            )
            self.assertTrue(any(x["role"] == "assistant" for x in result["items"]))
        self.assertTrue(
            all(
                c.kwargs["scope"] == "private"
                for c in store.recent_timeline.await_args_list
            )
        )

    async def test_strangers_groups_and_foreign_rows_are_not_shared(self):
        stranger = self.row("qq:FriendMessage:stranger", 1, "private foreign fact")
        foreign_bot = self.row(self.qq, 2, "foreign bot")
        foreign_bot["metadata"] = json.dumps(
            {"platform": "qq", "owner_bot_id": "other-bot"}
        )
        store = SimpleNamespace(
            source_revision=AsyncMock(return_value="r1"),
            recent_timeline=AsyncMock(return_value=[stranger, foreign_bot]),
        )
        for origin in ("qq:FriendMessage:stranger", "qq:GroupMessage:owner"):
            self.assertFalse(
                (await read_shared_history(self.links, origin, store))["ok"]
            )
        store.recent_timeline.assert_not_awaited()
        self.assertEqual(
            (await read_shared_history(self.links, self.wx, store))["items"], []
        )

    async def test_pagination_preserves_every_long_message_part(self):
        long_text = "a" * 3500

        async def rows(**kw):
            return (
                [self.row(self.wx, 1, long_text)] if kw["session_id"] == self.wx else []
            )

        store = SimpleNamespace(
            source_revision=AsyncMock(return_value="r1"),
            recent_timeline=AsyncMock(side_effect=rows),
        )
        result = await read_shared_history(self.links, self.qq, store, budget=1600)
        found = result["items"][:]
        while result["next_cursor"]:
            result = await read_shared_history(
                self.links, self.qq, store, budget=1600, **result["next_cursor"]
            )
            found.extend(result["items"])
        self.assertEqual(
            "".join(x["text"] for x in sorted(found, key=lambda x: x["part"])),
            long_text,
        )

    async def test_store_failure_is_reported_not_called_no_memory(self):
        store = SimpleNamespace(
            source_revision=AsyncMock(return_value="r1"),
            recent_timeline=AsyncMock(side_effect=OSError("unavailable")),
        )
        result = await read_shared_history(self.links, self.qq, store)
        self.assertFalse(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["errors"]), 2)

    async def test_new_message_invalidates_continuation_instead_of_silently_skipping(
        self,
    ):
        store = SimpleNamespace(
            source_revision=AsyncMock(return_value="r2"), recent_timeline=AsyncMock()
        )
        result = await read_shared_history(
            self.links, self.qq, store, offset=3, revision="r1"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "history_changed_restart_query")
        store.recent_timeline.assert_not_awaited()


class ToolHistoryTests(unittest.TestCase):
    def test_memory_cleanup_preserves_tool_results_for_dict_and_message(self):
        sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / "data/plugins")]
        from astrbot_plugin_memory_companion.core.astrbot_compat import (
            remove_temp_text,
            sanitize_request_history,
        )

        from astrbot.core.agent.message import Message

        header, footer = "<MemoryCompanion-Context>", "</MemoryCompanion-Context>"
        result = json.dumps({"memories": header + "original memory" + footer})
        for as_model in (False, True):
            tool = {"role": "tool", "tool_call_id": "call-recall", "content": result}
            tool = Message.model_validate(tool) if as_model else tool
            req = SimpleNamespace(
                contexts=[
                    {
                        "role": "user",
                        "content": header + "temporary injection" + footer,
                    },
                    tool,
                ],
                prompt="next question",
                extra_user_content_parts=[],
            )
            self.assertEqual(remove_temp_text(req, header, footer), 1)
            sanitize_request_history(req)
            self.assertEqual(req.contexts, [tool])
            self.assertEqual(tool.content if as_model else tool["content"], result)


if __name__ == "__main__":
    unittest.main()
