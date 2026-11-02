"""Exercise connection recovery with a real HTTP peer, including failure limits."""

import socket
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests
from astrbot_plugin_meme_manager.backend.packs.storage_network import (
    _http_get_with_optional_acceleration,
)


@pytest.fixture
def peer(monkeypatch):
    attempts = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            attempts[self.path] += 1
            if self.path == "/down" or (
                self.path == "/flaky" and attempts[self.path] == 1
            ):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(403 if self.path == "/auth" else 200)
            body = b'{"packs":[{"id":"recovered-resource"}]}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", attempts
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


def test_broken_connection_recovers_real_resource_without_manual_retry(peer):
    url, attempts = peer
    with _http_get_with_optional_acceleration(url + "/flaky", timeout=1) as result:
        assert result.status_code == 200
        assert result.json()["packs"] == [{"id": "recovered-resource"}]
    assert attempts["/flaky"] == 2


def test_persistent_connection_failure_stops_and_reports_failure(peer):
    url, attempts = peer
    with pytest.raises(requests.ConnectionError):
        _http_get_with_optional_acceleration(url + "/down", timeout=1)
    assert attempts["/down"] == 2


def test_authorization_rejection_is_returned_without_connection_retry(peer):
    url, attempts = peer
    with _http_get_with_optional_acceleration(url + "/auth", timeout=1) as result:
        assert result.status_code == 403
    assert attempts["/auth"] == 1
