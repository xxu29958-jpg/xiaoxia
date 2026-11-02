from collections.abc import Callable

from .protocol import (
    PACK_TRANSFER_MANIFEST,
)

PackOperationGuard = Callable[[str, str], None]


InstallProgressCallback = Callable[[str, int, int | None], None]


InstallCancelCheck = Callable[[], bool]


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


MAX_ARCHIVE_FILE_COUNT = 20_000


MAX_ARCHIVE_COMPRESSED_BYTES = 1024 * 1024 * 1024


MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


MAX_ARCHIVE_SINGLE_FILE_BYTES = 1024 * 1024 * 1024


MIN_FREE_SPACE_RESERVE_BYTES = 512 * 1024 * 1024


ARCHIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS = 15


ARCHIVE_DOWNLOAD_READ_TIMEOUT_SECONDS = 30


ARCHIVE_DOWNLOAD_SOURCE_TIMEOUT_SECONDS = 30 * 60


ARCHIVE_JSON_SIZE_LIMITS = {
    "manifest.json": 4 * 1024 * 1024,
    PACK_TRANSFER_MANIFEST: 1024 * 1024,
    "memes_data.json": 16 * 1024 * 1024,
    "semantic_metadata.json": 256 * 1024 * 1024,
    "index_manifest.json": 256 * 1024 * 1024,
}


class InstallCancelledError(RuntimeError):
    """Raised when an operator cancels an in-progress pack installation."""
