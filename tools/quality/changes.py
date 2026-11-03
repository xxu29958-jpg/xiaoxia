"""Select working-tree changes without changing Git's index or source files."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .git_snapshot import BlobReader, git, revision
from .ownership import TEXT


@dataclass(frozen=True)
class Change:
    path: str
    previous_path: str | None
    status: str


def changed_paths(repo: Path, base: str) -> list[Change]:
    raw = git(repo, "diff", "--name-status", "-z", "--find-renames=30%", base, "--")
    fields = iter(raw.decode("utf-8").split("\0"))
    result: dict[str, Change] = {}
    for status in fields:
        if not status:
            continue
        old = next(fields)
        name = next(fields) if status.startswith(("R", "C")) else old
        previous = None if status == "A" else old
        result[name] = Change(name, previous, status)
    for name in (
        git(repo, "ls-files", "--others", "--exclude-standard", "-z")
        .decode("utf-8")
        .split("\0")
    ):
        if name:
            result[name] = Change(name, None, "A")
    recover_unstaged_renames(repo, base, result)
    return sorted(result.values(), key=lambda item: item.path)


def move_digest(path: str, data: bytes) -> bytes:
    """Ignore CRLF conversion for text moves; retain all other content differences."""
    if Path(path).suffix in TEXT and b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).digest()


def recover_unstaged_renames(repo: Path, base: str, result: dict[str, Change]) -> None:
    """Pair untracked moves without staging them, accounting for Git's text EOLs."""
    deleted: dict[bytes, list[str]] = {}
    with BlobReader(repo) as reader:
        for item in list(result.values()):
            if item.status == "D":
                old = load_before(repo, base, item, reader)
                if old is not None:
                    deleted.setdefault(move_digest(item.path, old), []).append(
                        item.path
                    )
    for item in list(result.values()):
        if item.status != "A":
            continue
        data = source_bytes(repo, item.path)
        matches = (
            deleted.get(move_digest(item.path, data), []) if data is not None else []
        )
        if matches:
            previous = matches.pop(0)
            result.pop(previous)
            result[item.path] = Change(item.path, previous, "R100")


def source_bytes(repo: Path, name: str) -> bytes | None:
    target = repo / name
    if not target.resolve().is_relative_to(repo.resolve()):
        raise ValueError(f"Source points outside the checkout: {name}")
    if not target.exists():
        return None
    if target.is_symlink() or not target.is_file():
        raise ValueError(f"Expected an ordinary source file: {name}")
    return target.read_bytes()


def load_before(
    repo: Path, base: str, change: Change, reader: BlobReader
) -> bytes | None:
    if change.previous_path is None:
        return None
    oid = git(repo, "rev-parse", f"{base}:{change.previous_path}").decode().strip()
    return reader.read(oid)


def capture(repo: Path, base: str) -> dict:
    commit = revision(repo, base)
    changes = changed_paths(repo, commit)
    return {
        "base": commit,
        "head": revision(repo, "HEAD"),
        "changes": changes,
        "content_sha256": {
            change.path: hashlib.sha256(data).hexdigest() if data is not None else None
            for change in changes
            for data in [source_bytes(repo, change.path)]
        },
    }
