"""Kill a sender after a real loopback side effect, around local receipt commitment."""

import json
import multiprocessing as mp
import queue
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from data.plugins.astrbot_plugin_ada_companion.performance_store import PerformanceStore


class ReceiptSink(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), ReceiptHandler)
        self.calls = queue.Queue()


class ReceiptHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        cast(ReceiptSink, self.server).calls.put(body)
        receipt = b'{"kind":"server_accepted","message_id":"42","played":false}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(receipt)))
        self.end_headers()
        self.wfile.write(receipt)


def sending_worker(database, url, ready, committed):
    # Keep the claim valid until the chosen crash point, independent of host speed.
    store = PerformanceStore(Path(database), clock=lambda: 100.0)
    store.submit("owner", {"origin": "fixture"}, "native-event", "track", {}, {})
    claim = store.claim()
    assert claim is not None
    store.observe_generation(
        claim["id"], claim["token"], {"id": "job", "state": "completed"}
    )
    store.preparing(claim["id"], claim["token"])
    store.begin_send(
        claim["id"], claim["token"], {"format": "mp3", "sha256": "fixture"}
    )
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open(
        urllib.request.Request(url, data=b"one-local-side-effect", method="POST"),
        timeout=3,
    ) as response:
        receipt = json.load(response)
    if committed:
        store.receipt(claim["id"], claim["token"], receipt)
    ready.set()
    mp.Event().wait(60)


class SendCrashTests(unittest.TestCase):
    def test_crash_after_channel_response_before_or_after_local_receipt_commit(self):
        context = mp.get_context("spawn")
        for committed in (False, True):
            with (
                self.subTest(committed=committed),
                tempfile.TemporaryDirectory() as folder,
            ):
                sink = ReceiptSink()
                thread = threading.Thread(target=sink.serve_forever, daemon=True)
                thread.start()
                database = Path(folder) / "requests.sqlite3"
                ready = context.Event()
                process = context.Process(
                    target=sending_worker,
                    args=(
                        str(database),
                        f"http://127.0.0.1:{sink.server_port}/send",
                        ready,
                        committed,
                    ),
                )
                try:
                    process.start()
                    self.assertTrue(ready.wait(10))
                    self.assertEqual(
                        sink.calls.get(timeout=2), b"one-local-side-effect"
                    )
                    self.assertTrue(process.is_alive())
                finally:
                    if process.is_alive():
                        process.terminate()
                    process.join(10)
                    sink.shutdown()
                    thread.join(2)
                    sink.server_close()
                self.assertFalse(process.is_alive())
                self.assertIsNotNone(process.exitcode)
                self.assertNotEqual(process.exitcode, 0)
                now = 100.0
                restored = PerformanceStore(database, clock=lambda: now)
                self.assertEqual(
                    restored.get("owner")["state"],
                    "accepted" if committed else "sending",
                )
                self.assertIsNone(restored.claim())
                now = 161.0
                self.assertIsNone(restored.claim())
                saved = restored.get("owner")
                self.assertEqual(saved["state"], "accepted" if committed else "unknown")
                self.assertIsNone(restored.claim())
                self.assertEqual(saved["artifact"]["sha256"], "fixture")
                self.assertTrue(sink.calls.empty())


if __name__ == "__main__":
    unittest.main()
