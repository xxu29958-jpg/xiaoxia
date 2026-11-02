"""Bound disposable browser artifacts without touching saved user content."""

import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
MAX_AGE = 86400


def prune_tool_images(folder: Path, *, now=None):
    """Bound only AstrBot-generated MCP image copies; keep chat uploads untouched."""
    now = time.time() if now is None else now
    if not folder.is_dir() or folder.is_symlink():
        return
    owned = []
    for path in folder.iterdir():
        if (
            path.is_symlink()
            or not path.is_file()
            or not re.fullmatch(
                r"call_[a-zA-Z0-9_-]+_\d+\.(png|jpg|jpeg|webp)", path.name
            )
        ):
            continue
        stat = path.stat()
        owned.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in owned)
    for modified, size, path in sorted(owned):
        if modified < now - 300 and (modified < now - MAX_AGE or total > MAX_BYTES):
            path.unlink(missing_ok=True)
            total -= size


def prune_cache(folder: Path, db: Path, *, now=None):
    """Expire screenshot rows and MCP artifacts, preserving profiles and notebooks.

    Args:
        folder: The dedicated web backpack directory.
        db: Its notebook database, containing independently saved bookmarks/notes.
        now: Optional clock override for lifecycle verification.

    Returns:
        Counts of removed files and bytes still retained.
    """
    now = time.time() if now is None else now
    root = folder.resolve()
    removed = 0
    with closing(sqlite3.connect(db)) as conn, conn:
        old = conn.execute(
            "SELECT id,screenshot FROM visits WHERE visited_at<?", (now - MAX_AGE,)
        ).fetchall()
        for item_id, raw in old:
            candidate = Path(raw)
            if (
                candidate.parent.resolve() == root
                and re.fullmatch(r"[a-f0-9]{32}\.jpg", candidate.name)
                and not candidate.is_symlink()
            ):
                candidate.unlink(missing_ok=True)
                removed += 1
            conn.execute("DELETE FROM visits WHERE id=?", (item_id,))
        known = {row[0] for row in conn.execute("SELECT screenshot FROM visits")}
        candidates = list(folder.glob("*.jpg"))
        artifact_root = folder / "mcp-artifacts"
        if artifact_root.is_dir() and not artifact_root.is_symlink():
            candidates.extend(artifact_root.glob("*/*"))
        files = []
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if not candidate.resolve().is_relative_to(root):
                continue
            if candidate.parent == folder and not re.fullmatch(
                r"[a-f0-9]{32}\.jpg", candidate.name
            ):
                continue
            stat = candidate.stat()
            files.append((stat.st_mtime, stat.st_size, candidate))
        total = sum(size for _, size, _ in files)
        for modified, size, candidate in sorted(files):
            # Allow delivery/readers to finish before reclaiming any artifact.
            if modified > now - 300:
                continue
            orphan = candidate.parent == folder and str(candidate) not in known
            if modified < now - MAX_AGE or orphan or total > MAX_BYTES:
                candidate.unlink(missing_ok=True)
                conn.execute("DELETE FROM visits WHERE screenshot=?", (str(candidate),))
                removed += 1
                total -= size
    return {"removed_files": removed, "retained_bytes": total, "limit_bytes": MAX_BYTES}
