"""Bridge community web discovery into MemeManager's existing image pipeline."""

from __future__ import annotations

import html
import io
import ipaddress
import json
import re
from urllib.parse import urlsplit

from PIL import Image

from .public_transport import PublicTransport, browse_url
from .web_notebook import BackpackError

MAX_BYTES = 6 * 1024 * 1024


class CollectionError(ValueError):
    """An expected, user-safe collection failure."""


def public_url(value: str, *, image: bool = False) -> str:
    """Validate a source or enforce the stricter direct-download host boundary.

    Args:
        value: Proposed public URL, never a local path.
        image: Whether the URL will be fetched by this process.

    Returns:
        The validated URL with HTML entities decoded.

    Raises:
        CollectionError: The URL crosses the configured network boundary.
    """
    value = html.unescape(value.strip())
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (
            len(value) > 2048
            or parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
            or "\\" in value
            or any(ord(c) < 33 for c in value)
            or "." not in host
            or host.endswith((".local", ".localhost", ".internal", ".lan", "."))
            or host == "localhost.localdomain"
        ):
            raise CollectionError("A public HTTPS URL without credentials is required.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise CollectionError("IP-address URLs are not accepted.")
        try:
            browse_url(value, document=False)
        except BackpackError as exc:
            raise CollectionError(str(exc)) from None
    except ValueError as exc:
        if isinstance(exc, CollectionError):
            raise
        raise CollectionError("Invalid URL.") from None
    return value


def page_images(extracted: str) -> list[str]:
    """Read actual image references from a remote page extraction.

    Args:
        extracted: AnySearch JSON or Markdown source data.

    Returns:
        At most forty supported image URLs, in page order.
    """
    try:
        data = json.loads(extracted)
        text = str(data.get("content", "")) if isinstance(data, dict) else ""
    except (TypeError, ValueError):
        text = extracted
    found = re.findall(r"!\[[^\]]*\]\((https://[^\s)]+)", text)
    found += re.findall(r"<img\b[^>]*\bsrc=[\"\x27](https://[^\"\x27]+)", text, re.I)
    results = []
    for value in found:
        try:
            value = public_url(value, image=True)
        except CollectionError:
            continue
        if value not in results:
            results.append(value)
    return results[:40]


def validate_image(content: bytes) -> str:
    if len(content) > MAX_BYTES:
        raise CollectionError("Image exceeds the 6 MiB limit.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            extension = {
                "JPEG": ".jpg",
                "PNG": ".png",
                "WEBP": ".webp",
                "GIF": ".gif",
            }.get(image.format)
            if (
                not extension
                or image.width * image.height > 16_000_000
                or getattr(image, "n_frames", 1) > 160
            ):
                raise CollectionError("Unsupported or oversized image.")
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise CollectionError("The download is not a valid bounded image.") from None
    return extension


async def download_image(
    url: str,
    proxy: str | None = None,
    fallback_proxy: str | None = None,
    referer: str = "",
) -> bytes:
    """Fetch only image bytes through public DNS pinning and checked redirects."""
    try:
        async with PublicTransport(
            proxy, fallback_proxy, images_only=True, referer=referer
        ) as transport:
            _, response = await transport.load(
                public_url(url, image=True), document=False
            )
            content = response["body"]
            validate_image(content)
            return content
    except BackpackError as exc:
        raise CollectionError(str(exc)) from None
