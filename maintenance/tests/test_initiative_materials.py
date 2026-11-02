"""Notebook-to-sender journeys with durable reservations, scoped facts and stale work."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.initiative_materials import InterestMaterials
from astrbot_plugin_ada_companion.policy import DeliveryPolicy
from astrbot_plugin_ada_companion.web_notebook import WebNotebook

from maintenance.tests import test_contact_channels as channels

NOW, QQ, WX = channels.NOW, channels.QQ, channels.WX


@pytest.fixture
def flow():
    case = channels.ContactChannelTests()
    case.setUp()
    yield case
    case.doCleanups()


def save_note(flow, scope=None, suffix="one"):
    p = flow.plugin
    scope = scope or p.owner_memory.shared_scope
    store = p.public_browser.store
    store.save_visit(
        scope,
        {
            "page_id": suffix,
            "url": "https://example.org/" + suffix,
            "title": "星空小实验",
            "text": "拖动可以改变星轨。",
        },
        flow.folder / "page.jpg",
    )
    with patch("astrbot_plugin_ada_companion.web_notebook.datetime") as clock:
        clock.now.return_value = datetime.fromtimestamp(NOW, timezone.utc)
        note = store.notes(
            scope,
            "save",
            suffix,
            source_quote="拖动可以改变星轨。",
            comment="这拖尾看着像面条",
            next_step="下次试试不同转速",
        )
        store.bookmarks(scope, "save", suffix, note="留着下次玩")
    return note["id"]


def choose(flow, *, during=None):
    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        material = payload["fresh_web_discoveries"][0]
        assert material["source_quote"] == "拖动可以改变星轨。"
        assert material["personal_comment"] == "这拖尾看着像面条"
        if during:
            during()
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "send": True,
                    "text": "上回那个星轨小实验，看起来像一碗会转的面条。 "
                    + material["url"],
                    "thread_id": "",
                    "material_key": material["key"],
                }
            )
        )

    flow.plugin.initiative.models = SimpleNamespace(generate=generate)


@pytest.mark.asyncio
async def test_preview_keeps_material_then_uncertain_send_consumes_it_across_restart(
    flow,
):
    save_note(flow)
    save_note(flow, "someone-else", "private")
    materials = flow.plugin.initiative.materials
    assert len(materials.candidates()) == 1  # Note + bookmark are the same source.
    choose(flow)
    preview = await flow.plugin.initiative.proactive(preview=True)
    assert preview["send"] and preview["material"]["title"] == "星空小实验"
    assert len(materials.candidates()) == 1
    assert flow.plugin.policy.data["deliveries"] == []
    flow.context.send_message.side_effect = ConnectionError("may have reached remote")
    result = await flow.plugin.initiative.proactive(manual=True)
    assert result["reason"] == "delivery_uncertain"
    restored = DeliveryPolicy(flow.plugin.policy.path, WX)
    item = restored.data["deliveries"][-1]
    assert item["status"] == "delivery_uncertain"
    assert item["material_key"] == preview["material"]["key"]
    assert item["source_url"] == "https://example.org/one"
    assert (
        InterestMaterials(
            WebNotebook(flow.folder), restored, materials.scope
        ).candidates()
        == []
    )
    shared = InterestMaterials(
        WebNotebook(flow.folder), restored, materials.scope
    ).candidates(include_shared=True)
    assert len(shared) == 1
    assert shared[0]["source_quote"] == "拖动可以改变星轨。"
    assert shared[0]["personal_comment"] == "这拖尾看着像面条"
    flow.bridge.record_visible_turn.assert_not_awaited()
    assert flow.histories[WX] == flow.histories[QQ] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "dismiss", "edit"])
async def test_changed_material_between_generation_and_delivery_is_not_sent(
    flow, change
):
    item_id = save_note(flow)
    p, store = flow.plugin, flow.plugin.public_browser.store
    material = p.initiative.materials.candidates()[0]

    def mutate():
        if change == "delete":
            store.notes(p.owner_memory.shared_scope, "delete", item_id=item_id)
            for bookmark in store.bookmarks(p.owner_memory.shared_scope, "list")[
                "bookmarks"
            ]:
                store.bookmarks(
                    p.owner_memory.shared_scope, "delete", item_id=bookmark["id"]
                )
        elif change == "dismiss":
            p.initiative.materials.dismiss(material["key"])
        else:
            store.notes(
                p.owner_memory.shared_scope, "save", "one", comment="刚发现并不是那样"
            )

    choose(flow, during=mutate)
    result = await p.initiative.proactive(manual=True)
    assert result["reason"] == "material_changed"
    flow.context.send_message.assert_not_awaited()
    assert p.policy.data["deliveries"] == []


def test_dismiss_survives_restart_and_preserves_notebook(flow):
    save_note(flow)
    p = flow.plugin
    material = p.initiative.materials.candidates()[0]
    p.initiative.materials.dismiss(material["key"])
    restored = InterestMaterials(
        WebNotebook(flow.folder), p.policy, p.owner_memory.shared_scope
    )
    assert restored.candidates() == []
    assert len(restored.notebook.notes(restored.scope, "list")["notes"]) == 1
    assert len(restored.notebook.bookmarks(restored.scope, "list")["bookmarks"]) == 1
    assert p.policy.data["deliveries"] == []


def test_failed_reservation_leaves_no_false_consumption_or_quota(flow):
    save_note(flow)
    p = flow.plugin
    material = p.initiative.materials.candidates()[0]
    before = p.policy.path.read_bytes()
    with (
        patch.object(p.policy, "save", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        p.policy.reserve("真实的分享候选", NOW, manual=True, material=material)
    assert p.policy.path.read_bytes() == before
    assert p.policy.data["deliveries"] == []
    assert p.policy.data["unanswered"] == 0
    assert p.initiative.materials.candidates() == [material]


@pytest.mark.asyncio
async def test_source_less_or_unknown_source_is_not_claimed_as_a_saved_discovery(flow):
    save_note(flow)
    flow.plugin.initiative.models = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "send": True,
                        "text": "我刚看到了新的发现。",
                        "material_key": "invented-key",
                    }
                )
            )
        )
    )
    result = await flow.plugin.initiative.proactive(preview=True)
    assert not result["send"]
    flow.context.send_message.assert_not_awaited()
