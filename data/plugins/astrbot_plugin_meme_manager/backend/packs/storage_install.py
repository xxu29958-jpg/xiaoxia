import tempfile
import zipfile
from pathlib import Path

from ... import config as pack_paths
from .protocol import (
    is_official_pack_entry,
    validate_pack_directory,
    validate_source_descriptor,
)
from .storage_archive import _extract_zip_safely
from .storage_common import (
    InstallCancelCheck,
    InstallCancelledError,
    InstallProgressCallback,
    PackOperationGuard,
)
from .storage_community import (
    fetch_and_cache_community_index,
    load_cached_community_index,
)
from .storage_import import import_pack_archive
from .storage_network import _download_github_archive


def install_pack_from_github_source(
    source: dict,
    overwrite: bool = False,
    set_as_default: bool = False,
    operation_guard: PackOperationGuard | None = None,
    github_accelerator_url: str = "",
    progress_callback: InstallProgressCallback | None = None,
    cancel_check: InstallCancelCheck | None = None,
) -> dict:
    """从 GitHub 来源下载并安装表情包。

    Args:
        source: 已声明仓库、引用和子目录的 GitHub 来源描述。
        overwrite: 是否覆盖同 ID 的已安装表情包。
        set_as_default: 是否在安装后将表情包设为默认。
        operation_guard: 写入资源包前调用的并发操作保护器。
        github_accelerator_url: 可选的 GitHub 加速地址。
        progress_callback: 安装阶段、已下载字节和总字节的回调。
        cancel_check: 返回 True 时终止安装的协作式取消检查。

    Returns:
        已安装表情包的信息。

    Raises:
        FileExistsError: 目标表情包已存在且未允许覆盖时抛出。
        FileNotFoundError: 来源中的表情包目录不存在时抛出。
        ValueError: 来源、压缩包或表情包结构无效时抛出。
    """
    github_source = validate_source_descriptor(source)
    repo = github_source["repo"]
    ref = github_source["ref"]
    subpath = github_source["subpath"]
    if progress_callback:
        progress_callback("connecting", 0, None)

    pack_paths.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=pack_paths.TEMP_DIR, prefix="community_install_"
    ) as tmp_dir:
        tmp_root = Path(tmp_dir)
        remote_zip = tmp_root / "remote.zip"
        _download_github_archive(
            repo,
            ref,
            remote_zip,
            github_accelerator_url=github_accelerator_url,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

        if progress_callback:
            progress_callback("extracting", 0, None)
        if cancel_check and cancel_check():
            raise InstallCancelledError("安装已取消")
        extract_dir = tmp_root / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        # 远程仓库可能包含与 pack 无关的脚本文件；这里只做路径安全校验。
        _extract_zip_safely(
            remote_zip,
            extract_dir,
            block_executable_scripts=False,
        )

        roots = [child for child in extract_dir.iterdir() if child.is_dir()]
        if len(roots) != 1:
            raise ValueError("GitHub 压缩包结构异常")

        source_pack_dir = (roots[0] / subpath).resolve()
        try:
            source_pack_dir.relative_to(roots[0].resolve())
        except ValueError as exc:
            raise ValueError("source.subpath 越界") from exc
        if not source_pack_dir.is_dir():
            raise FileNotFoundError("source.subpath 对应目录不存在")
        validate_pack_directory(source_pack_dir, context="GitHub 包目录")

        if progress_callback:
            progress_callback("preparing", 0, None)
        if cancel_check and cancel_check():
            raise InstallCancelledError("安装已取消")
        local_zip = tmp_root / "pack.zip"
        with zipfile.ZipFile(local_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in source_pack_dir.rglob("*"):
                if file_path.is_dir():
                    continue
                arc_name = file_path.relative_to(source_pack_dir).as_posix()
                zip_file.write(file_path, arcname=arc_name)

        if progress_callback:
            progress_callback("installing", 0, None)
        if cancel_check and cancel_check():
            raise InstallCancelledError("安装已取消")
        from .updates import snapshot

        result = import_pack_archive(
            local_zip,
            overwrite=overwrite,
            set_as_default=set_as_default,
            operation_guard=operation_guard,
            community_origin={"source": github_source, **snapshot(source_pack_dir)},
        )
        result["source"] = github_source
        return result


def install_first_official_pack_from_index(
    index_url: str,
    overwrite: bool = False,
    set_as_default: bool = True,
    operation_guard: PackOperationGuard | None = None,
    github_accelerator_url: str = "",
) -> dict:
    """从社区索引安装首个官方包；若无官方条目则回退索引首项。"""
    cache_loaded = True
    try:
        cache_data = load_cached_community_index()
    except Exception:
        cache_loaded = False
        cache_data = fetch_and_cache_community_index(
            index_url,
            github_accelerator_url=github_accelerator_url,
        )

    packs = cache_data.get("index", {}).get("packs", [])
    if not isinstance(packs, list) or not packs:
        raise ValueError("社区索引中没有可安装的表情包")

    selected_entry = None
    for entry in packs:
        if is_official_pack_entry(entry):
            selected_entry = entry
            break
    if selected_entry is None:
        selected_entry = packs[0]

    source = selected_entry.get("source")
    if not isinstance(source, dict):
        raise ValueError("选中的社区条目缺少 source 信息")

    result = install_pack_from_github_source(
        source=source,
        overwrite=overwrite,
        set_as_default=set_as_default,
        operation_guard=operation_guard,
        github_accelerator_url=github_accelerator_url,
    )
    result["selected_pack_id"] = str(selected_entry.get("id") or "").strip()
    result["selected_pack_name"] = str(
        selected_entry.get("name") or result.get("name") or result.get("pack_id")
    )
    result["selected_is_official"] = is_official_pack_entry(selected_entry)
    result["from_cache"] = cache_loaded
    return result
