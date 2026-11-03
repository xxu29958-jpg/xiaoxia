"""Install pinned source archives; never replace existing or externally owned files."""

import hashlib
import io
import os
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

IGNORED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
MAX_SOURCE_BYTES = 32 * 1024 * 1024
METADATA_FILES = {
    "dependency_links.txt",
    "PKG-INFO",
    "requires.txt",
    "SOURCES.txt",
    "top_level.txt",
}


def archive_files(data: bytes, specification: dict) -> dict[str, bytes]:
    if hashlib.sha256(data).hexdigest() != specification["archive_sha256"]:
        raise ValueError("Source archive does not match its pinned SHA-256")
    files = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            original = member.orig_filename
            name = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                not name.parts
                or name.parts[0] != specification["archive_root"]
                or any(p in {".", ".."} for p in original.split("/"))
                or "\\" in original
                or ":" in original
                or "\0" in original
                or stat.S_ISLNK(mode)
            ):
                raise ValueError("Unsafe source archive member")
            if member.is_dir():
                continue
            relative = name.relative_to(specification["archive_root"]).as_posix()
            total += member.file_size
            if relative in files or total > MAX_SOURCE_BYTES:
                raise ValueError("Duplicate or oversized source archive member")
            files[relative] = archive.read(member)
    if not files:
        raise ValueError("Empty source archive")
    return files


def same_source(actual: bytes, expected: bytes) -> bool:
    if actual == expected:
        return True
    try:
        actual.decode("utf-8")
        expected.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\0" not in actual and actual.replace(b"\r\n", b"\n") == expected.replace(
        b"\r\n", b"\n"
    )


def verify_source(target: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        file = target / name
        if (
            file.is_symlink()
            or not file.resolve().is_relative_to(target.resolve())
            or not file.is_file()
            or not same_source(file.read_bytes(), data)
        ):
            raise ValueError(
                f"Existing source differs; preserved without changes: {name}"
            )
    for directory, children, names in os.walk(target):
        children[:] = [name for name in children if name not in IGNORED]
        for name in [*children, *names]:
            file = Path(directory) / name
            relative = file.relative_to(target)
            metadata = (
                len(relative.parts) == 2
                and relative.parts[0].endswith(".egg-info")
                and name in METADATA_FILES
            )
            if file.is_symlink() or (
                file.is_file() and relative.as_posix() not in files and not metadata
            ):
                raise ValueError(
                    "Unexpected file in pinned source; preserved unchanged"
                )


def install_source(target: Path, files: dict[str, bytes], *, allow_create: bool) -> str:
    if target.is_symlink():
        raise ValueError("Source directory must not be a symbolic link")
    if target.exists():
        verify_source(target, files)
        return "verified"
    if not allow_create:
        raise ValueError(
            "External source path is missing; only managed sources may be created"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-", dir=target.parent) as temporary:
        staging = Path(temporary) / "source"
        staging.mkdir()
        for name, data in files.items():
            file = staging / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        verify_source(staging, files)
        staging.rename(target)
    return "installed"


def fetch_archive(specification: dict) -> bytes:
    with urllib.request.urlopen(specification["archive_url"], timeout=60) as response:
        data = response.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("Source archive exceeds the download limit")
    return data
