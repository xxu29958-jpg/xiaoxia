"""Offline Bilibili reader contract, destination, and bounded transport tests."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "plugins"))
from astrbot_plugin_ada_companion import bili_reader
from astrbot_plugin_ada_companion.bili_reader import BiliReader
from astrbot_plugin_ada_companion.public_transport import PublicTransport
from astrbot_plugin_ada_companion.web_notebook import BackpackError

BV = "BV1xx411c7mD"
CDN = "https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/example.json"


def response(data, *, status=200, mime="application/json"):
    return {
        "status": status,
        "headers": {"content-type": mime},
        "body": json.dumps(data, ensure_ascii=False).encode(),
    }


def metadata(**changes):
    return {
        "bvid": BV,
        "title": "Public title",
        "owner": {"name": "Creator"},
        "cid": 123,
        **changes,
    }


def listing(url=CDN, **changes):
    return {
        "subtitle": {"subtitles": [{"lan": "zh-CN", "subtitle_url": url}]},
        **changes,
    }


@pytest.fixture
def setup_reader(monkeypatch):
    transport = SimpleNamespace(fetch=AsyncMock())
    manager = AsyncMock()
    manager.__aenter__.return_value = transport
    factory = Mock(return_value=manager)
    monkeypatch.setattr(bili_reader, "PublicTransport", factory)
    services = SimpleNamespace(
        plugin=Mock(
            return_value=SimpleNamespace(
                config={"runtime": {"proxy": "http://127.0.0.1:7890"}}
            )
        )
    )
    reader = BiliReader(
        {
            "public_network_fallback_proxy": "http://127.0.0.1:7891",
            "bili_sessdata": "DO_NOT_SEND_TEST_SECRET",
        },
        services,
    )
    return reader, transport, factory, services


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "video",
    [
        "http://www.bilibili.com/video/" + BV,
        "https://b23.tv/example",
        "https://evil.test/video/" + BV,
        "https://www.bilibili.com.evil.test/video/" + BV,
        "https://user@www.bilibili.com/video/" + BV,
        "https://www.bilibili.com:443/video/" + BV,
        "https://www.bilibili.com/video/" + BV + "?p=2",
        "https://www.bilibili.com/video/" + BV + "#fragment",
        "https://www.bilibili.com/video/../" + BV,
        "//www.bilibili.com/video/" + BV,
        BV + "/",
        "av123",
        "file:///private",
        "",
        None,
        [],
    ],
)
async def test_invalid_video_never_connects(setup_reader, video):
    reader, transport, factory, _ = setup_reader
    result = await reader.read(video)
    assert not result["ok"] and result["evidence"] == "metadata_only"
    assert result["transcript"] == "" and result["error"]
    factory.assert_not_called()
    transport.fetch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "video",
    [
        BV,
        f"https://www.bilibili.com/video/{BV}",
        f"https://www.bilibili.com/video/{BV}/",
    ],
)
async def test_subtitle_contract_provenance_and_anonymous_proxy(setup_reader, video):
    reader, transport, factory, services = setup_reader
    transport.fetch.side_effect = [
        response({"code": 0, "data": metadata()}),
        response({"code": 0, "data": listing()}),
        response({"body": [{"content": "字幕原文"}, {"content": "Second line"}]}),
    ]
    result = await reader.read(video)
    assert result["ok"] and result["bvid"] == BV
    assert result["evidence"] == "subtitles"
    assert result["transcript"] == "字幕原文\nSecond line"
    assert not result["truncated"] and result["subtitle_language"] == "zh-CN"
    assert result["scope"] == "first_part_only" and result["untrusted_source"]
    assert len(result["sources"]) == 3 and result["sources"][-1]["url"] == CDN
    assert "error" not in result
    factory.assert_called_once_with(
        "http://127.0.0.1:7890",
        "http://127.0.0.1:7891",
        json_only=True,
        subtitle_signatures=True,
    )
    services.plugin.assert_called_once_with("astrbot_plugin_codex_oauth_plug")
    for call in transport.fetch.call_args_list:
        assert call.kwargs == {"document": False}
        assert "DO_NOT_SEND_TEST_SECRET" not in str(call)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected_length,truncated",
    [
        ([{"content": "x" * 12000}], 12000, False),
        ([{"content": "x" * 12001}], 12000, True),
        ([{"content": "x" * 12000}, {"content": "more"}], 12000, True),
        ([{"content": "a"}, {"content": "b" * 12000}], 12000, True),
    ],
)
async def test_transcript_cap(setup_reader, body, expected_length, truncated):
    reader, transport, _, _ = setup_reader
    transport.fetch.side_effect = [
        response({"code": 0, "data": metadata()}),
        response({"code": 0, "data": listing()}),
        response({"body": body}),
    ]
    result = await reader.read(BV)
    assert result["evidence"] == "subtitles"
    assert len(result["transcript"]) == expected_length
    assert result["truncated"] is truncated


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "player",
    [
        {},
        {"subtitle": {"subtitles": []}},
        {"need_login_subtitle": True},
        {"is_paid_course": True},
        {"subtitle": None},
        {"bvid": "BV1xx411c7mE"},
        {"cid": 999},
        {"cid": True},
        {"subtitle": {"subtitles": "bad"}},
        {"subtitle": {"subtitles": [None]}},
        {"subtitle": {"subtitles": [{"lan": [], "subtitle_url": CDN}]}},
    ],
)
async def test_missing_restricted_or_malformed_subtitles_are_metadata_only(
    setup_reader, player
):
    reader, transport, _, _ = setup_reader
    transport.fetch.side_effect = [
        response({"code": 0, "data": metadata()}),
        response({"code": 0, "data": player}),
    ]
    result = await reader.read(BV)
    assert result["ok"] and result["title"] == "Public title"
    assert result["evidence"] == "metadata_only" and not result["transcript"]
    assert "metadata" in result["error"].lower()
    assert transport.fetch.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"rights": {"pay": 1}},
        {"rights": {"ugc_pay": 1}},
        {"rights": {"ugc_pay_preview": 1}},
        {"is_upower_exclusive": True},
    ],
)
async def test_paid_content_skips_player(setup_reader, changes):
    reader, transport, _, _ = setup_reader
    transport.fetch.return_value = response({"code": 0, "data": metadata(**changes)})
    result = await reader.read(BV)
    assert result["ok"] and result["evidence"] == "metadata_only"
    assert "skipped" in result["error"] and transport.fetch.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "https://evil.test/bfs/subtitle/a.json",
        "https://aisubtitle.hdslb.com.evil.test/bfs/subtitle/a.json",
        "https://aisubtitle.hdslb.com@evil.test/bfs/subtitle/a.json",
        "https://user@aisubtitle.hdslb.com/bfs/subtitle/a.json",
        "https://aisubtitle.hdslb.com:443/bfs/subtitle/a.json",
        "https://aisubtitle.hdslb.com./bfs/subtitle/a.json",
        "http://aisubtitle.hdslb.com/bfs/subtitle/a.json",
        "https://127.0.0.1/bfs/subtitle/a.json",
        "https://api.bilibili.com/x/web-interface/view",
        "https://aisubtitle.hdslb.com/other/a.json",
        "https://aisubtitle.hdslb.com/bfs/subtitle/../a.json",
        "https://aisubtitle.hdslb.com/bfs/subtitle/%2e%2e/a.json",
        CDN + "#fragment",
        CDN + "?auth_key=signed",
        CDN + "\n",
    ],
)
async def test_subtitle_domain_url_validation_before_fetch(setup_reader, source):
    reader, transport, _, _ = setup_reader
    transport.fetch.side_effect = [
        response({"code": 0, "data": metadata()}),
        response({"code": 0, "data": listing(source)}),
    ]
    result = await reader.read(BV)
    assert result["evidence"] == "metadata_only" and result["error"]
    assert transport.fetch.call_count == 2


@pytest.mark.asyncio
async def test_protocol_relative_cdn_promoted_to_https(setup_reader):
    reader, transport, _, _ = setup_reader
    transport.fetch.side_effect = [
        response({"code": 0, "data": metadata()}),
        response({"code": 0, "data": listing(CDN.removeprefix("https:"))}),
        response({"body": [{"content": "source"}]}),
    ]
    result = await reader.read(BV)
    assert result["evidence"] == "subtitles"
    assert transport.fetch.call_args.args[0] == CDN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        None,
        "bad",
        [None],
        [{"content": []}],
        [{"content": "x" * 12001}, {"content": 123}],
        [],
    ],
)
async def test_malformed_or_empty_transcript_does_not_become_evidence(
    setup_reader, body
):
    reader, transport, _, _ = setup_reader
    transport.fetch.side_effect = [
        response({"code": 0, "data": metadata()}),
        response({"code": 0, "data": listing()}),
        response({"body": body}),
    ]
    result = await reader.read(BV)
    assert result["ok"] and result["evidence"] == "metadata_only"
    assert not result["transcript"] and not result["truncated"] and result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        [],
        {"code": False, "data": {}},
        {"code": 0, "data": []},
        {"code": -101},
        {"code": -412},
        {"code": 0, "data": metadata(bvid="BV1xx411c7mE")},
        {"code": 0, "data": metadata(title=[])},
        {"code": 0, "data": metadata(owner=None)},
        {"code": 0, "data": metadata(cid=True)},
        {"code": 0, "data": metadata(cid=-1)},
    ],
)
async def test_malformed_metadata_or_api_errors_fail_closed(setup_reader, data):
    reader, transport, _, _ = setup_reader
    transport.fetch.return_value = response(data)
    result = await reader.read(BV)
    assert not result["ok"] and not result["transcript"] and result["error"]
    assert transport.fetch.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"status": 302, "headers": {"location": "https://evil.test"}, "body": b""},
        {
            "status": 200,
            "headers": {"content-type": "text/html"},
            "body": b"<html>captcha</html>",
        },
        {"status": 200, "headers": {"content-type": "application/json"}, "body": b"{"},
        {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": b'{"code":0,"data":{},"ignored":NaN}',
        },
        {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": b'{"code":0,"data":{},"ignored":Infinity}',
        },
        {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": b"\xff",
        },
        {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "body": b" " * (1024 * 1024 + 1),
        },
    ],
)
async def test_redirect_html_malformed_json_and_oversize_rejected(
    setup_reader, payload
):
    reader, transport, _, _ = setup_reader
    transport.fetch.return_value = payload
    result = await reader.read(BV)
    assert not result["ok"] and result["error"]
    assert transport.fetch.call_count == 1


@pytest.mark.asyncio
async def test_network_error_is_sanitized(setup_reader):
    reader, transport, _, _ = setup_reader
    transport.fetch.side_effect = BackpackError(
        "proxy password or other private detail"
    )
    result = await reader.read(BV)
    assert not result["ok"] and "private detail" not in result["error"]


@pytest.mark.asyncio
async def test_cancel_is_not_swallowed(setup_reader):
    reader, transport, _, _ = setup_reader
    transport.fetch.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await reader.read(BV)


@pytest.mark.asyncio
async def test_search_bounded_canonical_and_metadata_only(setup_reader):
    reader, transport, _, _ = setup_reader
    items = [
        {
            "bvid": f"BV1xx411c7m{x}",
            "title": '<em class="keyword">Public</em> title',
            "author": "Creator",
            "arcurl": "https://evil.test",
        }
        for x in "DEFG"
    ]
    transport.fetch.return_value = response({"code": 0, "data": {"result": items}})
    result = await reader.search("公开 & 公共", limit=999)
    assert result["ok"] and len(result["videos"]) == 3
    assert all(
        x["title"] == "Public title" and x["evidence"] == "metadata_only"
        for x in result["videos"]
    )
    assert all(
        x["url"].startswith("https://www.bilibili.com/video/BV")
        for x in result["videos"]
    )
    assert "page_size=3" in transport.fetch.call_args.args[0]
    assert "%26" in transport.fetch.call_args.args[0]
    assert result["sources"][0]["kind"] == "search_metadata"


@pytest.mark.asyncio
async def test_search_ignores_hostile_fields_and_duplicates(setup_reader):
    reader, transport, _, _ = setup_reader
    transport.fetch.return_value = response(
        {
            "code": 0,
            "data": {
                "result": [
                    None,
                    {"bvid": BV, "title": []},
                    {"bvid": BV, "title": "Valid", "author": {}},
                ]
            },
        }
    )
    result = await reader.search("Public")
    assert result["ok"] and result["videos"] == []
    transport.fetch.return_value = response(
        {"code": 0, "data": {"result": [{"bvid": BV, "title": "Valid"}] * 3}}
    )
    result = await reader.search("Public")
    assert len(result["videos"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,limit",
    [
        (None, 3),
        ([], 3),
        ("", 3),
        ("x" * 201, 3),
        ("line\nline", 3),
        ("valid", True),
        ("valid", "3"),
    ],
)
async def test_invalid_search_never_connects(setup_reader, query, limit):
    reader, _, factory, _ = setup_reader
    result = await reader.search(query, limit)
    assert not result["ok"] and result["videos"] == []
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_fixed_api_endpoint_allowlist(setup_reader):
    reader, transport, _, _ = setup_reader
    for url in (
        "https://api.bilibili.com/x/web-interface/archive/like",
        "https://evil.test/x/web-interface/view",
        "http://api.bilibili.com/x/player/v2",
    ):
        with pytest.raises(BackpackError):
            await reader._json(transport, url)
    transport.fetch.assert_not_called()


class MockContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_transport_json_mode_enforces_streaming_byte_limit_and_mime():
    transport = PublicTransport(None, None, json_only=True)
    raw = SimpleNamespace(
        status=200,
        headers={"Content-Type": "application/json"},
        content_length=None,
        content=MockContent([b"x" * (1024 * 1024), b"y"]),
    )
    with pytest.raises(BackpackError, match="byte limit"):
        await transport._read_response(
            raw, "https://api.bilibili.com/x/player/v2", False, "primary"
        )
    raw.content = MockContent([b"{}"])
    raw.headers = {"Content-Type": "text/html"}
    with pytest.raises(BackpackError, match="unsupported"):
        await transport._read_response(
            raw, "https://api.bilibili.com/x/player/v2", False, "primary"
        )
    raw.headers = {"Content-Type": "application/json"}
    normal = PublicTransport(None, None)
    with pytest.raises(BackpackError, match="unsupported"):
        await normal._read_response(
            raw, "https://api.bilibili.com/x/player/v2", False, "primary"
        )
    result = await transport._read_response(
        raw, "https://api.bilibili.com/x/player/v2", False, "primary"
    )
    assert result["body"] == b"{}"


@pytest.mark.asyncio
async def test_json_transport_retains_dns_pinning_and_disables_redirects():
    transport = PublicTransport("http://127.0.0.1:7890", None, json_only=True)
    transport._resolve_address = AsyncMock(return_value="93.184.216.34")
    reply = SimpleNamespace(
        status=200,
        headers={"Content-Type": "application/json"},
        content_length=2,
        content=MockContent([b"{}"]),
    )
    context = AsyncMock()
    context.__aenter__.return_value = reply
    transport.client = SimpleNamespace(get=Mock(return_value=context))
    await transport.fetch("https://api.bilibili.com/x/player/v2?cid=123", False)
    call = transport.client.get.call_args
    assert call.args[0].host == "93.184.216.34"
    assert call.kwargs["server_hostname"] == "api.bilibili.com"
    assert call.kwargs["allow_redirects"] is False
    assert call.kwargs["headers"]["Accept"] == "application/json"
    assert "Cookie" not in call.kwargs["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["169.254.169.254"], ["93.184.216.34", "10.0.0.2"]]
)
async def test_json_transport_rejects_private_or_mixed_dns_before_api_get(addresses):
    transport = PublicTransport(None, "http://127.0.0.1:7891", json_only=True)
    dns = {"Status": 0, "Answer": [{"type": 1, "data": value} for value in addresses]}
    reply = SimpleNamespace(
        status=200,
        content=SimpleNamespace(read=AsyncMock(return_value=json.dumps(dns).encode())),
    )
    context = AsyncMock()
    context.__aenter__.return_value = reply
    transport.client = SimpleNamespace(get=Mock(return_value=context))
    with pytest.raises(BackpackError, match="public addresses"):
        await transport.fetch("https://api.bilibili.com/x/player/v2?cid=123", False)
    assert transport.client.get.call_count == 1
    assert transport.client.get.call_args.args[0] == "https://1.1.1.1/dns-query"


@pytest.mark.asyncio
async def test_search_restricted_and_malformed_result_reports_error(setup_reader):
    reader, transport, _, _ = setup_reader
    for data in (
        {"code": -412},
        {"code": 0, "data": {}},
        {"code": 0, "data": {"result": {}}},
    ):
        transport.fetch.return_value = response(data)
        result = await reader.search("Public")
        assert not result["ok"] and result["videos"] == [] and result["error"]


@pytest.mark.asyncio
async def test_api_advertised_public_subtitle_signature_is_read_and_not_exposed(
    setup_reader,
):
    reader, transport, _, _ = setup_reader
    signed = CDN + "?auth_key=1800000000-0-0-" + "a" * 32
    transport.fetch.side_effect = [
        response({"code": 0, "data": metadata()}),
        response({"code": 0, "data": listing(signed)}),
        response({"body": [{"content": "真实公开字幕"}]}),
    ]
    result = await reader.read(BV)
    assert result["evidence"] == "subtitles"
    assert transport.fetch.call_args.args[0] == signed
    assert "auth_key" not in json.dumps(result)


def test_public_signature_exception_does_not_weaken_normal_browsing():
    from astrbot_plugin_ada_companion.public_transport import browse_url

    suffix = "?auth_key=1800000000-0-0-" + "a" * 32
    signed = CDN + suffix
    assert browse_url(signed, document=False, subtitle_signature=True) == signed
    with pytest.raises(BackpackError):
        browse_url(signed, document=False)
    with pytest.raises(BackpackError):
        browse_url(signed, document=True, subtitle_signature=True)
    for url in (
        "https://example.org/bfs/ai_subtitle/prod/example.json" + suffix,
        "https://aisubtitle.hdslb.com/private.json" + suffix,
        "http://aisubtitle.hdslb.com/bfs/subtitle/example.json" + suffix,
        CDN + "?auth_key=private-secret",
        signed + "&session=private",
        signed + "&auth_key=1800000000-0-0-" + "a" * 32,
    ):
        with pytest.raises(BackpackError):
            browse_url(url, document=False, subtitle_signature=True)
