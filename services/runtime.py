"""Shared installation paths for the bot and the reusable voice services."""

import json
import os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.environ.get("XIAOXIA_CONFIG", PROJECT / "xiaoxia.local.json"))
CONFIG = {
    "bot_root": ".",
    "voice_state": ".runtime/voice",
    "memory_state": ".runtime/memory",
    "voice_profile": "assets/voices/profile.json",
    "speech_engine": "cosy",
    "index_repo": "vendor/index_tts",
    "index_models": "models/index-tts",
    "index_aux": "models/index-aux",
    "cosy_repo": "vendor/cosyvoice",
    "cosy_models": "models/cosyvoice3",
    "fish_repo": "vendor/fish_speech",
    "fish_models": "models/fish-s2",
    "seed_repo": "vendor/seed_vc",
    "seed_models": "models/seed-vc",
    "soul_repo": "vendor/soulx_singer",
    "soul_models": "models/soulx",
    "separator_models": "models/separator",
    "recognition_models": "models/sensevoice",
    "memory_models": "models/e5-small",
    "python_bot": ".venvs/bot/Scripts/python.exe",
    "python_gateway": ".venvs/gateway/Scripts/python.exe",
    "python_index": ".venvs/index/Scripts/python.exe",
    "python_cosy": ".venvs/cosy/Scripts/python.exe",
    "python_fish": ".venvs/fish/Scripts/python.exe",
    "python_singing": ".venvs/singing/Scripts/python.exe",
    "python_memory": ".venvs/memory/Scripts/python.exe",
    "ffmpeg": "ffmpeg",
    "download_proxy": "",
}
if CONFIG_FILE.is_file():
    CONFIG.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")))


def path(name: str) -> Path:
    """Resolve a configured path against the installed project directory."""
    return (PROJECT / CONFIG[name]).resolve()


def voice_profile() -> tuple[dict, Path]:
    """Return the selected profile and its exact reference recording."""
    source = path("voice_profile")
    profile = json.loads(source.read_text(encoding="utf-8"))
    return profile, (source.parent / profile["reference_audio"]).resolve()
