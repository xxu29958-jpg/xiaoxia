"""Call the shared local Ada speech service."""

from pathlib import Path

import aiohttp

from .cloud_voice import VoiceError


class LocalVoice:
    def __init__(self, config):
        self.config = config
        self.last = {}

    def status(self):
        return {
            "provider": "local",
            "model": "IndexTTS-2",
            **self.last,
        }

    async def synthesize(self, text: str, mode: str = "speech") -> bytes:
        """Generate audio using the fixed local voice profile.

        Args:
            text: Words to say.
            mode: Speech or a short original Mandarin song.

        Returns:
            PCM WAV bytes from the local service.

        Raises:
            VoiceError: The local service is unavailable or generation failed.
        """
        key = (
            Path(self.config.get("voice_key_file", ".runtime/voice/service.key"))
            .read_text(encoding="utf-8")
            .strip()
        )
        try:
            async with aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=120)
            ) as client:
                async with client.post(
                    self.config.get("voice_base_url", "http://127.0.0.1:6190").rstrip(
                        "/"
                    )
                    + "/speech",
                    headers={"Authorization": "Bearer " + key},
                    json={"text": text, "mode": mode},
                ) as response:
                    if response.status != 200:
                        raise VoiceError(f"local_voice_http_{response.status}")
                    audio = await response.read()
                    self.last = {
                        "model": response.headers.get("X-Voice-Engine", "local"),
                        "mode": mode,
                        "generation_seconds": float(
                            response.headers.get("X-Generation-Seconds", "0")
                        ),
                        "audio_seconds": float(
                            response.headers.get("X-Audio-Seconds", "0")
                        ),
                    }
                    return audio
        except (aiohttp.ClientError, TimeoutError):
            raise VoiceError("local_voice_unavailable") from None

    async def cover(
        self,
        source: Path | None,
        *,
        acapella=False,
        pitch=0,
        start_seconds=0.0,
        duration_seconds=None,
        source_url="",
    ) -> bytes:
        """Convert a complete performance through the reusable local voice API.

        Args:
            source: Downloaded song or opera recording.
            acapella: Whether the recording is already isolated singing.
            pitch: Requested vocal transposition in semitones.
            start_seconds: Start of the requested excerpt, normally zero.
            duration_seconds: Requested excerpt length; None preserves the complete recording.
            source_url: A public recording URL, including supported video pages.

        Returns:
            The complete converted WAV, including accompaniment when present.

        Raises:
            VoiceError: The source cannot be converted or the worker is unavailable.
        """
        key = (
            Path(self.config.get("voice_key_file", ".runtime/voice/service.key"))
            .read_text(encoding="utf-8")
            .strip()
        )
        try:
            async with aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=3600)
            ) as client:
                async with client.post(
                    self.config.get("voice_base_url", "http://127.0.0.1:6190").rstrip(
                        "/"
                    )
                    + "/cover",
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "source_path": str(source) if source else "",
                        "source_url": source_url,
                        "acapella": acapella,
                        "pitch": pitch,
                        "start_seconds": start_seconds,
                        "duration_seconds": duration_seconds,
                    },
                ) as response:
                    if response.status != 200:
                        detail = (await response.json()).get("detail")
                        raise VoiceError(
                            "source_unavailable"
                            if detail == "source_unavailable"
                            else f"local_cover_http_{response.status}"
                        )
                    audio = await response.read()
                    self.last = {
                        "model": response.headers.get("X-Voice-Engine", "Seed-VC"),
                        "mode": "cover",
                        "generation_seconds": float(
                            response.headers["X-Generation-Seconds"]
                        ),
                        "audio_seconds": float(response.headers["X-Audio-Seconds"]),
                    }
                    return audio
        except (aiohttp.ClientError, TimeoutError):
            raise VoiceError("local_cover_unavailable") from None
