"""Deterministic audio fixtures and loopback model transport, without model weights."""

import io
import json
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from services.jobs.store import JobError
from services.voice.job_execution import JobExecution


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x01\x00\xff" * 400)
    return output.getvalue()


def wait_for(read, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.01)
    raise AssertionError(f"Timed out, last state: {value}")


class ModelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path):
        super().__init__(("127.0.0.1", 0), Handler)
        self.root = root
        self.protocol = "cover-v1"
        self.reference, self.signature = "reference", "signature"
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.calls = 0
        self.failure: str | None = None
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_port}"

    def close(self):
        self.release.set()
        self.shutdown()
        self.thread.join(2)
        self.server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, content, **headers):
        self.send_response(status)
        self.send_header("Content-Length", str(len(content)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        server = cast(ModelServer, self.server)
        self.reply(
            200,
            json.dumps(
                {
                    "job_protocol": server.protocol,
                    "reference_sha256": server.reference,
                    "runtime_signature": server.signature,
                }
            ).encode(),
        )

    def do_POST(self):
        server = cast(ModelServer, self.server)
        if self.headers.get("Authorization") != "Bearer test-key":
            self.reply(401, b"Unauthorized")
            return
        parameters = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            control = JobExecution(
                server.root,
                self.headers.get("X-Job-Id"),
                self.headers.get("X-Job-Token"),
            )
            control.validate(parameters, server.reference, server.signature)
            control.checkpoint("converting", 1, 2)
            server.calls += 1
            server.started.set()
            if not server.release.wait(5):
                raise JobError("test_timeout")
            control.checkpoint("converting", 2, 2)
        except JobError as error:
            server.failure = str(error)
            self.reply(409, json.dumps({"detail": str(error)}).encode())
            return
        self.reply(
            200,
            wav_bytes(),
            **{
                "Content-Type": "audio/wav",
                "X-Job-Control": server.protocol,
                "X-Voice-Engine": "fixture-wav",
            },
        )
