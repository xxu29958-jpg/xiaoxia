"""Cross-channel delivery and durable feedback regressions, with actual SQLite."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))

from astrbot_plugin_ada_companion.meme_controls import MemeControls, meme_feedback
from astrbot_plugin_ada_companion.owner_memory import OwnerMemory
from astrbot_plugin_meme_manager.meme_usage import MemeUsage, event_scope


def event(origin, text):
    extras = {}
    return SimpleNamespace(
        unified_msg_origin=origin,
        is_private_chat=lambda: True,
        get_message_str=lambda: text,
        get_platform_name=lambda: origin.split(":")[0],
        get_sender_id=lambda: origin.split(":")[-1],
        get_extra=lambda k, d=None: extras.get(k, d),
        set_extra=lambda k, v: extras.update({k: v}),
    )


@pytest.mark.asyncio
async def test_qq_sent_wechat_ban_restart_and_resume_share_one_identity(tmp_path):
    owners = OwnerMemory(tmp_path / "owners.json", ["qq:owner", "wechat:owner"])
    usage = MemeUsage(tmp_path / "usage.db")
    controls = MemeControls(
        owners, SimpleNamespace(plugin=lambda _: SimpleNamespace(meme_usage=usage))
    )
    qq = event("qq:owner", "发张开心表情")
    await controls.receive(qq)
    digest = "a" * 64
    qq.set_extra(
        "meme_manager_semantic_candidates", {"meme:a": {"content_sha256": digest}}
    )
    qq.set_extra("meme_manager_reaction", {"action": "requested"})
    token = usage.claim_event(qq, "meme:a")
    assert token
    usage.finish(token, True)
    wx = event("wechat:owner", "别发这张表情包了")
    await controls.receive(wx)
    assert event_scope(wx) == event_scope(qq)
    assert wx.get_extra("meme_manager_suppress")
    restarted = MemeUsage(usage.path)
    assert digest in restarted.status(event_scope(wx))["blocked"]
    assert restarted.claim(event_scope(qq), digest) is None
    await controls.receive(event("wechat:owner", "不要发表情包"))
    assert restarted.claim(event_scope(qq), "b" * 64) is None
    await controls.receive(event("qq:owner", "恢复表情包"))
    assert restarted.claim(event_scope(wx), "b" * 64)
    assert restarted.claim(event_scope(wx), digest) is None
    assert restarted.claim("stranger", digest)


def test_parallel_dispatch_reserves_once_and_uncertain_send_stays_excluded(tmp_path):
    usage = MemeUsage(tmp_path / "usage.db")
    with ThreadPoolExecutor(4) as executor:
        claims = list(executor.map(lambda _: usage.claim("owner", "c" * 64), range(4)))
    assert sum(bool(x) for x in claims) == 1
    token = next(x for x in claims if x)
    usage.finish(token, False)
    assert MemeUsage(usage.path).claim("owner", "c" * 64) is None
    assert usage.status("owner")["recent"][0]["state"] == "uncertain"


def test_different_images_compete_for_one_automatic_slot_and_restart_keeps_it(
    tmp_path, monkeypatch
):
    now = [1_700_000_000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    usage = MemeUsage(tmp_path / "usage.db")
    with ThreadPoolExecutor(4) as executor:
        claims = list(
            executor.map(lambda c: usage.claim("owner", c * 64, automatic=True), "abcd")
        )
    assert sum(bool(x) for x in claims) == 1
    usage.finish(next(x for x in claims if x), False)
    restored = MemeUsage(usage.path)
    assert not restored.automatic_ready("owner")
    now[0] += 301
    # Five minutes alone cannot turn every message into another sticker.
    assert not restored.automatic_ready("owner")
    now[0] += 1500
    assert restored.automatic_ready("owner")
    assert restored.claim("owner", "e" * 64, automatic=True)


def test_late_reply_cannot_claim_after_a_newer_shared_turn(tmp_path):
    usage = MemeUsage(tmp_path / "usage.db")
    qq, wx = event("qq:owner", "surprise"), event("wechat:owner", "newer topic")
    for current in (qq, wx):
        current.set_extra("meme_manager_scope", "owner")
    turn = usage.observe_event(qq)
    assert usage.observe_event(qq) == turn
    assert usage.observe_event(wx) == turn + 1
    assert usage.claim("owner", "a" * 64, automatic=True, turn=turn) is None
    assert usage.claim("owner", "a" * 64, automatic=True, turn=turn + 1)


def test_fast_back_and_forth_cannot_bypass_five_minute_spacing(tmp_path, monkeypatch):
    now = [1_700_000_000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    usage = MemeUsage(tmp_path / "usage.db")
    assert usage.claim("qq:owner", "a" * 64)
    for _ in range(3):
        usage.observe_event(event("qq:owner", "another turn"))
    now[0] += 299
    assert not usage.automatic_ready("qq:owner")
    now[0] += 1
    assert usage.automatic_ready("qq:owner")


@pytest.mark.parametrize(
    "text,action",
    [
        ("别发啊 别发这破壁表情包", "block"),
        ("不要表情包", "pause"),
        ("这条只回文字", "text"),
        ("恢复表情包", "resume"),
        ("来个开心表情包", ""),
        ("她说“不要表情包”是什么意思", ""),
    ],
)
def test_negative_request_is_not_an_image_request(text, action):
    assert meme_feedback(text) == action


@pytest.mark.asyncio
async def test_excluded_top_hits_do_not_hide_other_real_faiss_candidates(tmp_path):
    import faiss
    import numpy as np
    from astrbot_plugin_meme_manager.backend.semantic.index import (
        INDEX_FORMAT,
        search_index,
    )

    root = tmp_path / "semantic_indexes" / "pack"
    root.mkdir(parents=True)
    index = faiss.IndexIDMap(faiss.IndexFlatIP(2))
    vectors = np.asarray([[1, 0], [0.98, 0.2], [0.9, 0.4]], dtype="float32")
    faiss.normalize_L2(vectors)
    index.add_with_ids(vectors, np.array([1, 2, 3], dtype="int64"))
    faiss.write_index(index, str(root / "index.faiss"))
    keys = [c * 64 for c in "abc"]
    (root / "index_manifest.json").write_text(
        json.dumps(
            {
                "index_format": INDEX_FORMAT,
                "embedding_provider_id": "fixture",
                "embedding_model": "fixture",
                "embedding_dimension": 2,
                "items": {key: {"faiss_id": i + 1} for i, key in enumerate(keys)},
            }
        )
    )
    metadata = {
        "images": {
            key: {
                "category": "happy",
                "caption_status": "done",
                "caption": key,
                "tags": ["happy"],
                "content_sha256": key,
            }
            for key in keys
        }
    }

    async def embed(_):
        return [1, 0]

    embedding = SimpleNamespace(
        provider_id="fixture", model_name="fixture", dimension=2, embed=embed
    )
    usage = MemeUsage(tmp_path / "usage.db")
    received = []
    for _ in range(3):
        hits = await search_index(
            tmp_path,
            "pack",
            "happy",
            embedding,
            metadata,
            top_k=1,
            exclude_hashes=usage.exclusions("owner"),
        )
        assert len(hits) == 1
        digest = hits[0]["content_sha256"]
        received.append(digest)
        assert usage.claim("owner", digest)
    assert received == keys
    assert not await search_index(
        tmp_path,
        "pack",
        "happy",
        embedding,
        metadata,
        top_k=1,
        exclude_hashes=usage.exclusions("owner"),
    )
