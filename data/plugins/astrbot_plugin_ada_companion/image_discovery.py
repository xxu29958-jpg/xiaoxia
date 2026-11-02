"""Bounded public-page image discovery using the same real browser and budgets."""

import json
import re
from urllib.parse import unquote, urlsplit

from mcp.types import CallToolResult, TextContent

from .public_transport import browse_url
from .web_notebook import BackpackError


class ImageDiscovery:
    def __init__(self, web):
        self.web = web

    async def find(self, event, url, query="", page_limit=2):
        """Visit at most two same-site pages and inspect one lazy-load scroll each.

        URLs must come from page observations; there is no guessed asset path,
        private-cookie sharing, unbounded recursion or silent download.
        """
        try:
            self.web.owners.scope(event)
            start = browse_url(url)
            if type(page_limit) is not int or not 1 <= page_limit <= 2:
                raise BackpackError("Choose one or two source pages.")
            if not isinstance(query, str) or len(query) > 120:
                raise BackpackError("Use a short public image subject.")
        except (PermissionError, BackpackError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        queue, visited, found, errors, shots = [start], set(), {}, [], []
        words = [x for x in re.split(r"\s+", query.lower()) if x]
        words += [
            x
            for x in ("猫", "狗", "熊猫", "开心", "微笑", "晚安", "cat", "dog")
            if x in query.lower()
        ]
        while queue and len(visited) < page_limit:
            target = queue.pop(0)
            if target in visited:
                continue
            visited.add(target)
            result = await self.web.browse(event, "open", target)
            for step in range(2):
                if not isinstance(result, CallToolResult):
                    errors.append({"url": target, "result": json.loads(result)})
                    break
                data = json.loads(result.content[0].text)
                if not data.get("ok"):
                    errors.append({"url": target, "error": data.get("error")})
                    break
                shots = [x for x in result.content if x.type == "image"]
                for item in data.get("images", []):
                    try:
                        image_url = browse_url(item["url"], document=False)
                    except BackpackError:
                        continue
                    if not image_url.startswith("https://") or re.search(
                        r"(?:avatar|badge|favicon|logo|shields\.io)", image_url, re.I
                    ):
                        continue
                    found.setdefault(
                        image_url,
                        {
                            **item,
                            "url": image_url,
                            "page_id": data["page_id"],
                            "source_url": data["url"],
                        },
                    )
                if step == 0:
                    links = sorted(
                        data.get("links", []),
                        key=lambda x: (
                            sum(
                                word
                                in (
                                    x.get("text", "") + unquote(x.get("url", ""))
                                ).lower()
                                for word in [*words, "表情", "meme", "sticker"]
                            )
                            + (
                                20
                                if re.search(
                                    r"\.(?:png|jpe?g|gif|webp)(?:$|[?#])",
                                    x.get("url", ""),
                                    re.I,
                                )
                                else 0
                            )
                            - (
                                50
                                if re.search(
                                    r"/(?:commits?|blame|pulls?|issues?)/",
                                    x.get("url", ""),
                                )
                                else 0
                            )
                        ),
                        reverse=True,
                    )
                    for link in links:
                        try:
                            candidate = browse_url(link.get("url", ""))
                        except BackpackError:
                            continue
                        if (
                            urlsplit(candidate).hostname == urlsplit(start).hostname
                            and urlsplit(candidate).path != urlsplit(target).path
                            and candidate not in visited
                            and candidate not in queue
                        ):
                            queue.append(candidate)
                    if len(found) >= 12:
                        break
                    result = await self.web.browse(event, "scroll")
        candidates = sorted(
            found.values(),
            key=lambda x: (
                sum(
                    word in (x.get("alt", "") + unquote(x["url"])).lower()
                    for word in words
                )
                - (2 if x.get("width", 0) > 1600 or x.get("height", 0) > 1600 else 0)
            ),
            reverse=True,
        )[:30]
        payload = {
            "ok": bool(candidates),
            "saved": False,
            "pages_visited": len(visited),
            "candidates": candidates,
            "errors": errors,
            "instruction": "These are real observed image URLs, not all verified stickers. Inspect the screenshot, then call ada_collect_online_meme with that candidate's page_id and exact image_url. Collection will validate, classify and deduplicate the image before indexing. No image has been saved or sent by discovery.",
        }
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(payload, ensure_ascii=False)),
                *shots,
            ]
        )
