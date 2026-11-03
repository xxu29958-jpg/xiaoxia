"""Exercise the installed downloader over loopback, then recover without the URL."""

import functools
import hashlib
import os
import tempfile
import threading
import unittest
import wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from services.jobs.store import InterruptedJob, JobStore
from services.voice.cover_source import prepare_source
from services.voice.job_execution import JobExecution


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class SourceTests(unittest.TestCase):
    def test_download_binds_exact_recording_then_recovery_uses_pinned_local_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "recording.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\x00\x01\x00\xff" * 400)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root))
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/recording.wav"
            parameters = {"source_path": "", "source_url": url}
            store = JobStore(root / "jobs/state.sqlite3")
            store.submit("owner", "download", parameters, parameters)
            job = store.claim()
            assert job is not None
            control = JobExecution(root / "jobs", job["id"], job["token"])
            try:
                # Inherited system proxies must not route the local fixture externally.
                with patch.dict(
                    os.environ,
                    {
                        "NO_PROXY": "127.0.0.1,localhost",
                        "no_proxy": "127.0.0.1,localhost",
                    },
                ):
                    downloaded, elapsed = prepare_source(
                        parameters,
                        root / "jobs/download",
                        {"ffmpeg": "ffmpeg", "download_proxy": ""},
                        control,
                    )
            finally:
                server.shutdown()
                thread.join(2)
                server.server_close()
            self.assertEqual(downloaded.read_bytes(), source.read_bytes())
            self.assertGreater(elapsed, 0)
            pinned = store.claimed_payload(job["id"], job["token"])
            self.assertEqual(pinned["source_url"], "")
            self.assertEqual(pinned["origin_url"], url)
            self.assertEqual(
                pinned["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
            )
            # The HTTP server is now closed. Recovery must not fetch the original URL.
            recovered, elapsed = prepare_source(
                pinned, root / "jobs/recovered", {}, control
            )
            self.assertEqual(recovered, downloaded)
            self.assertEqual(elapsed, 0)
            store.cancel(job["id"], "owner")
            with self.assertRaisesRegex(InterruptedJob, "job_cancelled"):
                prepare_source(parameters, root / "jobs/cancelled", {}, control)


if __name__ == "__main__":
    unittest.main()
