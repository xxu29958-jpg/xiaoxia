"""Reading range, attribution and recovery through the actual notebook consumers."""

import json
import multiprocessing
import shutil
import sqlite3
import time
from contextlib import closing
from unittest.mock import patch

import pytest
from astrbot_plugin_ada_companion import reading_store
from astrbot_plugin_ada_companion.browser_cache import prune_cache
from astrbot_plugin_ada_companion.browser_document import PAGE_DATA
from astrbot_plugin_ada_companion.reading_evidence import MAX_SOURCE_CHARS
from astrbot_plugin_ada_companion.web_notebook import BackpackError, WebNotebook
from playwright.sync_api import sync_playwright

from maintenance.tests.reading_crash_support import capture_until_terminated


def capture(store, text, *, page_id="first", shown=8, url="https://example.org/item"):
    data = {
        "page_id": page_id,
        "url": url,
        "title": "A source",
        "text": text[:shown],
        "_source_text": text,
        "source_kind": "rss_entry",
        "source_url": "https://example.org/feed",
        "source_total_chars": len(text),
    }
    store.save_visit("owner", data, "")
    assert "_source_text" not in data
    return data


def test_unprovided_quote_rejected_then_resume_same_version_after_restart(tmp_path):
    store = WebNotebook(tmp_path)
    text = "前面读过的文字。后面隐藏的反例说明实验失败。"
    first = capture(store, text)
    hidden = "反例说明实验失败"
    with pytest.raises(BackpackError, match="actually read"):
        store.notes("owner", "save", "first", source_quote=hidden)
    saved = store.notes(
        "owner", "save", "first", source_quote=text[:8], next_step="继续读反例"
    )
    store = WebNotebook(tmp_path)
    continuation = store.read("owner", item_id=saved["id"])
    assert continuation["text"] == text[8:]
    assert continuation["reading"]["source_id"] == first["reading"]["source_id"]
    updated = store.notes(
        "owner",
        "save",
        continuation["page_id"],
        source_quote=hidden,
        comment="原来适用范围有限",
    )
    quote_range = updated["reading"]["quote_range"]
    assert text[quote_range["start"] : quote_range["end"]] == hidden
    assert updated["reading"]["source_kind"] == "rss_entry"
    assert updated["reading"]["source_url"] == "https://example.org/feed"
    assert store.read("owner", item_id=saved["id"])["text"] == ""
    with pytest.raises(BackpackError, match="unavailable"):
        store.read("stranger", source_id=first["reading"]["source_id"])


def test_browser_long_unicode_source_can_continue_past_original_prefix(tmp_path):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page()
            full = "🦉树上的观察。" * 5000 + "可验证的尾部"
            page.set_content("<main>" + full + "</main>")
            data = page.evaluate(PAGE_DATA)
        finally:
            browser.close()
    data.update(page_id="dom", url="https://example.org/long")
    store = WebNotebook(tmp_path)
    store.save_visit("owner", data, "")
    assert data["reading"]["provided_range"] == {
        "start": 0,
        "end": 24000,
        "unit": "unicode_codepoints",
    }
    rest = store.read("owner", source_id=data["reading"]["source_id"], limit=24000)
    assert data["text"] + rest["text"] == full
    assert rest["reading"]["next_offset"] is None
    assert not rest["reading"]["capture_incomplete"]


def test_changed_source_never_mixes_versions_and_pinned_source_survives_cache(tmp_path):
    store = WebNotebook(tmp_path)
    old = capture(store, "原始材料的前半。原始材料的后半。")
    note = store.notes("owner", "save", "first", source_quote=old["text"])
    new = capture(store, "已更新材料前半。现在结论已经不同。", page_id="new")
    assert new["reading"]["source_id"] != old["reading"]["source_id"]
    for number in range(35):
        capture(
            store,
            "未收藏缓存" + str(number),
            page_id=f"p{number}",
            url=f"https://example.org/{number}",
        )
    prune_cache(store.folder, store.db, now=time.time() + 90000)
    continued = WebNotebook(tmp_path).read("owner", item_id=note["id"])
    assert continued["text"] == "原始材料的后半。"
    assert continued["reading"]["source_id"] == old["reading"]["source_id"]


def test_capture_failure_rolls_back_source_range_and_visit(tmp_path):
    store = WebNotebook(tmp_path)
    with patch.object(
        reading_store, "provided", side_effect=RuntimeError("injected before commit")
    ):
        with pytest.raises(RuntimeError, match="injected"):
            capture(store, "没有提交的读取内容")
    with closing(sqlite3.connect(store.db)) as conn:
        assert [
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("reading_sources", "reading_ranges", "visits")
        ] == [0, 0, 0]
    assert (
        capture(store, "重试后实际提交的内容")["reading"]["provided_range"]["end"] == 8
    )


def test_independent_instances_replays_and_overlapping_ranges_stay_bounded(tmp_path):
    first, second = WebNotebook(tmp_path), WebNotebook(tmp_path)
    text = "abcdefghijklmnopqrstuvwxyz" * 4
    data = capture(first, text)
    for number in range(10):
        capture(second, text, page_id=f"repeat{number}")
        first.read("owner", source_id=data["reading"]["source_id"], offset=4, limit=10)
    with closing(sqlite3.connect(first.db)) as conn:
        assert conn.execute("SELECT count(*) FROM reading_sources").fetchone()[0] == 1
        assert conn.execute("SELECT start,end FROM reading_ranges").fetchall() == [
            (0, 14)
        ]
    assert (
        second.read("owner", source_id=data["reading"]["source_id"], limit=5)["text"]
        == text[14:19]
    )


def test_source_limit_error_pages_and_invalid_offsets_do_not_claim_completion(tmp_path):
    store = WebNotebook(tmp_path)
    source = "段" * MAX_SOURCE_CHARS
    data = {
        "page_id": "bounded",
        "url": "https://example.org/long",
        "title": "Long",
        "text": source[:10],
        "_source_text": source,
        "source_total_chars": MAX_SOURCE_CHARS + 20,
    }
    store.save_visit("owner", data, "")
    assert data["reading"]["capture_incomplete"]
    for offset in (-2, True, MAX_SOURCE_CHARS + 1):
        with pytest.raises(BackpackError):
            store.read("owner", page_id="bounded", offset=offset)
    store.save_visit(
        "owner",
        {
            "page_id": "denied",
            "url": "https://example.org/private",
            "title": "Forbidden",
            "text": "Access denied",
            "ok": False,
        },
        "",
    )
    with pytest.raises(BackpackError, match="access error"):
        store.notes("owner", "save", "denied", source_quote="Access denied")


def test_export_and_database_relocation_preserve_references_without_other_owner(
    tmp_path,
):
    store = WebNotebook(tmp_path / "before")
    data = capture(store, "一份留下开放问题、可以在下一次继续读的材料。")
    note = store.notes("owner", "save", "first", source_quote=data["text"])
    exported = json.loads(store.export("owner").read_text(encoding="utf-8"))
    assert exported["version"] == 2
    assert (
        exported["notes"][0]["reading"]["source_id"]
        == exported["reading_evidence"]["sources"][0]["id"]
    )
    assert not json.loads(store.export("stranger").read_text(encoding="utf-8"))[
        "reading_evidence"
    ]["sources"]
    relocated = tmp_path / "after" / "web_backpack"
    relocated.mkdir(parents=True)
    shutil.copy2(store.db, relocated / "notebook.sqlite3")
    restored = WebNotebook(relocated.parent)
    assert (
        restored.read("owner", item_id=note["id"])["text"]
        == "一份留下开放问题、可以在下一次继续读的材料。"[8:]
    )


@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
def test_process_termination_recovers_only_committed_reading(tmp_path, window):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    wait = ctx.Event()
    process = ctx.Process(
        target=capture_until_terminated, args=(tmp_path, window, child, wait)
    )
    process.start()
    try:
        assert parent.poll(15), (
            "Native reading transaction did not reach its kill window"
        )
        assert parent.recv() == (
            "transaction_open" if window == "before_commit" else "committed"
        )
        process.terminate()
        process.join(10)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        child.close()
        parent.close()
    restored = WebNotebook(tmp_path)
    with closing(sqlite3.connect(restored.db)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        counts = [
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("reading_sources", "reading_ranges", "visits")
        ]
    assert counts == ([0, 0, 0] if window == "before_commit" else [1, 1, 1])
    if window == "after_commit":
        assert restored.read("owner", page_id="crash")["text"] == "和还未返回的后半段。"


def test_export_holds_one_snapshot_when_notes_and_sources_change(tmp_path):
    store = WebNotebook(tmp_path)
    data = capture(store, "导出时必须保持来源与笔记一致。")
    store.notes("owner", "save", "first", source_quote=data["text"])
    with closing(sqlite3.connect(store.db)) as other:
        other.execute("PRAGMA journal_mode=WAL")
    original = reading_store.export

    def delete_between_reads(conn, scope):
        with closing(sqlite3.connect(store.db)) as other, other:
            other.execute("DELETE FROM notes WHERE scope=?", (scope,))
            other.execute("DELETE FROM reading_sources WHERE scope=?", (scope,))
        return original(conn, scope)

    with patch.object(reading_store, "export", side_effect=delete_between_reads):
        exported = json.loads(store.export("owner").read_text(encoding="utf-8"))
    assert (
        exported["notes"][0]["reading"]["source_id"]
        == exported["reading_evidence"]["sources"][0]["id"]
    )
    assert not store.notes("owner", "list")["notes"]


def test_upstream_truncation_is_part_of_source_identity(tmp_path):
    store = WebNotebook(tmp_path)
    first = capture(store, "实际捕获的字幕片段", shown=4)
    second = {
        "page_id": "cut",
        "url": first["url"],
        "title": first["title"],
        "text": "实际捕获",
        "_source_text": "实际捕获的字幕片段",
        "source_kind": "rss_entry",
        "source_url": "https://example.org/feed",
        "source_truncated": True,
    }
    store.save_visit("owner", second, "")
    assert first["reading"]["source_id"] != second["reading"]["source_id"]
    assert second["reading"]["capture_incomplete"]
    assert not first["reading"]["capture_incomplete"]


@pytest.mark.parametrize("offset", [-1, 4])
def test_empty_capture_cannot_record_a_range_outside_source(tmp_path, offset):
    store = WebNotebook(tmp_path)
    with pytest.raises(BackpackError, match="does not match"):
        store.save_visit(
            "owner",
            {
                "page_id": "empty",
                "url": "https://example.org/empty",
                "title": "Empty",
                "text": "",
                "_source_text": "abc",
                "reading_offset": offset,
            },
            "",
        )
    with closing(sqlite3.connect(store.db)) as conn:
        assert all(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            for table in ("reading_sources", "reading_ranges", "visits")
        )


def test_legacy_upgrade_backs_up_and_does_not_invent_coverage(tmp_path):
    folder = tmp_path / "web_backpack"
    folder.mkdir()
    path = folder / "notebook.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript("""
            CREATE TABLE notes (id TEXT PRIMARY KEY,scope TEXT,url TEXT,title TEXT,kind TEXT,
            source_quote TEXT,comment TEXT,next_step TEXT,created_at TEXT,updated_at TEXT,UNIQUE(scope,url,kind));
            INSERT INTO notes VALUES ('legacy','owner','https://example.org/old','Old','interesting',
            'old quote','old comment','','2026-11-01','2026-11-01');
        """)
    upgraded = WebNotebook(tmp_path)
    row = upgraded.notes("owner", "list")["notes"][0]
    assert row["reading"] == {"source_kind": "legacy_unknown", "coverage": "unknown"}
    with pytest.raises(BackpackError, match="unavailable"):
        upgraded.read("owner", item_id="legacy")
    backups = list(folder.glob("notebook.before-reading-v1-*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as conn:
        assert "reading" not in {r[1] for r in conn.execute("PRAGMA table_info(notes)")}
        assert conn.execute("SELECT source_quote,comment FROM notes").fetchone() == (
            "old quote",
            "old comment",
        )
    WebNotebook(tmp_path)
    assert list(folder.glob("notebook.before-reading-v1-*.sqlite3")) == backups
