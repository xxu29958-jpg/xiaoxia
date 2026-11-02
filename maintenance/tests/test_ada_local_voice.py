"""Check voice input survives recognition and reaches spoken reply routing."""

import asyncio
import io
import shutil
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from imageio_ffmpeg import get_ffmpeg_exe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data/plugins"))
from astrbot_plugin_ada_companion.cloud_voice import VoiceError
from astrbot_plugin_ada_companion.lifecycle import Lifecycle
from astrbot_plugin_ada_companion.main import AdaCompanion
from astrbot_plugin_ada_companion.prompting import ContextBuilder
from astrbot_plugin_ada_companion.response_media import ResponseMedia

from astrbot.core.message.components import File, Plain, Record, Reply
from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage
from maintenance.tests.test_browser_url_messages import event as native_event

FFMPEG = shutil.which("ffmpeg") or get_ffmpeg_exe()


def builder(origin):
    prompt = object.__new__(ContextBuilder)
    prompt.config = {"owner_sessions": [origin]}
    prompt.policy = SimpleNamespace(data={"voice_sessions": {}})
    return prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("quoted", [False, True])
async def test_recognized_voice_routes_reply_but_quoted_voice_does_not(
    tmp_path, quoted
):
    audio = tmp_path / "incoming.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 1600)
    record = Record(file=str(audio), url=str(audio))
    current = native_event("weixin_oc", "")
    current.message_obj.message = (
        [Reply(id="prior", chain=[record])] if quoted else [record]
    )
    stt = SimpleNamespace(get_text=AsyncMock(return_value="今天有点累"))
    stage = PreProcessStage()
    stage.config = {}
    stage.platform_settings = {}
    stage.stt_settings = {"enable": True}
    stage.plugin_manager = SimpleNamespace(
        context=SimpleNamespace(
            get_using_stt_provider_async=AsyncMock(return_value=stt)
        )
    )
    await stage.process(current)
    assert "今天有点累" in current.get_message_str()
    assert bool(current.get_extra("voice_input")) is (not quoted)
    assert current.get_extra("voice_input_files", []) == (
        [] if quoted else [str(audio)]
    )
    prompt = builder(current.unified_msg_origin)
    assert prompt._response_intent(current) is (not quoted)
    if not quoted:
        assert isinstance(current.get_messages()[0], Plain)
        current.message_str = "今天有点累，只用文字回我"
        assert prompt._response_intent(current) is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("给我哼两句吧", False),
        ("唱首短一点的小曲", False),
        ("别唱歌了，用语音说一句晚安", True),
        ("今天听了一首歌", False),
    ],
)
def test_only_supported_voice_requests_trigger_audio(text, expected):
    current = native_event("weixin_oc", text)
    prompt = builder(current.unified_msg_origin)
    assert prompt._response_intent(current) is expected


@pytest.mark.asyncio
async def test_tool_intro_does_not_consume_final_voice_reply(tmp_path):
    voice = SimpleNamespace(synthesize=AsyncMock(return_value=b"final audio"))
    media = ResponseMedia({"voice_enabled": True}, tmp_path, voice)
    media.audio_files.create = AsyncMock(return_value=Record(file="final.wav"))
    current = native_event("aiocqhttp", "唱完以后告诉我")
    current.set_extra("ada_native_reply", True)
    current.set_extra("ada_voice_requested", True)
    current.set_result(current.plain_result("我先准备一下。"))
    await media.decorate(current)
    voice.synthesize.assert_not_awaited()
    assert not current.get_extra("ada_voice_done")
    current.set_extra("ada_reply_reaction", {"action": "skip"})
    current.set_result(current.plain_result("已经好了。"))
    await media.decorate(current)
    voice.synthesize.assert_awaited_once_with("已经好了。")


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["aiocqhttp", "weixin_oc"])
async def test_singing_uses_platform_audio_and_does_not_add_spoken_duplicate(
    tmp_path, platform
):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 32000)
    voice = SimpleNamespace(synthesize=AsyncMock(return_value=buffer.getvalue()))
    plugin = object.__new__(AdaCompanion)
    plugin.config = {"voice_enabled": True, "ffmpeg_path": FFMPEG}
    plugin.lifecycle = Lifecycle()
    plugin.owner_memory = SimpleNamespace(scope=lambda event: "owner")
    plugin.local_voice = voice
    plugin.media = ResponseMedia(plugin.config, tmp_path, voice)
    current = native_event(platform, "给我唱两句原创的小曲")
    current.set_extra("ada_voice_requested", True)
    results = [
        result
        async for result in plugin.sing(current, "晚风轻轻吹过窗，你把今天放一旁。")
    ]
    assert len(results) == 1 and len(results[0].chain) == 1
    component = results[0].chain[0]
    assert isinstance(component, File if platform == "weixin_oc" else Record)
    assert Path(component.file).is_file()
    voice.synthesize.assert_awaited_once_with(
        "晚风轻轻吹过窗，你把今天放一旁。", mode="singing"
    )
    current.set_result(results[0])
    await plugin.media.decorate(current)
    assert len(current.get_result().chain) == 1
    assert plugin.media.last_voice["mode"] == "singing"


@pytest.mark.asyncio
async def test_failed_singing_does_not_claim_or_send_audio():
    voice = SimpleNamespace(
        synthesize=AsyncMock(side_effect=VoiceError("local_voice_unavailable"))
    )
    plugin = object.__new__(AdaCompanion)
    plugin.config = {"voice_enabled": True, "ffmpeg_path": FFMPEG}
    plugin.lifecycle = Lifecycle()
    plugin.owner_memory = SimpleNamespace(scope=lambda event: "owner")
    plugin.local_voice = voice
    plugin.media = SimpleNamespace(voice_lock=asyncio.Lock())
    current = native_event("aiocqhttp", "唱两句")
    results = [
        result
        async for result in plugin.sing(current, "晚风轻轻吹过窗，你把今天放一旁。")
    ]
    assert len(results) == 1
    assert all(isinstance(item, Plain) for item in results[0].chain)
    assert current.get_extra("ada_voice_done") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["aiocqhttp", "weixin_oc"])
async def test_complete_cover_uses_attachment_and_preserves_full_audio(
    tmp_path, platform
):
    source = tmp_path / "opera.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 16000 * 61)
    voice = SimpleNamespace(cover=AsyncMock(return_value=source.read_bytes()))
    plugin = object.__new__(AdaCompanion)
    plugin.config = {"voice_enabled": True, "ffmpeg_path": FFMPEG}
    plugin.lifecycle = Lifecycle()
    plugin.owner_memory = SimpleNamespace(scope=lambda event: "owner")
    plugin.local_voice = voice
    plugin.media = ResponseMedia(plugin.config, tmp_path, voice)
    current = native_event(platform, "把这段戏曲完整唱出来")
    current.message_obj.message.append(File(name="opera.wav", file=str(source)))
    results = [
        result
        async for result in plugin.cover_song(current, title="戏曲", acapella=True)
    ]
    voice.cover.assert_awaited_once_with(
        source,
        acapella=True,
        pitch=0,
        start_seconds=0.0,
        duration_seconds=None,
        source_url="",
    )
    assert len(results) == 1 and len(results[0].chain) == 1
    component = results[0].chain[0]
    assert isinstance(component, File) and component.name == "小夏 - 戏曲.mp3"
    process = await asyncio.create_subprocess_exec(
        FFMPEG,
        "-v",
        "error",
        "-i",
        component.file,
        "-f",
        "s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    decoded, _ = await process.communicate()
    assert process.returncode == 0
    assert abs(len(decoded) / 32000 - 61) < 0.02
