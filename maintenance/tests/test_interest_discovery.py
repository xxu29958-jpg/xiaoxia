"""Public reading shares the existing scan cadence and notebook evidence."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from astrbot_plugin_ada_companion.interest_discovery import (
    InterestDiscovery,
    feed_entries,
)

from maintenance.tests.test_bili_discovery import discovery as bili_fixture

discovery = bili_fixture


@pytest.mark.asyncio
async def test_failed_bili_search_reads_feed_once_and_cooldown_survives(discovery):
    discovery.reader.search = AsyncMock(
        return_value={"ok": False, "error": "unavailable"}
    )
    reader = InterestDiscovery(discovery)
    reader.read_feeds = AsyncMock(
        return_value={"ok": True, "saved": [{"title": "Read article"}]}
    )
    first = await reader.scan("owner", automatic=True)
    assert first["saved"] and first["bilibili"]["error"] == "unavailable"
    await reader.scan("owner", automatic=True)
    reader.read_feeds.assert_awaited_once()
    discovery.config["bili_discovery_enabled"] = False
    await reader.scan("another", automatic=True)
    reader.read_feeds.assert_awaited_once()


def test_publisher_text_and_link_survive_rss_html():
    excerpt = "A bird makes its own tools. " * 8
    entries = list(
        feed_entries(f"""<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
        <title>Bird tools</title><link>https://example.org/bird</link>
        <description>Short preview</description><content:encoded><![CDATA[<p>{excerpt}</p>]]></content:encoded>
        <pubDate>Sun, 01 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>""")
    )
    assert len(entries) == 1
    assert entries[0]["text"] == excerpt.strip()
    assert entries[0]["url"] == "https://example.org/bird"


@pytest.mark.asyncio
async def test_reading_chooses_a_detail_and_keeps_its_own_thought(discovery):
    quote = "The crow bends a wire into a hook."
    body = (
        "<rss><channel>"
        + "".join(
            f"<item><title>{title}</title><link>https://example.org/{key}</link>"
            f"<description>{text * 8}</description></item>"
            for key, title, text in (
                ("first", "A forest", "A forest is home to many birds. "),
                ("second", "A crow's tool", quote + " "),
            )
        )
        + "</channel></rss>"
    )
    discovery.models.context = SimpleNamespace(
        persona_manager=SimpleNamespace(
            get_persona=AsyncMock(return_value=SimpleNamespace(system_prompt="Ada"))
        )
    )
    discovery.models.generate.return_value.completion_text = json.dumps(
        {
            "pick": 1,
            "quote": quote,
            "thought": "它会自己改工具，这比只会用现成的有意思。",
        }
    )
    transport = AsyncMock()
    transport.load.return_value = ("https://example.org/feed", {"body": body})
    with patch(
        "astrbot_plugin_ada_companion.interest_discovery.PublicTransport"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=transport)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await InterestDiscovery(discovery).read_feeds("owner")
    assert result["saved"] == [
        {"url": "https://example.org/second", "title": "A crow's tool"}
    ]
    notes = discovery.store.notes("owner", "list")["notes"]
    assert len(notes) == 1
    assert notes[0]["source_quote"] == quote
    assert notes[0]["comment"] == "它会自己改工具，这比只会用现成的有意思。"
    payload = json.loads(discovery.models.generate.await_args.kwargs["prompt"])
    assert len(payload) == 2
