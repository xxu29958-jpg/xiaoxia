"""Own temporary speech files, conversion subprocesses and retention."""

import asyncio
import subprocess
import time
import uuid

from astrbot.api.message_components import File, Record


class AudioFiles:
    def __init__(self, folder, config):
        self.folder, self.config = folder, config

    async def create(self, audio, text, platform, *, song=False):
        folder = self.folder / "audio"
        folder.mkdir(exist_ok=True)
        output = folder / f"ada-{uuid.uuid4().hex}.wav"
        output.write_bytes(audio)
        if platform == "weixin_oc" or song:
            mp3 = output.with_suffix(".mp3")
            process = await asyncio.create_subprocess_exec(
                self.config.get("ffmpeg_path", "ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(output),
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2" if song else "4",
                str(mp3),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                await asyncio.wait_for(process.communicate(), timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.wait()
                raise
            if process.returncode or not mp3.exists() or mp3.stat().st_size < 1000:
                raise ValueError("MP3 conversion failed")
            component = File(
                name=f"小夏 - {text[:64]}.mp3" if song else "小夏.mp3", file=str(mp3)
            )
        else:
            component = Record(file=str(output), url=str(output), text=text)
        for path in folder.glob("ada-*.*"):
            if path.suffix not in (".wav", ".mp3"):
                continue
            if path.stat().st_mtime < time.time() - 7 * 86400:
                path.unlink(missing_ok=True)
        return component
