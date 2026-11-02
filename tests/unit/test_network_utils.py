import ssl
from unittest.mock import Mock

import pytest

from astrbot.core.utils import network_utils


def test_create_proxy_client_reuses_shared_ssl_context(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_calls: list[dict] = []
    headers = {"X-Test-Header": "value"}

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            captured_calls.append(kwargs)

    monkeypatch.setattr(network_utils.httpx, "AsyncClient", _FakeAsyncClient)

    network_utils.create_proxy_client("OpenAI")
    network_utils.create_proxy_client("OpenAI", proxy="http://127.0.0.1:7890")
    network_utils.create_proxy_client("OpenAI", headers=headers)
    network_utils.create_proxy_client("OpenAI", proxy="")

    assert len(captured_calls) == 4
    assert "proxy" not in captured_calls[0]
    assert captured_calls[1]["proxy"] == "http://127.0.0.1:7890"
    assert captured_calls[2]["headers"] is headers
    assert "proxy" not in captured_calls[3]
    assert isinstance(captured_calls[0]["verify"], ssl.SSLContext)
    assert captured_calls[0]["verify"] is captured_calls[1]["verify"]
    assert captured_calls[1]["verify"] is captured_calls[2]["verify"]
    assert captured_calls[2]["verify"] is captured_calls[3]["verify"]


def test_create_proxy_client_allows_verify_override(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_calls: list[dict] = []
    custom_verify = ssl.create_default_context()

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            captured_calls.append(kwargs)

    monkeypatch.setattr(network_utils.httpx, "AsyncClient", _FakeAsyncClient)

    network_utils.create_proxy_client("OpenAI", verify=custom_verify)

    assert len(captured_calls) == 1
    assert captured_calls[0]["verify"] is custom_verify


@pytest.mark.parametrize("source", ["explicit", "environment"])
def test_proxy_credentials_reach_transport_but_never_diagnostics(monkeypatch, source):
    proxy = "http://private-user:private-pass%40word@127.0.0.1:17897"
    logs = Mock()
    monkeypatch.setattr(network_utils, "logger", logs)
    transport = Mock()
    client = network_utils.create_proxy_client("test", proxy, httpx_module=transport)
    assert client is transport.AsyncClient.return_value
    assert transport.AsyncClient.call_args.kwargs["proxy"] == proxy
    monkeypatch.setenv("http_proxy", proxy)
    network_utils.log_connection_failure(
        "test",
        OSError(f"cannot connect to {proxy}"),
        proxy if source == "explicit" else None,
    )
    messages = "\n".join(str(call.args[0]) for call in logs.mock_calls)
    assert "private-user" not in messages
    assert "private-pass" not in messages
    assert "127.0.0.1:17897" in messages
    assert "OSError" in messages
    assert "[redacted]" in messages
