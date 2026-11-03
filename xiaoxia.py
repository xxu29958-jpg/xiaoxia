"""Install configuration, run Xiaoxia, and use its local voice API."""

import argparse
import copy
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from services.runtime import CONFIG, CONFIG_FILE, PROJECT, path

SERVICES = {
    "gateway": ("services/voice/server_voice.py", 6190, "/health"),
    "index": ("services/voice/server_index.py", 6191, "/health"),
    "singing": ("services/voice/server_singing.py", 6192, "/health"),
    "memory": ("services/memory/server.py", 11435, "/health"),
    "bot": ("main.py", 6185, "/"),
}
SPEECH_ENGINES = {"index": "server_index.py", "cosy": "server_cosy.py"}
SERVICES["index"] = (
    "services/voice/" + SPEECH_ENGINES[CONFIG["speech_engine"]],
    6191,
    "/health",
)
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def initialize():
    """Create local keys and initial settings without replacing existing data."""
    for kind, filename in [
        ("voice_state", "service.key"),
        ("memory_state", "local-key.txt"),
    ]:
        folder = path(kind)
        (folder / "temp").mkdir(parents=True, exist_ok=True)
        key = folder / filename
        if not key.exists():
            key.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    (PROJECT / ".runtime/logs").mkdir(parents=True, exist_ok=True)
    local = (
        json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        if CONFIG_FILE.exists()
        else {}
    )
    changed = not CONFIG_FILE.exists()
    if CONFIG["ffmpeg"] == "ffmpeg":
        encoder = shutil.which("ffmpeg")
        if not encoder:
            try:
                import imageio_ffmpeg

                bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
                encoder = PROJECT / ".runtime/bin/ffmpeg.exe"
                encoder.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bundled, encoder)
                encoder = str(encoder)
            except ImportError:
                pass
        if encoder:
            local["ffmpeg"] = encoder
            CONFIG["ffmpeg"] = encoder
            changed = True
    if changed:
        CONFIG_FILE.write_text(json.dumps(local, indent=2), encoding="utf-8")
    data = path("bot_root") / "data"
    (data / "config").mkdir(parents=True, exist_ok=True)
    main_config = data / "cmd_config.json"
    if not main_config.exists():
        from astrbot.core.config.default import DEFAULT_CONFIG

        config = copy.deepcopy(DEFAULT_CONFIG)
        config["agent_runner"]["config"]["persona"]["persona_id"] = "Ada"
        key = (path("voice_state") / "service.key").read_text().strip()
        config["provider_sources"].append(
            {
                "id": "xiaoxia-voice",
                "type": "openai_whisper_api",
                "provider": "openai",
                "provider_type": "speech_to_text",
                "enable": True,
                "api_base": "http://127.0.0.1:6190/v1",
                "api_key": key,
                "proxy": "",
                "timeout": 120,
            }
        )
        config["provider"].append(
            {
                "id": "xiaoxia-stt",
                "enable": True,
                "model": "ada-sensevoice",
                "provider_source_id": "xiaoxia-voice",
            }
        )
        config["provider_stt_settings"] = {"enable": True, "provider_id": "xiaoxia-stt"}
        main_config.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    plugin_config = data / "config/astrbot_plugin_ada_companion_config.json"
    if not plugin_config.exists():
        schema = json.loads(
            (
                PROJECT / "data/plugins/astrbot_plugin_ada_companion/_conf_schema.json"
            ).read_text(encoding="utf-8")
        )
        config = {name: setting["default"] for name, setting in schema.items()}
        config.update(
            voice_enabled=True,
            chat_replyer_enabled=True,
            voice_key_file=str(path("voice_state") / "service.key"),
            voice_base_url="http://127.0.0.1:6190",
            ffmpeg_path=CONFIG["ffmpeg"],
        )
        plugin_config.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        "Local configuration ready. Open http://127.0.0.1:6185 after starting to configure your chat model and accounts."
    )


def status(names):
    """Return actual HTTP readiness for the selected services."""
    rows = {}
    for name in names:
        _, port, route = SERVICES[name]
        try:
            with HTTP.open(f"http://127.0.0.1:{port}{route}", timeout=2) as response:
                body = response.read()
                rows[name] = {"ready": True} if name == "bot" else json.loads(body)
        except (OSError, ValueError):
            rows[name] = {"ready": False}
    return rows


def start(names):
    """Start the installed workers with logs and wait for real HTTP readiness."""
    initialize()
    env = dict(
        os.environ,
        PYTHONUTF8="1",
        PYTHONUNBUFFERED="1",
        PYTHONPATH=str(PROJECT),
        ASTRBOT_ROOT=str(path("bot_root")),
        XIAOXIA_CONFIG=str(CONFIG_FILE),
    )
    if Path(CONFIG["ffmpeg"]).is_file():
        env["PATH"] = (
            str(Path(CONFIG["ffmpeg"]).parent) + os.pathsep + env.get("PATH", "")
        )
    processes = {}
    for name in names:
        current = status([name])[name]
        if current.get("ready") or current.get("status") == "ready":
            print(f"{name}: already listening")
            continue
        entry = PROJECT / SERVICES[name][0]
        python = path(
            "python_" + (CONFIG["speech_engine"] if name == "index" else name)
        )
        arguments = [str(python), str(entry)]
        if name == "bot":
            arguments += ["--webui-dir", str(PROJECT / "astrbot/dashboard/dist")]
        with (
            (PROJECT / f".runtime/logs/{name}.stdout.log").open("ab") as stdout,
            (PROJECT / f".runtime/logs/{name}.stderr.log").open("ab") as stderr,
        ):
            process = subprocess.Popen(
                arguments,
                cwd=PROJECT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                startupinfo=(hidden_startup() if os.name == "nt" else None),
            )
        processes[name] = process
        (PROJECT / f".runtime/{name}.pid").write_text(str(process.pid))
        print(f"{name}: starting", flush=True)
    deadline = time.monotonic() + 180
    while processes and time.monotonic() < deadline:
        for name, process in list(processes.items()):
            if process.poll() is not None:
                raise RuntimeError(
                    f"{name} exited; see .runtime/logs/{name}.stderr.log"
                )
            ready = status([name])[name]
            if ready.get("ready") or ready.get("status") == "ready":
                print(f"{name}: ready", flush=True)
                del processes[name]
        if processes:
            time.sleep(0.5)
    if processes:
        raise TimeoutError("Still loading: " + ", ".join(processes))


def hidden_startup():
    """Keep Windows service consoles hidden while allowing graceful shutdown."""
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info


def stop(names):
    """Stop the selected workers through their owned process entry points."""
    for name in reversed(names):
        pid = PROJECT / f".runtime/{name}.pid"
        if pid.is_file():
            python_name = CONFIG["speech_engine"] if name == "index" else name
            subprocess.run(
                [
                    str(path("python_bot")),
                    str(PROJECT / "maintenance/runtime/graceful_stop.py"),
                    pid.read_text().strip(),
                    str(path("python_" + python_name)),
                    str(PROJECT / SERVICES[name][0]),
                    "--timeout",
                    "30",
                ],
                check=True,
            )
            pid.unlink()


def main():
    parser = argparse.ArgumentParser(description="小夏：聊天、听、说、歌曲和戏曲")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    p = commands.add_parser(
        "voice-engine", help="Switch the speech worker and start it"
    )
    p.add_argument("engine", choices=list(SPEECH_ENGINES))
    for command in ["start", "stop", "status"]:
        p = commands.add_parser(command)
        p.add_argument(
            "component", choices=["all", "voice", *SERVICES], default="all", nargs="?"
        )
    p = commands.add_parser("speak")
    p.add_argument("text")
    p.add_argument("--output", type=Path, default=PROJECT / "outputs/speech.wav")
    p.add_argument(
        "--emotion",
        default="neutral",
        choices=[
            "neutral",
            "happy",
            "angry",
            "sad",
            "afraid",
            "disgusted",
            "melancholic",
            "surprised",
            "calm",
        ],
    )
    p.add_argument("--emotion-strength", type=float, default=0.08)
    p = commands.add_parser("hear")
    p.add_argument("source", type=Path)
    p = commands.add_parser("sing")
    p.add_argument("source", help="Local recording or a supported public media URL")
    p.add_argument("--acapella", action="store_true")
    p.add_argument("--start", type=float, default=0)
    p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Omit to sing the entire remaining recording",
    )
    p.add_argument("--pitch", type=int, default=0)
    p.add_argument("--output", type=Path, default=PROJECT / "outputs/song.wav")
    args = parser.parse_args()
    if args.command == "init":
        initialize()
        return
    if args.command == "voice-engine":
        if not path("python_" + args.engine).is_file():
            raise RuntimeError(
                "Speech environment missing; run setup.ps1 -WithCosy for CosyVoice3"
            )
        model_config = "cosyvoice3.yaml" if args.engine == "cosy" else "config.yaml"
        if not (path(args.engine + "_models") / model_config).is_file():
            raise RuntimeError(
                "Speech model missing; run tools/download_models.py --only "
                + args.engine
                + "_models"
            )
        if args.engine != CONFIG["speech_engine"]:
            stop(["index"])
            if status(["index"])["index"].get("status") == "ready":
                raise RuntimeError(
                    "The previous speech worker still listens; stop its original launcher first"
                )
            local = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            local["speech_engine"] = args.engine
            CONFIG_FILE.write_text(
                json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            CONFIG["speech_engine"] = args.engine
            SERVICES["index"] = (
                "services/voice/" + SPEECH_ENGINES[args.engine],
                6191,
                "/health",
            )
        start(["index"])
        print(json.dumps(status(["index"]), ensure_ascii=False, indent=2))
        return
    if args.command in {"start", "stop", "status"}:
        names = (
            list(SERVICES)
            if args.component == "all"
            else (
                ["gateway", "index", "singing"]
                if args.component == "voice"
                else [args.component]
            )
        )
        if args.command == "start":
            start(names)
        elif args.command == "status":
            print(json.dumps(status(names), ensure_ascii=False, indent=2))
        else:
            stop(names)
        return
    key = (path("voice_state") / "service.key").read_text().strip()
    headers = {"Authorization": "Bearer " + key}
    if args.command == "hear":
        boundary = uuid.uuid4().hex
        content = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="input{args.source.suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + args.source.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        route = "/v1/audio/transcriptions"
    else:
        headers["Content-Type"] = "application/json"
        if args.command == "speak":
            route, data = (
                "/speech",
                {
                    "text": args.text,
                    "emotion": args.emotion,
                    "emotion_strength": args.emotion_strength,
                },
            )
        else:
            is_url = args.source.startswith(("https://", "http://"))
            route, data = (
                "/cover",
                {
                    "source_url": args.source if is_url else "",
                    "source_path": "" if is_url else str(Path(args.source).resolve()),
                    "acapella": args.acapella,
                    "pitch": args.pitch,
                    "start_seconds": args.start,
                    "duration_seconds": args.duration,
                },
            )
        content = json.dumps(data).encode()
    started = time.perf_counter()
    request = urllib.request.Request(
        "http://127.0.0.1:6190" + route, data=content, headers=headers
    )
    with HTTP.open(request, timeout=3600) as response:
        audio = response.read()
        if args.command == "hear":
            print(audio.decode())
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(audio)
            print(
                json.dumps(
                    {
                        "output": str(args.output.resolve()),
                        "request_seconds": round(time.perf_counter() - started, 3),
                        "audio_seconds": response.headers.get("X-Audio-Seconds"),
                        "engine": response.headers.get("X-Voice-Engine"),
                        "emotion": response.headers.get("X-Voice-Emotion"),
                    },
                    ensure_ascii=False,
                )
            )


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(
            f"HTTP {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr
        )
        raise SystemExit(1) from None
