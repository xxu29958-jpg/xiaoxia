"""The native remember tool's real note types support repeated corrections."""

import pytest
from core.memory_proposal import MemoryProposal
from test_memory_correction import correct, seeded
from test_memory_correction import store as memory_store

store = memory_store


@pytest.mark.asyncio
@pytest.mark.parametrize("note_type", ["memory", "preference", "fact"])
async def test_manual_note_types_remain_correctable_after_replacement(store, note_type):
    proposal = MemoryProposal.from_payload("old fact", {"note_type": note_type})
    old = await seeded(
        store,
        memory_type=proposal.memory_type,
        metadata={
            "persona_id": "persona",
            "owner_bot_id": "bot",
            "tool": "memory_companion_remember",
        },
    )
    receipt = await correct(store, old)
    replacement = await store.get_memory(receipt["new_ref"]["id"])
    assert replacement.supersedes_id == old.id
    assert (await store.get_memory(old.id)).validity_status == "superseded"
    assert replacement.metadata["origin_tool"] == "memory_companion_remember"
    second = await correct(
        store, replacement, correction_id="change-2", content="另一次明确更正。"
    )
    assert second["ok"]
    assert (await store.get_memory(replacement.id)).validity_status == "superseded"
    assert (await store.get_memory(old.id)).content == old.content
