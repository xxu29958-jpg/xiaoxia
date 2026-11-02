"""Subtitle discovery uses existing scoped storage and the guarded sender."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.bili_discovery import BiliDiscovery
from astrbot_plugin_ada_companion.initiative_materials import InterestMaterials
from astrbot_plugin_ada_companion.policy import DeliveryPolicy
from astrbot_plugin_ada_companion.web_notebook import WebNotebook

from maintenance.tests import test_contact_channels as channels

BV = "BV1xx411c7mD"
URL = "https://www.bilibili.com/video/" + BV
QUOTE = "蜗牛的齿舌上排列着许多细小的牙齿。"


@pytest.fixture
def discovery(tmp_path):
    config = {"bili_discovery_enabled": True, "bili_topics": ["科普"]}
    owners = SimpleNamespace(scope=lambda event: "owner")
    models = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "picks": [
                            {
                                "bvid": BV,
                                "source_quote": QUOTE,
                                "comment": "这嘴里的工具箱还挺精细。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        )
    )
    worker = BiliDiscovery(
        config,
        owners,
        WebNotebook(tmp_path),
        SimpleNamespace(plugin=lambda name: None),
        models,
    )
    worker.reader.search = AsyncMock(
        return_value={"ok": True, "videos": [{"bvid": BV, "url": URL, "title": "蜗牛"}]}
    )
    worker.reader.read = AsyncMock(
        return_value={
            "ok": True,
            "bvid": BV,
            "url": URL,
            "title": "蜗牛",
            "author": "科普",
            "evidence": "subtitles",
            "transcript": QUOTE,
            "truncated": False,
        }
    )
    return worker


@pytest.mark.asyncio
async def test_scanned_evidence_reaches_existing_materials_without_sending(
    discovery, tmp_path
):
    report = await discovery.scan("owner", automatic=True)
    assert report["ok"] and len(report["saved"]) == 1
    policy = DeliveryPolicy(tmp_path / "state.json", "wechat:FriendMessage:owner")
    candidates = InterestMaterials(discovery.store, policy, "owner").candidates()
    assert len(candidates) == 1
    assert candidates[0]["source_quote"] == QUOTE
    assert candidates[0]["url"] == URL
    assert "未观看" in candidates[0]["next_step"]
    assert not InterestMaterials(discovery.store, policy, "stranger").candidates()
    assert not policy.data["deliveries"]
    assert discovery.status("owner")["latest"] == report
    assert discovery.status("stranger")["latest"] is None
    payload = json.loads(discovery.models.generate.await_args.kwargs["prompt"])
    assert payload[0]["transcript"] == QUOTE
    assert discovery.models.generate.await_args.kwargs["contexts"] == []


@pytest.mark.asyncio
async def test_metadata_only_is_skipped_not_summarized(discovery):
    discovery.reader.read.return_value.update(
        evidence="metadata_only", transcript="", error="subtitles_unavailable"
    )
    report = await discovery.scan("owner")
    assert report["ok"] and not report["saved"]
    assert report["skipped"][0]["reason"] == "subtitles_unavailable"
    discovery.models.generate.assert_not_awaited()
    assert discovery.store.notes("owner", "list")["notes"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "completion",
    [
        '{"picks":[{"bvid":"BV1xx411c7mD","source_quote":"这是根本不存在于字幕里的编造内容","comment":"假的"}]}',
        '{"picks":"bad"}',
        "[]",
        '{"picks":[null]}',
        "not json",
    ],
)
async def test_invented_or_malformed_selection_never_saves(discovery, completion):
    discovery.models.generate.return_value.completion_text = completion
    report = await discovery.scan("owner")
    assert not report["saved"]
    assert discovery.store.notes("owner", "list")["notes"] == []


@pytest.mark.asyncio
async def test_failed_scan_consumes_durable_cooldown(discovery):
    discovery.reader.search.side_effect = OSError("offline")
    report = await discovery.scan("owner", automatic=True)
    assert not report["ok"]
    restored = BiliDiscovery(
        discovery.config,
        discovery.owners,
        discovery.store,
        SimpleNamespace(plugin=lambda _: None),
        discovery.models,
    )
    restored.reader.search = AsyncMock()
    result = await restored.scan("owner", automatic=True)
    assert not result["ok"] and "冷却" in result["error"]
    restored.reader.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_discovery_never_touches_network(discovery):
    discovery.config["bili_discovery_enabled"] = False
    assert (await discovery.scan("owner", automatic=True))["reason"] == "disabled"
    discovery.reader.search.assert_not_awaited()
    discovery.store.set_enabled(False)
    assert (await discovery.scan("owner"))["reason"] == "disabled"
    discovery.reader.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_during_selection_does_not_publish(discovery):
    async def generate(**kwargs):
        discovery.config["bili_discovery_enabled"] = False
        return SimpleNamespace(
            completion_text=json.dumps(
                {"picks": [{"bvid": BV, "source_quote": QUOTE, "comment": "不应保存"}]}
            )
        )

    discovery.models.generate.side_effect = generate
    report = await discovery.scan("owner", automatic=True)
    assert report["reason"] == "disabled"
    assert not discovery.store.notes("owner", "list")["notes"]


@pytest.mark.asyncio
async def test_cancelled_scan_propagates_and_reserves_attempt(discovery):
    discovery.reader.search.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await discovery.scan("owner")
    assert not discovery.lock.locked()
    result = await discovery.scan("owner")
    assert not result["ok"] and "冷却" in result["error"]


@pytest.mark.asyncio
async def test_stop_cancels_read_without_killing_parent_or_publishing(discovery):
    started = asyncio.Event()

    async def waiting(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    discovery.reader.read.side_effect = waiting
    task = asyncio.create_task(discovery.scan("owner"))
    await asyncio.wait_for(started.wait(), 2)
    await discovery.stop("owner")
    assert (await task)["reason"] == "stopped"
    assert not discovery.active
    assert not discovery.lock.locked()
    assert not discovery.store.notes("owner", "list")["notes"]


@pytest.mark.asyncio
async def test_existing_owner_note_is_preserved_and_not_reread(discovery):
    discovery.store.save_visit(
        "owner",
        {"page_id": "manual", "url": URL, "title": "my note", "text": QUOTE},
        "",
    )
    discovery.store.notes(
        "owner", "save", "manual", source_quote=QUOTE, comment="我自己写的感想"
    )
    before = discovery.store.notes("owner", "list")
    result = await discovery.scan("owner")
    assert not result["saved"]
    discovery.reader.read.assert_not_awaited()
    assert discovery.store.notes("owner", "list") == before


@pytest.mark.asyncio
async def test_four_rounds_per_day_even_when_no_videos(discovery):
    from unittest.mock import patch

    discovery.reader.search.return_value = {"ok": True, "videos": []}
    for i in range(4):
        with patch(
            "astrbot_plugin_ada_companion.bili_discovery.time.time",
            return_value=2_000_000 + i * 61,
        ):
            assert (await discovery.scan("owner"))["ok"]
    with patch(
        "astrbot_plugin_ada_companion.bili_discovery.time.time",
        return_value=2_000_000 + 5 * 61,
    ):
        result = await discovery.scan("owner")
    assert not result["ok"] and "上限" in result["error"]
    assert discovery.reader.search.await_count == 4


@pytest.mark.asyncio
async def test_note_added_during_selection_is_preserved(discovery):
    original = discovery.models.generate.return_value

    async def generate(**kwargs):
        discovery.store.save_visit(
            "owner",
            {"page_id": "concurrent", "url": URL, "title": "mine", "text": QUOTE},
            "",
        )
        discovery.store.notes(
            "owner", "save", "concurrent", source_quote=QUOTE, comment="主人刚写下的"
        )
        return original

    discovery.models.generate.side_effect = generate
    result = await discovery.scan("owner")
    assert not result["saved"]
    assert (
        discovery.store.notes("owner", "list")["notes"][0]["comment"] == "主人刚写下的"
    )


@pytest.mark.asyncio
async def test_slow_discovery_uses_current_selection_time():
    from unittest.mock import patch

    case = channels.ContactChannelTests()
    case.setUp()
    try:
        p = case.plugin
        p.config["bili_discovery_enabled"] = True
        now = [channels.NOW]

        async def scan(*args, **kwargs):
            now[0] += 65
            return {"ok": True}

        p.bili.scan = scan
        p.initiative._candidate = AsyncMock(
            return_value={"send": False, "text": "", "thread_id": "", "meme_query": ""}
        )
        with patch("time.time", side_effect=lambda: now[0]):
            await p.initiative.proactive(manual=True)
        assert p.initiative._candidate.await_args.args[3] == channels.NOW + 65
    finally:
        case.doCleanups()


@pytest.mark.asyncio
async def test_actual_proactive_gate_preview_and_stale_activity():
    case = channels.ContactChannelTests()
    case.setUp()
    try:
        p = case.plugin
        p.config["bili_discovery_enabled"] = True
        p.bili.scan = AsyncMock(return_value={"ok": True, "saved": []})
        await p.initiative.proactive(preview=True)
        p.bili.scan.assert_not_awaited()
        p.policy.data["enabled"] = False
        await p.initiative.proactive()
        p.bili.scan.assert_not_awaited()
        p.policy.data["enabled"] = True

        async def changed(*args, **kwargs):
            p.policy.data["last_user"] += 1
            return {"ok": True}

        p.bili.scan.side_effect = changed
        result = await p.initiative.proactive(manual=True)
        assert result["reason"] == "context_changed"
        case.context.send_message.assert_not_awaited()
    finally:
        case.doCleanups()


@pytest.mark.asyncio
async def test_actual_tool_owner_scope_and_private_tool_isolation():
    case = channels.ContactChannelTests()
    case.setUp()
    try:
        p = case.plugin
        p.bili.reader.search = AsyncMock(return_value={"ok": True, "videos": []})
        for origin in (channels.QQ, channels.WX):
            result = json.loads(await p.bili.tool(case.event(origin), "search", "科普"))
            assert result["ok"]
        denied = json.loads(
            await p.bili.tool(
                case.event("wechat-companion:FriendMessage:stranger"), "search", "科普"
            )
        )
        assert not denied["ok"]
        private_query = json.loads(
            await p.bili.tool(case.event(channels.QQ), "search", "私人记忆里的姓名地址")
        )
        assert not private_query["ok"]
        assert p.bili.reader.search.await_count == 2
        from astrbot_plugin_ada_companion.tool_policy import (
            WEB_READ_TOOLS,
            WEB_REPLY_TOOLS,
        )

        assert "ada_bili" in WEB_READ_TOOLS and "ada_bili" in WEB_REPLY_TOOLS
    finally:
        case.doCleanups()
