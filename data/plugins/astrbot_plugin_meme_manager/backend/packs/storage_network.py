import time
from pathlib import Path

import requests

from .storage_common import (
    ARCHIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS,
    ARCHIVE_DOWNLOAD_READ_TIMEOUT_SECONDS,
    ARCHIVE_DOWNLOAD_SOURCE_TIMEOUT_SECONDS,
    MAX_ARCHIVE_COMPRESSED_BYTES,
    InstallCancelCheck,
    InstallCancelledError,
    InstallProgressCallback,
)
from .storage_files import _safe_nonnegative_int


def _get_with_connection_retry(url: str, *, timeout, stream: bool):
    """Retry one idempotent GET after a connection or response-header timeout.

    Args:
        url: Public resource URL already selected by the caller.
        timeout: Existing connect/read timeout or timeout pair.
        stream: Whether the caller owns streaming the response body.

    Returns:
        The actual HTTP response, including non-success status codes.

    Raises:
        requests.RequestException: Both connection attempts failed. Failures
            while consuming a streamed body stay with the download owner.
    """
    try:
        return requests.get(url, timeout=timeout, stream=stream)
    except (requests.ConnectionError, requests.Timeout):
        return requests.get(url, timeout=timeout, stream=stream)


def _build_accelerated_url(raw_url: str, github_accelerator_url: str) -> str:
    accelerator = str(github_accelerator_url or "").strip()
    url = str(raw_url or "").strip()
    if not accelerator or not url:
        return url
    if "{url}" in accelerator:
        return accelerator.replace("{url}", url)
    if accelerator.endswith("/"):
        return f"{accelerator}{url}"
    return f"{accelerator}/{url}"


def _http_get_with_optional_acceleration(
    raw_url: str,
    timeout: int,
    github_accelerator_url: str = "",
    stream: bool = False,
) -> requests.Response:
    """请求 GitHub 资源，并在加速地址失败时回退原始地址。

    Args:
        raw_url: 原始资源地址。
        timeout: 请求超时秒数。
        github_accelerator_url: 可选的 GitHub 加速地址。
        stream: 是否以流式响应返回，用于分块下载大文件。

    Returns:
        成功建立连接的 HTTP 响应。

    Raises:
        ValueError: 加速地址和原始地址均无法访问时抛出。
    """
    request_url = _build_accelerated_url(raw_url, github_accelerator_url)
    last_error = None

    if request_url and request_url != raw_url:
        try:
            accelerated_response = _get_with_connection_retry(
                request_url, timeout=timeout, stream=stream
            )
            if accelerated_response.status_code == 200:
                return accelerated_response
            last_error = ValueError(
                f"加速地址请求失败，状态码: {accelerated_response.status_code}"
            )
            accelerated_response.close()
        except Exception as exc:
            last_error = exc

    try:
        native_response = _get_with_connection_retry(
            raw_url, timeout=timeout, stream=stream
        )
        return native_response
    except Exception as exc:
        if last_error is not None:
            raise ValueError(f"加速与原生请求均失败: {last_error}; {exc}") from exc
        raise


def _download_github_archive(
    repo: str,
    ref: str,
    target_zip_path: Path,
    github_accelerator_url: str = "",
    progress_callback: InstallProgressCallback | None = None,
    cancel_check: InstallCancelCheck | None = None,
) -> None:
    """分块下载 GitHub 仓库压缩包并报告真实字节进度。

    Args:
        repo: GitHub 仓库名，格式为 ``owner/repo``。
        ref: 要下载的分支、标签或提交。
        target_zip_path: 压缩包写入路径。
        github_accelerator_url: 可选的 GitHub 加速地址。
        progress_callback: 下载阶段、已下载字节和总字节的回调。
        cancel_check: 返回 True 时终止下载的协作式取消检查。

    Raises:
        InstallCancelledError: 操作者取消安装时抛出。
        ValueError: 下载失败、超时或压缩包超过安全大小限制时抛出。
    """
    archive_url = f"https://github.com/{repo}/archive/{ref}.zip"
    accelerated_url = _build_accelerated_url(archive_url, github_accelerator_url)
    download_urls = [accelerated_url] if accelerated_url != archive_url else []
    download_urls.append(archive_url)
    failures = []
    target_zip_path.parent.mkdir(parents=True, exist_ok=True)

    for download_url in download_urls:
        response = None
        try:
            if cancel_check and cancel_check():
                raise InstallCancelledError("安装已取消")
            response = _get_with_connection_retry(
                download_url,
                timeout=(
                    ARCHIVE_DOWNLOAD_CONNECT_TIMEOUT_SECONDS,
                    ARCHIVE_DOWNLOAD_READ_TIMEOUT_SECONDS,
                ),
                stream=True,
            )
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            total_bytes = _safe_nonnegative_int(response.headers.get("content-length"))
            if total_bytes is not None and total_bytes > MAX_ARCHIVE_COMPRESSED_BYTES:
                raise ValueError("GitHub 压缩包超过 1 GB 安全限制")

            downloaded_bytes = 0
            started_at = time.monotonic()
            if progress_callback:
                progress_callback("downloading", downloaded_bytes, total_bytes)
            with target_zip_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if cancel_check and cancel_check():
                        raise InstallCancelledError("安装已取消")
                    if (
                        time.monotonic() - started_at
                        > ARCHIVE_DOWNLOAD_SOURCE_TIMEOUT_SECONDS
                    ):
                        raise TimeoutError("下载源超过 30 分钟仍未完成")
                    if not chunk:
                        continue
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > MAX_ARCHIVE_COMPRESSED_BYTES:
                        raise ValueError("GitHub 压缩包超过 1 GB 安全限制")
                    file_obj.write(chunk)
                    if progress_callback:
                        progress_callback("downloading", downloaded_bytes, total_bytes)
            return
        except InstallCancelledError:
            target_zip_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            target_zip_path.unlink(missing_ok=True)
            source_name = "加速源" if download_url != archive_url else "GitHub 原生源"
            failures.append(f"{source_name}: {exc}")
        finally:
            if response is not None:
                response.close()

    raise ValueError("下载 GitHub 压缩包失败；" + "；".join(failures))
