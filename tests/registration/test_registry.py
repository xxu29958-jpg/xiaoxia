"""Exercise native registries and managers; fixture adapters never use a network."""

# ruff: noqa: E402 -- isolation must be checked before importing AstrBot

import asyncio
import os
import unittest

assert os.environ.get("ASTRBOT_ROOT"), "Run through the isolated registration gate"
assert os.environ.get("ASTRBOT_TEST_MODE") == "true"
assert os.environ.get("TESTING") == "true"

from astrbot_plugin_codex_oauth_plug.oauth_plug_openai_codex import registration
from astrbot_plugin_codex_oauth_plug.oauth_plug_openai_codex.provider import (
    ProviderOAuthPlugOpenAICodex,
)
from astrbot_plugin_codex_oauth_plug.oauth_plug_openai_codex.service import (
    PROVIDER_TYPE,
)

from astrbot.core import astrbot_config, db_helper, sp
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.platform.manager import PlatformManager
from astrbot.core.platform.platform import Platform, PlatformStatus
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.register import (
    platform_cls_map,
    register_platform_adapter,
    unregister_platform_adapters_by_module,
)
from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.manager import ProviderManager
from astrbot.core.provider.provider import STTProvider
from astrbot.core.provider.register import (
    provider_cls_map,
    provider_registry,
    register_provider_adapter,
)
from astrbot.core.umop_config_router import UmopConfigRouter


class LocalRecognizer(STTProvider):
    """Deterministic fixture, not a model-quality or audio-recognition test."""

    async def initialize(self):
        self.initialized = True

    async def get_text(self, audio_url: str) -> str:
        return "fixture transcript: " + audio_url

    async def terminate(self):
        self.stopped = True


class LocalChannel(Platform):
    """Native platform lifecycle fixture; it only publishes to an asyncio queue."""

    def __init__(self, config, settings, event_queue):
        super().__init__(config, event_queue)
        self.closed = False

    async def run(self):
        await self._event_queue.put((self.config["id"], "started"))
        await asyncio.Event().wait()

    async def terminate(self):
        self.closed = True

    def meta(self):
        return PlatformMetadata(
            "fm_fixture_channel", "isolated fixture", self.config["id"]
        )


class NativeRegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_actual_oauth_registration_replacement_and_unload(self):
        self.assertNotIn(PROVIDER_TYPE, provider_cls_map)
        self.addCleanup(registration.unregister_provider_adapter)
        registration.register_provider_adapter()
        first = provider_cls_map[PROVIDER_TYPE]
        self.assertIs(first.cls_type, ProviderOAuthPlugOpenAICodex)
        self.assertEqual(first.provider_type, ProviderType.CHAT_COMPLETION)
        registration.register_provider_adapter()
        self.assertIsNot(provider_cls_map[PROVIDER_TYPE], first)
        self.assertEqual(sum(p.type == PROVIDER_TYPE for p in provider_registry), 1)
        registration.unregister_provider_adapter()
        registration.unregister_provider_adapter()
        self.assertNotIn(PROVIDER_TYPE, provider_cls_map)
        self.assertFalse(any(p.type == PROVIDER_TYPE for p in provider_registry))

    async def test_provider_manager_resolves_registered_class_and_terminates_it(self):
        name = "fm_fixture_stt"
        register = register_provider_adapter(
            name, "fixture", ProviderType.SPEECH_TO_TEXT
        )
        register(LocalRecognizer)
        metadata = provider_cls_map[name]
        self.addCleanup(provider_cls_map.pop, name)
        self.addCleanup(provider_registry.remove, metadata)
        with self.assertRaises(ValueError):
            register(LocalRecognizer)
        acm = AstrBotConfigManager(astrbot_config, UmopConfigRouter(sp), sp)
        manager = ProviderManager(acm, db_helper, PersonaManager(db_helper, acm))
        config = {"id": name, "type": name, "enable": True}
        self.addAsyncCleanup(manager.terminate_provider, name)
        await manager.load_provider({**config, "enable": False})
        self.assertNotIn(name, manager.inst_map)
        await manager.load_provider(config)
        inst = manager.inst_map[name]
        self.assertIsInstance(inst, LocalRecognizer)
        assert isinstance(inst, LocalRecognizer)
        self.assertTrue(inst.initialized)
        self.assertIs(manager.curr_stt_provider_inst, inst)
        self.assertEqual(
            await inst.get_text("local.wav"), "fixture transcript: local.wav"
        )
        self.assertEqual(inst.meta().type, name)
        await manager.terminate_provider(name)
        self.assertTrue(inst.stopped)
        self.assertNotIn(name, manager.inst_map)
        self.assertIsNone(manager.curr_stt_provider_inst)
        provider_cls_map.pop(name)
        self.addCleanup(provider_cls_map.__setitem__, name, metadata)
        await manager.load_provider(config)
        self.assertNotIn(name, manager.inst_map)

    async def test_platform_manager_runs_cancels_and_unregisters_native_adapter(self):
        name = "fm_fixture_channel"
        register = register_platform_adapter(name, "fixture")
        register(LocalChannel)
        self.addCleanup(unregister_platform_adapters_by_module, LocalChannel.__module__)
        with self.assertRaises(ValueError):
            register(LocalChannel)
        queue = asyncio.Queue()
        manager = PlatformManager(astrbot_config, queue)
        self.addAsyncCleanup(manager.terminate)
        config = {"id": name, "type": name, "enable": True}
        await manager.load_platform({**config, "enable": False})
        self.assertEqual(manager.platform_insts, [])
        await manager.load_platform(config)
        inst = manager.platform_insts[0]
        self.assertIsInstance(inst, LocalChannel)
        assert isinstance(inst, LocalChannel)
        self.assertEqual(await asyncio.wait_for(queue.get(), 2), (name, "started"))
        self.assertEqual(inst.status, PlatformStatus.RUNNING)
        tasks = manager._platform_tasks[inst.client_self_id]
        await manager.terminate_platform(name)
        self.assertTrue(inst.closed)
        self.assertTrue(tasks.run.done())
        self.assertTrue(tasks.wrapper.done())
        self.assertEqual(inst.status, PlatformStatus.STOPPED)
        self.assertEqual(manager.platform_insts, [])
        self.assertEqual(
            unregister_platform_adapters_by_module(LocalChannel.__module__), [name]
        )
        self.assertNotIn(name, platform_cls_map)
        await manager.load_platform(config)
        self.assertEqual(manager.platform_insts, [])
