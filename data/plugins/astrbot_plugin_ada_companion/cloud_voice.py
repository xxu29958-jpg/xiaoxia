"""CosyVoice synthesis with a durable, conservative local character allowance."""

import contextlib
import io
import re
import sqlite3
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .policy import LOCAL_TZ

MODEL = "cosyvoice-v3-flash"
VOICES = {"longanrou_v3", "longyingtao_v3", "longyingling_v3"}
MAX_AUDIO_BYTES = 20 * 1024 * 1024


class VoiceError(RuntimeError):
    """Expose only a fixed error code, never provider responses or signed URLs."""


class CloudVoice:
    def __init__(self, folder: Path, config: dict):
        self.config = config
        self.path = folder / "voice-usage.sqlite3"
        self.healthy = True
        try:
            with contextlib.closing(sqlite3.connect(self.path, timeout=5)) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS usage ("
                    "id TEXT PRIMARY KEY, month TEXT NOT NULL, "
                    "characters INTEGER NOT NULL, state TEXT NOT NULL, "
                    "created_at REAL NOT NULL)"
                )
                db.commit()
        except sqlite3.Error:
            self.healthy = False

    def status(self):
        month = datetime.now(LOCAL_TZ).strftime("%Y-%m")
        used = None
        try:
            with contextlib.closing(sqlite3.connect(self.path, timeout=5)) as db:
                used = db.execute(
                    "SELECT COALESCE(SUM(characters), 0) FROM usage WHERE month=?",
                    (month,),
                ).fetchone()[0]
        except sqlite3.Error:
            self.healthy = False
        return {
            "provider": "Alibaba Cloud Bailian (Beijing)",
            "model": MODEL,
            "voice": self.config.get("cloud_voice", "longanrou_v3"),
            "key_configured": bool(self.config.get("cloud_api_key", "").strip()),
            "workspace_configured": bool(
                self.config.get("cloud_workspace_id", "").strip()
            ),
            "month": month,
            "reserved_characters": used,
            "monthly_character_limit": self.config.get("voice_monthly_limit", 50000),
            "usage_healthy": self.healthy,
            "account_billing_limit": False,
        }

    async def synthesize(self, text: str):
        """Reserve allowance before submitting one non-retried synthesis request.

        Args:
            text: Short plain text to speak, without SSML or acting instructions.

        Returns:
            Validated 24 kHz PCM WAV bytes.

        Raises:
            VoiceError: Configuration, allowance, network or audio validation failed.
        """
        key = self.config.get("cloud_api_key", "").strip()
        workspace = self.config.get("cloud_workspace_id", "").strip().lower()
        voice = self.config.get("cloud_voice", "longanrou_v3")
        if not key or not workspace:
            raise VoiceError("not_configured")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", workspace):
            raise VoiceError("invalid_workspace")
        if voice not in VOICES or "\n" in key or "\r" in key:
            raise VoiceError("invalid_configuration")
        if not text.strip() or len(text) > 650:
            raise VoiceError("invalid_text_length")
        try:
            limit = int(self.config.get("voice_monthly_limit", 50000))
            if not 1 <= limit <= 1000000:
                raise ValueError
        except (ValueError, TypeError):
            raise VoiceError("invalid_limit") from None
        if not self.healthy:
            raise VoiceError("usage_unavailable")
        month = datetime.now(LOCAL_TZ).strftime("%Y-%m")
        reservation = uuid.uuid4().hex
        try:
            # A database transaction also protects reloads and overlapping workers.
            with contextlib.closing(sqlite3.connect(self.path, timeout=5)) as db:
                db.execute("BEGIN IMMEDIATE")
                used = db.execute(
                    "SELECT COALESCE(SUM(characters), 0) FROM usage WHERE month=?",
                    (month,),
                ).fetchone()[0]
                if used + len(text) > limit:
                    raise VoiceError("monthly_limit")
                db.execute(
                    "INSERT INTO usage VALUES (?, ?, ?, 'reserved', ?)",
                    (reservation, month, len(text), time.time()),
                )
                db.commit()
        except sqlite3.Error:
            raise VoiceError("usage_unavailable") from None

        try:
            async with aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=90)
            ) as client:
                async with client.post(
                    f"https://{workspace}.cn-beijing.maas.aliyuncs.com"
                    "/api/v1/services/audio/tts/SpeechSynthesizer",
                    headers={"Authorization": "Bearer " + key},
                    json={
                        "model": MODEL,
                        "input": {
                            "text": text,
                            "voice": voice,
                            "format": "wav",
                            "sample_rate": 24000,
                            "volume": 50,
                            "rate": 1.0,
                            "pitch": 1.0,
                        },
                    },
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise VoiceError(f"provider_http_{response.status}")
                    payload = await response.json()
                audio = await self._download_audio(client, payload)
        except VoiceError:
            raise
        except (aiohttp.ClientError, TimeoutError):
            raise VoiceError("network_error") from None
        except Exception:
            raise VoiceError("invalid_response") from None

        # Do not release reservations on failures: the provider may have billed.
        try:
            with contextlib.closing(sqlite3.connect(self.path, timeout=5)) as db:
                db.execute(
                    "UPDATE usage SET state='generated' WHERE id=?", (reservation,)
                )
                db.commit()
        except sqlite3.Error:
            self.healthy = False
        return bytes(audio)

    async def _download_audio(self, client, payload):
        """Validate the provider URL and bounded WAV without forwarding its API key."""
        output = payload.get("output", {})
        if output.get("finish_reason") != "stop":
            raise VoiceError("incomplete_response")
        url = urlsplit(output.get("audio", {}).get("url", ""))
        if (
            url.scheme not in ("http", "https")
            or not (url.hostname or "").endswith(".aliyuncs.com")
            or url.port not in (None, 80, 443)
            or url.username
            or url.password
        ):
            raise VoiceError("invalid_audio_url")
        # OSS supports HTTPS. Never send the API key to the audio host.
        download_url = urlunsplit(("https", url.hostname, url.path, url.query, ""))
        async with client.get(download_url, allow_redirects=False) as response:
            if response.status != 200:
                raise VoiceError(f"audio_http_{response.status}")
            audio = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                audio.extend(chunk)
                if len(audio) > MAX_AUDIO_BYTES:
                    raise VoiceError("audio_too_large")
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (
                wav.getframerate() != 24000
                or wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or not 0.1 < wav.getnframes() / 24000 <= 300
                or len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2
            ):
                raise VoiceError("invalid_audio")
        return bytes(audio)
