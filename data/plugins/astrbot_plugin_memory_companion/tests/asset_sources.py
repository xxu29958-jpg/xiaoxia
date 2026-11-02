"""Resolve the delivered stylesheet import graph for semantic CSS checks."""

import re
from pathlib import Path


def stylesheet(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    return re.sub(
        r'@import\s+(?:url\()?["\']([^"\']+)["\']\)?;',
        lambda match: stylesheet((path.parent / match.group(1)).resolve()),
        source,
    )
