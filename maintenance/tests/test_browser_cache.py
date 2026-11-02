"""Verify cache eviction against real persisted notebook data and files."""

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion.browser_cache import prune_cache
from astrbot_plugin_ada_companion.web_notebook import WebNotebook


def test_expiry_keeps_bookmarks_notes_login_and_unrelated_files(tmp_path):
    store = WebNotebook(tmp_path)
    old = store.folder / ("a" * 32 + ".jpg")
    old.write_bytes(b"expired screenshot")
    page = {
        "page_id": "old",
        "url": "https://example.com",
        "title": "Example",
        "text": "fact",
    }
    store.save_visit("owner", page, old)
    store.bookmarks("owner", "save", "old", "keep this link")
    store.notes("owner", "save", "old", source_quote="fact", comment="my thought")
    with sqlite3.connect(store.db) as conn:
        conn.execute("UPDATE visits SET visited_at=0")
    profile = store.folder / "profiles" / "owner" / "Cookies"
    profile.parent.mkdir(parents=True)
    profile.write_bytes(b"login stays")
    unrelated = store.folder / "family.jpg"
    unrelated.write_bytes(b"not owned by cache")
    orphan = store.folder / ("b" * 32 + ".jpg")
    orphan.write_bytes(b"interrupted operation")
    os.utime(orphan, (0, 0))
    prune_cache(store.folder, store.db)
    reloaded = WebNotebook(tmp_path)
    assert not old.exists() and not orphan.exists()
    assert profile.read_bytes() == b"login stays"
    assert unrelated.read_bytes() == b"not owned by cache"
    assert (
        reloaded.bookmarks("owner", "list")["bookmarks"][0]["note"] == "keep this link"
    )
    assert reloaded.notes("owner", "list")["notes"][0]["source_quote"] == "fact"


def test_size_pressure_reclaims_old_artifacts_without_deleting_inflight(
    tmp_path, monkeypatch
):
    import astrbot_plugin_ada_companion.browser_cache as cache

    store = WebNotebook(tmp_path)
    artifacts = store.folder / "mcp-artifacts" / "owner"
    artifacts.mkdir(parents=True)
    old = artifacts / "console.log"
    recent = artifacts / "new.log"
    old.write_bytes(b"x" * 80)
    recent.write_bytes(b"y" * 40)
    os.utime(old, (1000, 1000))
    os.utime(recent, (1999, 1999))
    monkeypatch.setattr(cache, "MAX_BYTES", 100)
    result = prune_cache(store.folder, store.db, now=2000)
    assert not old.exists()
    assert recent.read_bytes() == b"y" * 40
    assert result["retained_bytes"] == 40


def test_mcp_copy_cleanup_keeps_uploads_and_inflight_images(tmp_path, monkeypatch):
    import astrbot_plugin_ada_companion.browser_cache as cache

    expired = tmp_path / "call_old_1.png"
    pressure = tmp_path / "call_older_2.jpg"
    inflight = tmp_path / "call_current_1.png"
    upload = tmp_path / "20261021_user_photo.jpg"
    for file in (expired, pressure, inflight, upload):
        file.write_bytes(b"image bytes")
        os.utime(file, (0, 0))
    os.utime(pressure, (99000, 99000))
    os.utime(inflight, (99999, 99999))
    monkeypatch.setattr(cache, "MAX_BYTES", 15)
    cache.prune_tool_images(tmp_path, now=100000)
    assert not expired.exists() and not pressure.exists()
    assert inflight.read_bytes() == b"image bytes"
    assert upload.read_bytes() == b"image bytes"
