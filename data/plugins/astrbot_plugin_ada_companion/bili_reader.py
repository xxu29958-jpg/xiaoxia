"""Anonymous, read-only Bilibili metadata and subtitle retrieval."""

import asyncio
import html
import json
import re
from urllib.parse import urlencode, urlsplit

import aiohttp

from .public_transport import BILI_SUBTITLE_HOSTS, PublicTransport, browse_url
from .web_notebook import BackpackError

BV_PATTERN = r"BV[0-9A-Za-z]{10}"
API = "https://api.bilibili.com"
API_PATHS = {"/x/web-interface/search/type", "/x/web-interface/view", "/x/player/v2"}
SUBTITLE_HOSTS = BILI_SUBTITLE_HOSTS
MAX_JSON_BYTES = 1024 * 1024
MAX_TRANSCRIPT_CHARS = 12000
UNAVAILABLE = "Subtitles unavailable; metadata is not evidence of video content."


class BiliReader:
    """Read public data without login, media downloads, or account operations.

    Args:
        config: Companion configuration, used only for the public fallback proxy.
        services: Plugin services exposing the shared network proxy configuration.
    """

    def __init__(self, config, services):
        self.config = config
        self.services = services

    async def _json(self, transport, url, *, subtitle=False):
        """Validate each destination before fetching one bounded JSON object.

        Args:
            transport: The public pinned transport for this operation.
            url: A fixed API URL or an API-advertised subtitle URL.
            subtitle: Whether only the subtitle CDN allowlist is permitted.

        Returns:
            A decoded JSON object, never an HTML page or redirect.

        Raises:
            BackpackError: The destination, response, or API status is invalid.
        """
        if not isinstance(url, str) or len(url) > 2000:
            raise BackpackError("Invalid Bilibili source URL.")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port is not None
            or parts.fragment
            or re.search(r"[\x00-\x20\\]", url)
        ):
            raise BackpackError("Only fixed HTTPS Bilibili sources are allowed.")
        if subtitle:
            if (
                parts.netloc not in SUBTITLE_HOSTS
                or not re.fullmatch(
                    r"/bfs/(?:subtitle|ai_subtitle)/[A-Za-z0-9_./-]+", parts.path
                )
                or any(segment in {".", ".."} for segment in parts.path.split("/"))
            ):
                raise BackpackError(
                    "Subtitle source is outside the Bilibili CDN allowlist."
                )
        elif parts.netloc != "api.bilibili.com" or parts.path not in API_PATHS:
            raise BackpackError(
                "Only fixed read-only Bilibili API endpoints are allowed."
            )
        # Only an API-advertised, exact-CDN resource signature can pass this exception.
        url = browse_url(url, document=False, subtitle_signature=subtitle)
        payload = await transport.fetch(url, document=False)
        if payload.get("status") != 200:
            raise BackpackError(
                "Bilibili redirects are not followed; access was not bypassed."
            )
        raw = payload.get("body")
        mime = (
            payload.get("headers", {})
            .get("content-type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if mime not in {"application/json", "text/json"} or not isinstance(raw, bytes):
            raise BackpackError("Bilibili did not return a JSON response.")
        if len(raw) > MAX_JSON_BYTES:
            raise BackpackError("Bilibili JSON response exceeded its byte limit.")
        try:
            # int rejects JSON's nonstandard NaN and Infinity constants.
            data = json.loads(raw.decode("utf-8"), parse_constant=int)
        except (ValueError, UnicodeError, RecursionError):
            raise BackpackError("Malformed Bilibili JSON response.") from None
        if not isinstance(data, dict):
            raise BackpackError("Malformed Bilibili JSON object.")
        if not subtitle:
            if type(data.get("code")) is not int:
                raise BackpackError("Malformed Bilibili API status.")
            if data["code"] != 0:
                raise BackpackError(
                    "Bilibili API unavailable or restricted; login, payment and captcha are not bypassed."
                )
            data = data.get("data")
            if not isinstance(data, dict):
                raise BackpackError("Malformed Bilibili API data.")
        return data

    async def search(self, query: str, limit=3) -> dict:
        """Search for at most three public metadata candidates, not content evidence.

        Args:
            query: A nonempty public search phrase of at most 200 characters.
            limit: An integer candidate count, clamped to the range one to three.

        Returns:
            A result with ok, videos, source provenance, and an optional error.
        """
        result = {"ok": False, "videos": [], "sources": [], "untrusted_source": True}
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 200
            or re.search(r"[\x00-\x1f\x7f]", query)
            or type(limit) is not int
        ):
            result["error"] = (
                "Use a public search phrase of 1–200 characters and an integer limit."
            )
            return result
        limit = max(1, min(3, limit))
        url = (
            API
            + "/x/web-interface/search/type?"
            + urlencode(
                {
                    "search_type": "video",
                    "keyword": query.strip(),
                    "page": 1,
                    "page_size": limit,
                }
            )
        )
        try:
            network = self.services.plugin("astrbot_plugin_codex_oauth_plug")
            proxy = network.config.get("runtime", {}).get("proxy") if network else None
            fallback = self.config.get("public_network_fallback_proxy")
            async with PublicTransport(
                proxy or None, fallback or None, json_only=True
            ) as transport:
                data = await self._json(transport, url)
            items = data.get("result")
            if not isinstance(items, list) or len(items) > 100:
                raise BackpackError("Malformed Bilibili search results.")
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                bvid, title, author = (
                    item.get("bvid"),
                    item.get("title"),
                    item.get("author", ""),
                )
                if (
                    not isinstance(bvid, str)
                    or not re.fullmatch(BV_PATTERN, bvid)
                    or not isinstance(title, str)
                    or not title.strip()
                    or len(title) > 2000
                    or not isinstance(author, str)
                    or len(author) > 500
                ):
                    continue
                title = html.unescape(re.sub(r"<[^>]*>", "", title))[:300]
                if any(x["bvid"] == bvid for x in result["videos"]):
                    continue
                result["videos"].append(
                    {
                        "bvid": bvid,
                        "title": title,
                        "author": author[:200],
                        "url": f"https://www.bilibili.com/video/{bvid}",
                        "evidence": "metadata_only",
                        "source_url": url,
                    }
                )
                if len(result["videos"]) >= limit:
                    break
            result.update(ok=True, sources=[{"kind": "search_metadata", "url": url}])
        except (
            BackpackError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
            AttributeError,
        ):
            result["error"] = (
                "Bilibili search unavailable, restricted, or malformed; no content was read."
            )
        return result

    async def read(self, video: str) -> dict:
        """Read first-part subtitles if public, otherwise return metadata explicitly.

        Args:
            video: A BV identifier or canonical HTTPS www.bilibili.com/video URL.

        Returns:
            Metadata and at most 12000 subtitle characters with explicit evidence,
            truncation, provenance, and an error when subtitles are unavailable.
        """
        result = {
            "ok": False,
            "bvid": "",
            "url": "",
            "title": "",
            "author": "",
            "evidence": "metadata_only",
            "transcript": "",
            "truncated": False,
            "sources": [],
            "untrusted_source": True,
            "scope": "first_part_only",
        }
        match = (
            re.fullmatch(
                rf"(?:https://www\.bilibili\.com/video/)?({BV_PATTERN})/?", video
            )
            if isinstance(video, str) and len(video) <= 200
            else None
        )
        if not match or (video.endswith("/") and not video.startswith("https://")):
            result["error"] = (
                "Use a BV identifier or canonical https://www.bilibili.com/video/BV URL without query or fragment."
            )
            return result
        bvid = match[1]
        result.update(bvid=bvid, url=f"https://www.bilibili.com/video/{bvid}")
        view_url = API + "/x/web-interface/view?" + urlencode({"bvid": bvid})
        try:
            network = self.services.plugin("astrbot_plugin_codex_oauth_plug")
            proxy = network.config.get("runtime", {}).get("proxy") if network else None
            fallback = self.config.get("public_network_fallback_proxy")
            async with PublicTransport(
                proxy or None,
                fallback or None,
                json_only=True,
                subtitle_signatures=True,
            ) as transport:
                metadata = await self._json(transport, view_url)
                title, owner = metadata.get("title"), metadata.get("owner")
                cid = metadata.get("cid")
                if (
                    metadata.get("bvid") != bvid
                    or not isinstance(title, str)
                    or not title.strip()
                    or len(title) > 2000
                    or not isinstance(owner, dict)
                    or not isinstance(owner.get("name"), str)
                    or len(owner["name"]) > 500
                    or type(cid) is not int
                    or not 0 < cid < 10**18
                ):
                    raise BackpackError("Malformed Bilibili video metadata.")
                result.update(
                    ok=True, title=title[:300], author=owner["name"][:200], cid=cid
                )
                result["sources"].append({"kind": "video_metadata", "url": view_url})
                result["error"] = UNAVAILABLE
                rights = metadata.get("rights", {})
                if (
                    not isinstance(rights, dict)
                    or any(
                        rights.get(flag) not in (None, 0, False)
                        for flag in ("pay", "ugc_pay", "ugc_pay_preview")
                    )
                    or metadata.get("is_upower_exclusive")
                ):
                    result["error"] = "Paid or restricted video skipped. " + UNAVAILABLE
                    return result
                player_url = (
                    API + "/x/player/v2?" + urlencode({"bvid": bvid, "cid": cid})
                )
                player = await self._json(transport, player_url)
                if (
                    "bvid" in player
                    and player["bvid"] != bvid
                    or "cid" in player
                    and (type(player["cid"]) is not int or player["cid"] != cid)
                ):
                    raise BackpackError(
                        "Bilibili subtitle listing did not match the video."
                    )
                result["sources"].append(
                    {"kind": "subtitle_listing", "url": player_url}
                )
                if player.get("need_login_subtitle") or player.get("is_paid_course"):
                    result["error"] = (
                        "Login or payment restricted subtitles skipped. " + UNAVAILABLE
                    )
                    return result
                subtitle = player.get("subtitle", {})
                if not isinstance(subtitle, dict):
                    raise BackpackError("Malformed Bilibili subtitle listing.")
                subtitles = subtitle.get("subtitles", [])
                if not isinstance(subtitles, list) or len(subtitles) > 100:
                    raise BackpackError("Malformed Bilibili subtitle listing.")
                if not subtitles:
                    return result
                selected = None
                for item in subtitles:
                    if not isinstance(item, dict):
                        raise BackpackError("Malformed Bilibili subtitle entry.")
                    language, source = item.get("lan"), item.get("subtitle_url")
                    if (
                        not isinstance(language, str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", language)
                        or not isinstance(source, str)
                        or not source
                    ):
                        raise BackpackError("Malformed Bilibili subtitle entry.")
                    if selected is None or language in {"zh-CN", "ai-zh", "zh-Hans"}:
                        selected = (language, source)
                        if language in {"zh-CN", "zh-Hans"}:
                            break
                language, source = selected
                if source.startswith("//"):
                    source = "https:" + source
                data = await self._json(transport, source, subtitle=True)
                body = data.get("body")
                if not isinstance(body, list) or len(body) > 20000:
                    raise BackpackError("Malformed Bilibili subtitle body.")
                lines, length, truncated = [], 0, False
                for segment in body:
                    if not isinstance(segment, dict) or not isinstance(
                        segment.get("content"), str
                    ):
                        raise BackpackError("Malformed Bilibili subtitle content.")
                    text = segment["content"].strip()
                    if not text:
                        continue
                    text = ("\n" if lines else "") + text
                    remaining = MAX_TRANSCRIPT_CHARS - length
                    if len(text) > remaining:
                        truncated = True
                    if remaining:
                        lines.append(text[:remaining])
                        length += min(remaining, len(text))
                transcript = "".join(lines)
                if not transcript:
                    return result
                result.update(
                    evidence="subtitles",
                    transcript=transcript,
                    truncated=truncated,
                    subtitle_language=language,
                )
                result.pop("error", None)
                result["sources"].append(
                    {"kind": "subtitles", "url": source.split("?", 1)[0]}
                )
        except (
            BackpackError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            TypeError,
            AttributeError,
        ):
            result["error"] = (
                "Subtitles unavailable, restricted, or malformed; metadata is not content evidence."
                if result["ok"]
                else "Bilibili metadata unavailable, restricted, or malformed; no video was watched."
            )
        return result
