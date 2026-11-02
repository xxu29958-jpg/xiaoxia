import json
import os
import shutil
import tempfile
from pathlib import Path

from ... import config as pack_paths
from .protocol import (
    validate_pack_manifest,
)


def _load_json(path: Path, default):
    try:
        with path.open(encoding="utf-8-sig") as file_obj:
            return json.load(file_obj)
    except Exception:
        return default


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _restore_file_snapshot(path: Path, content: bytes | None) -> None:
    """根据精确字节快照恢复单个事务控制文件。

    Args:
        path: 要恢复的运行时控制文件。
        content: 原始字节；文件原本不存在时为 ``None``。
    """
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".rollback", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _safe_nonnegative_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return None
    return parsed if parsed >= 0 else None


def _index_bundle_details(index_root: Path) -> tuple[dict, Path | None]:
    """从旧版或快照包中解析清单指定的 FAISS 文件。

    Args:
        index_root: 解压后的语义索引目录。

    Returns:
        解析后的清单，以及清单中安全且实际存在的 FAISS 文件（如有）。
    """
    manifest = _load_json(index_root / "index_manifest.json", {})
    if not isinstance(manifest, dict):
        return {}, None
    filename = str(manifest.get("index_file") or "index.faiss").strip()
    if (
        not filename
        or Path(filename).name != filename
        or not filename.endswith(".faiss")
    ):
        return manifest, None
    index_path = index_root / filename
    return manifest, index_path if index_path.is_file() else None


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            total += max(0, file_path.stat().st_size)
        except OSError:
            continue
    return total


def _require_regular_tree(path: Path, operation: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{operation}目录不能是符号链接")
    if not path.is_dir():
        return
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"{operation}包含符号链接，已拒绝处理: {item.name}")


def _require_free_space(path: Path, required_bytes: int, operation: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < max(0, int(required_bytes)):
        required_gib = required_bytes / (1024**3)
        free_gib = free_bytes / (1024**3)
        raise ValueError(
            f"剩余磁盘空间不足，无法{operation}：预计至少需要 {required_gib:.2f} GB，"
            f"当前可用 {free_gib:.2f} GB"
        )


def _load_manifest(pack_id: str) -> dict:
    manifest_path = pack_paths.PACKS_DIR / pack_id / "manifest.json"
    manifest = _load_json(manifest_path, {})
    if not isinstance(manifest, dict):
        return {}
    try:
        return validate_pack_manifest(manifest)
    except Exception:
        return manifest


def _count_images(memes_dir: Path) -> int:
    if not memes_dir.is_dir():
        return 0
    total = 0
    for category_dir in memes_dir.iterdir():
        if not category_dir.is_dir():
            continue
        for file_path in category_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in {
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".webp",
            }:
                total += 1
    return total
