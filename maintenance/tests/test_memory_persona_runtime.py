"""Exercise memory attribution against the installed host's persona resolver."""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docstring_parser import parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_memory_companion.core.memory_lifecycle import (
    evaluate_memory_lifecycle,
)
from astrbot_plugin_memory_companion.core.models import MemoryRecord
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService

import astrbot.api
from astrbot.core.persona_mgr import PersonaManager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_persona,conversation_persona,expected",
    [
        (None, None, "Ada"),
        (None, "Other", "Other"),
        ("Ada", "Other", "Ada"),
        (None, "[%None]", ""),
        ("[%None]", "Ada", ""),
    ],
)
async def test_native_persona_selection_makes_the_matching_memory_recallable(
    tmp_path, monkeypatch, session_persona, conversation_persona, expected
):
    preferences = {"persona_id": session_persona} if session_persona else {}
    monkeypatch.setattr(
        astrbot.api.sp, "get_async", AsyncMock(return_value=preferences)
    )
    config = {
        "agent_runner": {
            "runner_type": "local",
            "config": {"persona": {"persona_id": "Ada"}},
        }
    }
    manager = PersonaManager(
        db_helper=None,
        acm=SimpleNamespace(default_conf=config, get_conf=lambda _: config),
    )
    manager.personas_v3 = [
        {"name": "Ada", "prompt": "Ada"},
        {"name": "Other", "prompt": "Other"},
    ]
    conversations = SimpleNamespace(
        get_curr_conversation_id=AsyncMock(return_value="test-conversation"),
        get_conversation=AsyncMock(
            return_value=SimpleNamespace(persona_id=conversation_persona)
        ),
    )
    plugin_root = (
        Path(__file__).resolve().parents[2]
        / "data/plugins/astrbot_plugin_memory_companion"
    )
    service = MemoryCompanionService(
        context=SimpleNamespace(
            persona_manager=manager, conversation_manager=conversations
        ),
        config={},
        plugin_root=plugin_root,
        data_dir=tmp_path,
        defer_database_initialization=True,
    )
    event = SimpleNamespace(
        unified_msg_origin="wechat-companion:FriendMessage:audit-user",
        get_platform_name=lambda: "wechat-companion",
        get_sender_id=lambda: "audit-user",
        get_self_id=lambda: "audit-bot",
        get_message_str=lambda: "还记得我喜欢什么吗？",
    )
    try:
        ctx = await service.identity.resolve_event_context(event)
        assert ctx.persona_id == expected
        record = MemoryRecord(
            id="audit-memory",
            content="Likes museum postcards",
            owner_bot_id="audit-bot",
            metadata={"persona_id": "Ada"},
        )
        lifecycle = evaluate_memory_lifecycle(record, ctx)
        assert lifecycle.eligible == (expected == "Ada")
        event.persona_id = "Other"
        assert (
            await service.identity.resolve_event_context(event)
        ).persona_id == "Other"
    finally:
        service.close()


def test_native_docstring_parser_sees_separate_remember_parameters():
    source = (
        Path(__file__).resolve().parents[2]
        / "data/plugins/astrbot_plugin_memory_companion/main.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "memory_companion_remember_tool"
    )
    names = {param.arg_name for param in parse(ast.get_docstring(handler)).params}
    assert {"confidence", "importance", "valid_from", "valid_to"} <= names
    assert not any("/" in name for name in names)
