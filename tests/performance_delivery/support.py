"""Real gateway HTTP and native AstrBot events, with a deterministic WAV engine."""

import asyncio
import socket
import threading
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from services.jobs.artifacts import describe_artifact
from services.jobs.runner import JobRunner
from services.jobs.store import JobStore
from services.voice.job_api import JobService, router


def event(channel, sender="1234567890", message_id="performance-request"):
    message = AstrBotMessage()
    message.type = MessageType.FRIEND_MESSAGE
    message.self_id = "test-bot"
    message.sender = MessageMember(sender, "Owner")
    message.message = [Plain("请把这段完整唱出来")]
    message.message_str = "请把这段完整唱出来"
    message.message_id = message_id
    return AstrMessageEvent(
        message.message_str,
        message,
        PlatformMetadata(channel, "fixture", channel),
        sender,
    )


async def until(read, predicate):
    value = None
    try:
        async with asyncio.timeout(10):
            while True:
                value = read()
                if predicate(value):
                    return value
                await asyncio.sleep(0.02)
    except TimeoutError:
        raise AssertionError(f"Last state: {value}") from None


class Gateway:
    def __init__(self, root):
        self.root = root
        self.store = JobStore(root / "state.sqlite3")
        self.started, self.release = threading.Event(), threading.Event()
        self.fail_generation = False
        self.runner = JobRunner(self.store, self.render)
        app = FastAPI(lifespan=self.lifespan)
        app.include_router(router(self.authenticate))
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", access_log=False)
        )
        self.thread = threading.Thread(
            target=lambda: self.server.run(sockets=[self.socket]), daemon=True
        )
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self.server.started:
            raise RuntimeError("Fixture gateway failed to start")

    @staticmethod
    def authenticate(authorization: str | None = Header(default=None)):
        if authorization != "Bearer fixture-key":
            raise HTTPException(401, "Unauthorized")

    @asynccontextmanager
    async def lifespan(self, app):
        app.state.media_jobs = JobService(
            self.store, self.runner, self.root, "reference", "signature"
        )
        self.runner.start()
        try:
            yield
        finally:
            self.release.set()
            await asyncio.to_thread(self.runner.close)

    def render(self, job, check):
        check("converting", 1, 2)
        self.started.set()
        deadline = time.monotonic() + 20
        while not self.release.wait(0.02):
            check()
            if time.monotonic() > deadline:
                raise TimeoutError("fixture_timeout")
        check("converting", 2, 2)
        if self.fail_generation:
            raise ValueError("controlled generation failure")
        output = self.root / (job["id"] + ".wav")
        output.write_bytes(Path(job["payload"]["source_path"]).read_bytes())
        return describe_artifact(self.root, output, engine="fixture-wav")

    def close(self):
        self.release.set()
        self.server.should_exit = True
        self.thread.join(5)
        self.socket.close()
        if self.thread.is_alive():
            raise RuntimeError("Fixture gateway did not close")


def audio_file(path, seconds=61):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\1\0\xff" * 8000 * seconds)
    return path
