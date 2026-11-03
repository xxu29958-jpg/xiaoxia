"""Acquire a recording and commit its provenance before model execution."""

import json
import time
from pathlib import Path

from services.jobs.store import JobError
from services.voice.job_execution import JobExecution


def prepare_source(
    parameters: dict, output: Path, configuration: dict, control: JobExecution
) -> tuple[Path, float]:
    source = Path(parameters["source_path"])
    if not parameters["source_url"]:
        if not source.is_file():
            raise JobError("source_not_found")
        control.checkpoint("loading")
        return source, 0.0
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    output.mkdir(parents=True, exist_ok=True)
    control.checkpoint("downloading")
    started = time.perf_counter()
    try:
        with YoutubeDL(
            {
                "format": "bestaudio/best",
                "noplaylist": True,
                "outtmpl": str(output / "source.%(ext)s"),
                "ffmpeg_location": configuration["ffmpeg"],
                "proxy": configuration["download_proxy"] or None,
                "quiet": True,
                "socket_timeout": 20,
                "retries": 1,
                "fragment_retries": 1,
                "progress_hooks": [control.download_progress],
            }
        ) as downloader:
            info = downloader.extract_info(parameters["source_url"], download=True)
            if not info:
                raise JobError("source_unavailable")
            source = Path(downloader.prepare_filename(info))
            (output / "source-page.json").write_text(
                json.dumps(
                    {
                        "url": parameters["source_url"],
                        "title": info.get("title"),
                        "uploader": info.get("uploader"),
                        "duration": info.get("duration"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    except DownloadError as error:
        raise JobError("source_unavailable") from error
    control.checkpoint("loading")
    control.bind_source(source)
    return source, time.perf_counter() - started
