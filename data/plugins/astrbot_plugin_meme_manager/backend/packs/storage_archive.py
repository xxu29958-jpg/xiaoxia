import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from ... import config as pack_paths
from ..semantic.storage import (
    LEGACY_METADATA_BACKUP_NAME,
    load_metadata,
)
from .protocol import (
    PACK_TRANSFER_FORMAT,
    PACK_TRANSFER_MANIFEST,
    validate_pack_directory,
    validate_pack_id,
    validate_pack_manifest,
    validate_transfer_manifest,
)
from .storage_common import (
    ARCHIVE_JSON_SIZE_LIMITS,
    IMAGE_EXTENSIONS,
    MAX_ARCHIVE_COMPRESSED_BYTES,
    MAX_ARCHIVE_FILE_COUNT,
    MAX_ARCHIVE_SINGLE_FILE_BYTES,
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    MIN_FREE_SPACE_RESERVE_BYTES,
)
from .storage_files import (
    _count_images,
    _index_bundle_details,
    _load_json,
    _require_free_space,
    _save_json,
)


def _archive_root_candidates(extract_root: Path) -> list[Path]:
    candidates = [extract_root]
    candidates.extend(
        child
        for child in extract_root.iterdir()
        if child.is_dir() and child.name != "__MACOSX"
    )
    return candidates


def _find_manifest_root(extract_root: Path) -> Path:
    candidates = [
        root
        for root in _archive_root_candidates(extract_root)
        if (root / "manifest.json").is_file()
    ]

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError("压缩包中存在多个 manifest 根目录")
    raise ValueError("压缩包中未找到 manifest.json")


def _legacy_root_has_categories(root: Path) -> bool:
    metadata_path = root / "memes_data.json"
    if not metadata_path.is_file():
        return False
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if any(
            file_path.is_file() and file_path.suffix.lower() in IMAGE_EXTENSIONS
            for file_path in child.rglob("*")
        ):
            return True
    return False


def _find_import_root(extract_root: Path) -> tuple[Path, str]:
    try:
        pack_root = _find_manifest_root(extract_root)
        transfer_path = pack_root / PACK_TRANSFER_MANIFEST
        detected_format = "v2" if transfer_path.is_file() else "v1"
        return pack_root, detected_format
    except ValueError as manifest_error:
        legacy_candidates = [
            root
            for root in _archive_root_candidates(extract_root)
            if (root / "memes").is_dir() or _legacy_root_has_categories(root)
        ]
        if len(legacy_candidates) == 1:
            return legacy_candidates[0], "legacy"
        if len(legacy_candidates) > 1:
            raise ValueError("压缩包中存在多个可能的旧版表情包目录") from manifest_error
        raise ValueError(
            "无法识别压缩包：新版包需要 manifest.json，旧版包需要 memes 目录"
        ) from manifest_error


def _legacy_pack_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").lower())
    normalized = normalized.strip("._-")[:64]
    if len(normalized) < 2:
        normalized = "legacy-pack"
    return validate_pack_id(normalized, "旧版表情包")


def _legacy_descriptions(root: Path, memes_dir: Path) -> dict[str, str]:
    raw = _load_json(root / "memes_data.json", {})
    if not isinstance(raw, dict):
        raw = {}
    descriptions = {
        str(category): str(description or "请添加描述")
        for category, description in raw.items()
        if str(category).strip()
    }
    if memes_dir.is_dir():
        for category_dir in memes_dir.iterdir():
            if category_dir.is_dir():
                descriptions.setdefault(category_dir.name, "请添加描述")
    return descriptions


def _copy_legacy_pack(
    legacy_root: Path,
    target_root: Path,
    suggested_pack_id: str,
) -> dict:
    pack_id = _legacy_pack_id(suggested_pack_id)
    source_memes = legacy_root / "memes"
    direct_category_layout = not source_memes.is_dir()
    if direct_category_layout:
        source_memes = legacy_root

    target_memes = target_root / "memes"
    target_memes.mkdir(parents=True, exist_ok=True)
    for source_path in sorted(source_memes.rglob("*")):
        if (
            not source_path.is_file()
            or source_path.suffix.lower() not in IMAGE_EXTENSIONS
        ):
            continue
        relative = source_path.relative_to(source_memes)
        if not relative.parts or relative.parts[0].startswith("."):
            continue
        if len(relative.parts) == 1:
            relative = Path("default") / relative
        target_path = target_memes / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)

    descriptions = _legacy_descriptions(legacy_root, target_memes)
    if not descriptions:
        descriptions = {
            path.name: "请添加描述" for path in target_memes.iterdir() if path.is_dir()
        }
    if not descriptions:
        raise ValueError("旧版压缩包中没有可导入的表情图片")

    manifest = {
        "schema_version": 1,
        "id": pack_id,
        "name": f"旧版导入包 ({pack_id})",
        "version": "1.0.0",
        "description": "由旧版无语义化表情包自动转换",
        "tags": ["legacy", "converted"],
        "categories": {
            category: {"description": description}
            for category, description in sorted(descriptions.items())
        },
    }
    _save_json(target_root / "manifest.json", manifest)
    _save_json(target_root / "memes_data.json", descriptions)
    return manifest


def _extract_zip_safely(
    zip_path: Path, target_dir: Path, block_executable_scripts: bool = True
) -> None:
    archive_size = zip_path.stat().st_size
    if archive_size > MAX_ARCHIVE_COMPRESSED_BYTES:
        raise ValueError("压缩包体积超过 1 GB，无法安全导入")
    with zipfile.ZipFile(zip_path, "r") as zip_file:
        members = zip_file.infolist()
        if len(members) > MAX_ARCHIVE_FILE_COUNT:
            raise ValueError("压缩包文件数量过多")
        total_uncompressed = sum(max(0, member.file_size) for member in members)
        if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("压缩包解压后的体积过大")
        # 导入时会同时保留解压目录、规范化目录和向量校验副本。提前预留
        # 三倍解压体积以及固定安全余量，避免把 AstrBot 所在磁盘写满。
        _require_free_space(
            target_dir,
            total_uncompressed * 3 + MIN_FREE_SPACE_RESERVE_BYTES,
            "解压表情包",
        )
        for member in members:
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("压缩包包含非法路径")
            if member.filename.endswith("/"):
                continue
            if member.file_size > MAX_ARCHIVE_SINGLE_FILE_BYTES:
                raise ValueError(f"压缩包中的单个文件过大: {member_path.name}")
            json_limit = ARCHIVE_JSON_SIZE_LIMITS.get(member_path.name)
            if json_limit is not None and member.file_size > json_limit:
                raise ValueError(f"压缩包中的 {member_path.name} 体积异常")
            suffix = member_path.suffix.lower()
            if (
                block_executable_scripts
                and suffix
                and suffix in {".exe", ".bat", ".cmd", ".ps1", ".sh"}
            ):
                raise ValueError("压缩包包含不允许的可执行脚本文件")
        zip_file.extractall(target_dir)


def _load_transfer_info(pack_root: Path, detected_format: str) -> dict:
    if detected_format != "v2":
        return {
            "format": PACK_TRANSFER_FORMAT,
            "format_version": 1,
            "export_mode": "share",
            "features": {
                "semantic_metadata": (pack_root / "semantic_metadata.json").is_file(),
                "vectors": False,
            },
        }
    transfer_info = _load_json(pack_root / PACK_TRANSFER_MANIFEST, {})
    return validate_transfer_manifest(transfer_info)


def _prepare_import_pack(
    pack_root: Path,
    detected_format: str,
    target_root: Path,
    suggested_pack_id: str,
) -> tuple[dict, dict]:
    if detected_format == "legacy":
        manifest = _copy_legacy_pack(pack_root, target_root, suggested_pack_id)
        transfer_info = {
            "format": PACK_TRANSFER_FORMAT,
            "format_version": 0,
            "export_mode": "share",
            "features": {"semantic_metadata": False, "vectors": False},
        }
        return manifest, transfer_info

    transfer_info = _load_transfer_info(pack_root, detected_format)

    def ignore_transfer_files(directory: str, names: list[str]) -> set[str]:
        if Path(directory).resolve() != pack_root.resolve():
            return set()
        return {
            name
            for name in names
            if name
            in {PACK_TRANSFER_MANIFEST, "semantic_index", ".community-origin.json"}
        }

    shutil.copytree(pack_root, target_root, ignore=ignore_transfer_files)
    (target_root / LEGACY_METADATA_BACKUP_NAME).unlink(missing_ok=True)
    manifest = _load_json(target_root / "manifest.json", {})
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json 格式无效")
    return validate_pack_manifest(manifest), transfer_info


def inspect_pack_archive(zip_path: Path, suggested_pack_id: str | None = None) -> dict:
    """只读检查导入包，返回 WebUI 确认导入所需的兼容性摘要。"""
    if not zip_path.is_file():
        raise FileNotFoundError("压缩包不存在")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("文件不是有效的 zip 压缩包")

    pack_paths.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=pack_paths.TEMP_DIR, prefix="pack_inspect_"
    ) as tmp_dir:
        workspace = Path(tmp_dir)
        extract_root = workspace / "extract"
        extract_root.mkdir()
        _extract_zip_safely(zip_path, extract_root)
        pack_root, detected_format = _find_import_root(extract_root)
        prepared_root = workspace / "prepared"
        manifest, transfer_info = _prepare_import_pack(
            pack_root,
            detected_format,
            prepared_root,
            suggested_pack_id or zip_path.stem,
        )
        validate_pack_directory(prepared_root, context="待导入表情包")

        image_count = _count_images(prepared_root / "memes")
        categories = manifest.get("categories", {})
        semantic_path = prepared_root / "semantic_metadata.json"
        semantic_data = load_metadata(prepared_root) if semantic_path.is_file() else {}
        if semantic_data.get("metadata_read_only"):
            raise ValueError(str(semantic_data.get("metadata_error") or "语义文件无效"))
        semantic_images = (
            semantic_data.get("images", {}) if isinstance(semantic_data, dict) else {}
        )
        semantic_done = sum(
            1
            for item in semantic_images.values()
            if isinstance(item, dict) and item.get("caption_status") == "done"
        )
        declared_vectors = bool(transfer_info.get("features", {}).get("vectors", False))
        _, bundled_index_path = _index_bundle_details(pack_root / "semantic_index")
        vector_files_present = bundled_index_path is not None
        warnings = []
        if detected_format == "legacy":
            warnings.append("已识别为旧版无语义化压缩包，导入时会自动转换为新版结构。")
        if declared_vectors and not vector_files_present:
            warnings.append(
                "压缩包声明包含向量，但缺少完整索引文件；将按无向量包导入。"
            )

        return {
            "detected_format": detected_format,
            "format_version": int(transfer_info.get("format_version", 0) or 0),
            "export_mode": str(transfer_info.get("export_mode") or "share"),
            "pack_id": str(manifest.get("id") or ""),
            "name": str(manifest.get("name") or manifest.get("id") or ""),
            "version": str(manifest.get("version") or "1.0.0"),
            "image_count": image_count,
            "category_count": len(categories) if isinstance(categories, dict) else 0,
            "semantic_metadata": semantic_path.is_file(),
            "semantic_done": semantic_done,
            "vectors_declared": declared_vectors,
            "vectors_present": vector_files_present,
            "warnings": warnings,
        }
