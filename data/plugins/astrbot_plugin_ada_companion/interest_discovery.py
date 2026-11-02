"""Read new public interest material into the existing companion notebook.

RSS-backed material follows Private Companion's news-source approach, using
the same notebook and scheduled sender as Ada's Bilibili discoveries.
"""

import json
import re
import time
import uuid
from html.parser import HTMLParser
from xml.etree import ElementTree

from .public_transport import PublicTransport, browse_url

FEEDS = (
    "https://www.gcores.com/rss",
    "https://www.sciencedaily.com/rss/plants_animals/birds.xml",
    "https://www.stardewvalley.net/feed/",
)

READING_PROMPT = """
你在读几段公开文章。挑一个让你真有点兴趣、想接着琢磨的具体细节，
留一条小夏自己的随手想法：为什么吸引你、你偏心哪里，或它让你想到的一个问题。
不必向人推荐文章，不写摘要或赞美套话，也不替对方安排要做什么。
依据只限给出的正文；想象是想象，已经发生的经历按记录来。
返回 JSON：{"pick":从0开始的序号,"quote":"正文中12到600字的连续原文",
"thought":"自己的简短想法"}。没有感兴趣的就返回 {"pick":null}。
"""


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def feed_entries(raw):
    """Read publisher-provided article text and original links from an RSS feed."""
    for item in ElementTree.fromstring(raw).findall("./channel/item")[:12]:
        fields = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in item}
        parser = PlainText()
        parser.feed(fields.get("encoded") or fields.get("description", ""))
        text = " ".join(" ".join(parser.parts).split())
        if len(text) >= 100 and fields.get("link"):
            yield {
                "url": browse_url(fields["link"]),
                "title": fields.get("title", ""),
                "text": text[:12000],
                "published": fields.get("pubDate", ""),
            }


class InterestDiscovery:
    def __init__(self, bili):
        self.bili = bili
        self.store = bili.store

    async def scan(self, scope, *, automatic=False):
        result = await self.bili.scan(scope, automatic=automatic)
        if result.get("saved") or result.get("reason") or not result.get("attempted"):
            return result
        if not self.store.enabled():
            return result
        report = await self.bili._run(scope, self.read_feeds(scope), 45)
        report["bilibili"] = result
        return report

    async def read_feeds(self, scope):
        """Read a few excerpts and keep one detail with Ada's own reaction."""
        known = {row["url"] for row in self.store.notes(scope, "list").get("notes", [])}
        network = self.bili.reader.services.plugin("astrbot_plugin_codex_oauth_plug")
        proxy = network.config.get("runtime", {}).get("proxy") if network else None
        fallback = self.bili.config.get("public_network_fallback_proxy")
        report = {"ok": False, "source": "public_feeds", "saved": [], "errors": []}
        persona = await self.bili.models.context.persona_manager.get_persona("Ada")
        offset = int(time.time() // 21600) % len(FEEDS)
        async with PublicTransport(proxy or None, fallback or None) as transport:
            for url in FEEDS[offset:] + FEEDS[:offset]:
                try:
                    _, response = await transport.load(url, document=False)
                    entries = [
                        entry
                        for entry in feed_entries(response["body"])
                        if entry["url"] not in known
                    ][:3]
                    if not entries:
                        continue
                    reading = await self.bili.models.generate(
                        system_prompt=persona.system_prompt + READING_PROMPT,
                        prompt=json.dumps(
                            [
                                {"title": entry["title"], "text": entry["text"][:4000]}
                                for entry in entries
                            ],
                            ensure_ascii=False,
                        ),
                        contexts=[],
                    )
                    chosen = json.loads(
                        re.sub(
                            r"^```(?:json)?\s*|\s*```$",
                            "",
                            reading.completion_text.strip(),
                        )
                    )
                    index = chosen.get("pick")
                    if type(index) is not int or not 0 <= index < len(entries):
                        continue
                    entry = entries[index]
                    quote, thought = chosen.get("quote"), chosen.get("thought")
                    if (
                        not isinstance(quote, str)
                        or not 12 <= len(quote) <= 600
                        or quote not in entry["text"][:4000]
                        or not isinstance(thought, str)
                        or not 1 <= len(thought) <= 600
                    ):
                        continue
                    page_id = uuid.uuid4().hex
                    self.store.save_visit(scope, {"page_id": page_id, **entry}, "")
                    note = self.store.notes(
                        scope,
                        "save",
                        page_id,
                        source_quote=quote,
                        comment=thought,
                        next_step=f"Read from the publisher's feed. Published: {entry['published']}",
                        only_if_new=True,
                    )
                    if note.get("ok"):
                        report.update(
                            ok=True,
                            saved=[{"url": entry["url"], "title": entry["title"]}],
                        )
                        return report
                except Exception as exc:
                    report["errors"].append({"url": url, "error": str(exc)[:200]})
        return report
