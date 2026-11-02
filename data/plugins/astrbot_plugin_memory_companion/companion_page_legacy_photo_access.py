from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import stat
import time
from pathlib import Path
from typing import Any

from .companion_page_bridge import (
    MEMORY_PAGE_PHOTO_MAX_BYTES,
    CompanionPageBridgeError,
)
from .companion_page_legacy_support import (
    _MAX_PHOTO_REFS,
    _PHOTO_TTL_SECONDS,
    _fail,
    _LegacyPhoto,
)


class LegacyCompanionPageAdapterPhotoAccess:
    """Photo access; shared runtime resources belong to the facade."""

    def _roots(self, plugin: Any) -> tuple[Path, ...]:
        candidates = [
            getattr(plugin, "data_dir", None),
            getattr(plugin, "plugin_data_dir", None),
        ]
        data_file = getattr(plugin, "data_file", None)
        if data_file:
            candidates.append(Path(str(data_file)).parent)
        roots: list[Path] = []
        for candidate in candidates:
            if not candidate:
                continue
            try:
                source = Path(str(candidate)).expanduser()
                source_state = source.lstat()
                if stat.S_ISLNK(source_state.st_mode) or not stat.S_ISDIR(
                    source_state.st_mode
                ):
                    continue
                resolved = source.resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                continue
            if resolved not in roots:
                roots.append(resolved)
        return tuple(roots)

    @staticmethod
    def _mime(content: bytes) -> str:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        ):
            return "image/webp"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"BM"):
            return "image/bmp"
        if (
            len(content) >= 12
            and content[4:8] == b"ftyp"
            and content[8:12] in {b"avif", b"avis"}
        ):
            return "image/avif"
        _fail("memory_page_photo_unsupported")

    @staticmethod
    def _open_nofollow(root: Path, parts: tuple[str, ...], *, changed: bool) -> int:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory is None or not parts:
            _fail(
                "memory_page_photo_changed"
                if changed
                else "memory_page_photo_unavailable"
            )
        cloexec = getattr(os, "O_CLOEXEC", 0)
        nonblock = getattr(os, "O_NONBLOCK", 0)
        directory_flags = os.O_RDONLY | directory | nofollow | cloexec
        file_flags = os.O_RDONLY | nofollow | cloexec | nonblock
        opened_directory = -1
        try:
            opened_directory = os.open(os.fspath(root), directory_flags)
            for part in parts[:-1]:
                next_directory = os.open(part, directory_flags, dir_fd=opened_directory)
                os.close(opened_directory)
                opened_directory = next_directory
            return os.open(parts[-1], file_flags, dir_fd=opened_directory)
        except OSError:
            _fail(
                "memory_page_photo_changed"
                if changed
                else "memory_page_photo_unavailable"
            )
        finally:
            if opened_directory >= 0:
                try:
                    os.close(opened_directory)
                except OSError:
                    pass

    @classmethod
    def _read_descriptor(
        cls,
        root: Path,
        parts: tuple[str, ...],
        *,
        expected: _LegacyPhoto | None = None,
    ) -> tuple[bytes, os.stat_result]:
        descriptor = cls._open_nofollow(root, parts, changed=expected is not None)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                _fail(
                    "memory_page_photo_changed"
                    if expected is not None
                    else "memory_page_photo_unavailable"
                )
            if before.st_size > MEMORY_PAGE_PHOTO_MAX_BYTES:
                _fail("memory_page_photo_too_large")
            if expected is not None and (
                before.st_dev != expected.device
                or before.st_ino != expected.inode
                or before.st_size != expected.size
                or before.st_mtime_ns != expected.mtime_ns
            ):
                _fail("memory_page_photo_changed")
            payload = bytearray()
            while len(payload) <= MEMORY_PAGE_PHOTO_MAX_BYTES:
                block = os.read(
                    descriptor,
                    min(1024 * 1024, MEMORY_PAGE_PHOTO_MAX_BYTES + 1 - len(payload)),
                )
                if not block:
                    break
                payload.extend(block)
            after = os.fstat(descriptor)
            if len(payload) > MEMORY_PAGE_PHOTO_MAX_BYTES:
                _fail("memory_page_photo_too_large")
            if (
                len(payload) != after.st_size
                or after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
            ):
                _fail("memory_page_photo_changed")
            return bytes(payload), before
        except CompanionPageBridgeError:
            raise
        except OSError:
            _fail(
                "memory_page_photo_changed"
                if expected is not None
                else "memory_page_photo_unavailable"
            )
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _safe_candidate(
        self,
        raw_path: Any,
        plugin: Any,
    ) -> tuple[Path, tuple[str, ...]]:
        if not isinstance(raw_path, (str, os.PathLike)):
            _fail("memory_page_photo_unavailable")
        try:
            text = os.fspath(raw_path)
        except TypeError:
            _fail("memory_page_photo_unavailable")
        if not isinstance(text, str) or not text or "\x00" in text:
            _fail("memory_page_photo_unavailable")
        raw_parts = [text]
        for separator in tuple(item for item in (os.sep, os.altsep) if item):
            raw_parts = [piece for part in raw_parts for piece in part.split(separator)]
        if any(part in {".", ".."} for part in raw_parts):
            _fail("memory_page_photo_unavailable")
        source = Path(text).expanduser()
        roots = self._roots(plugin)
        if not roots:
            _fail("memory_page_photo_unavailable")
        for root in roots:
            try:
                candidate = Path(
                    os.path.abspath(source if source.is_absolute() else root / source)
                )
                relative = candidate.relative_to(root)
            except (OSError, ValueError):
                continue
            parts = relative.parts
            try:
                invalid_parts = (
                    not parts
                    or len(parts) > 32
                    or any(
                        not part
                        or part in {".", ".."}
                        or len(part.encode("utf-8")) > 255
                        for part in parts
                    )
                )
            except UnicodeEncodeError:
                invalid_parts = True
            if invalid_parts:
                continue
            current = root
            safe = True
            try:
                for part in parts:
                    current = current / part
                    value = current.lstat()
                    if stat.S_ISLNK(value.st_mode):
                        safe = False
                        break
            except (OSError, RuntimeError, ValueError):
                continue
            if safe:
                return root, tuple(parts)
        _fail("memory_page_photo_unavailable")

    def _stage_photo_sync(self, raw_path: Any) -> tuple[str, _LegacyPhoto]:
        plugin = self._live_plugin()
        root, parts = self._safe_candidate(raw_path, plugin)
        content, identity = self._read_descriptor(root, parts)
        mime_type = self._mime(content)
        digest = hashlib.sha256(content).hexdigest()
        material = (
            f"{self.instance_generation}:{identity.st_dev}:{identity.st_ino}:"
            f"{identity.st_size}:"
            f"{identity.st_mtime_ns}:{digest}"
        ).encode("ascii")
        token = (
            base64.urlsafe_b64encode(
                hmac.new(self._secret, material, hashlib.sha256).digest()[:16]
            )
            .decode("ascii")
            .rstrip("=")
        )
        photo_ref = f"mphoto_{self.instance_generation[:12]}_{token}"
        record = _LegacyPhoto(
            root=root,
            parts=parts,
            device=identity.st_dev,
            inode=identity.st_ino,
            size=identity.st_size,
            mtime_ns=identity.st_mtime_ns,
            sha256=digest,
            mime_type=mime_type,
            expires_at=0.0,
        )
        return photo_ref, record

    def _commit_photo_refs(self, staged: list[tuple[str, _LegacyPhoto]]) -> None:
        now = time.monotonic()
        self._live_plugin()
        with self._lock:
            previous = self._photos.copy()
            try:
                self._live_plugin()
                for reference, record in staged:
                    self._photos[reference] = _LegacyPhoto(
                        root=record.root,
                        parts=record.parts,
                        device=record.device,
                        inode=record.inode,
                        size=record.size,
                        mtime_ns=record.mtime_ns,
                        sha256=record.sha256,
                        mime_type=record.mime_type,
                        expires_at=now + _PHOTO_TTL_SECONDS,
                    )
                    self._photos.move_to_end(reference)
                while len(self._photos) > _MAX_PHOTO_REFS:
                    self._photos.popitem(last=False)
                self._live_plugin()
            except BaseException:
                self._photos.clear()
                self._photos.update(previous)
                raise

    async def _thread_call(
        self,
        callback: Any,
        *args: Any,
    ) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(callback, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            raise cancellation
