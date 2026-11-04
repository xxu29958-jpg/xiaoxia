"""Native plugin loading must not depend on aliases installed by test runners."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace


async def native_probe(folder):
    """Use the production loader namespace and a private synthetic database."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    prefix = "data.plugins.astrbot_plugin_memory_companion.core"
    store = import_module(prefix + ".store").MemoryStore(folder / "memory.db")
    store.initialize()
    origin = "qq:FriendMessage:fixture"
    owners = SimpleNamespace(
        healthy=True,
        owners=[origin],
        shared_scope="fixture-owner",
        context_variants=lambda _: [],
    )
    bridge = SimpleNamespace(_plugin=SimpleNamespace(store=store))
    services = SimpleNamespace(memory=lambda: bridge)
    companion = "data.plugins.astrbot_plugin_ada_companion"
    dialogue = import_module(companion + ".dialogue").DialogueState(
        folder / "dialogue.json", owners, services
    )
    appraisals = import_module(companion + ".appraisals").EventUnderstanding(dialogue)
    try:
        for consumer in (dialogue, appraisals):
            result = await consumer.snapshot(origin)
            assert result["ok"], result
            state, _, _, _ = await consumer._access(origin, None)
            assert state.store is store
        assert "astrbot_plugin_memory_companion" not in sys.modules
        print(json.dumps({"native_namespace": True, "same_store": True}))
    finally:
        store.close()


class RuntimeNamespaceTests(unittest.TestCase):
    def test_native_consumers_share_the_loaded_store_without_test_aliases(self):
        with tempfile.TemporaryDirectory(prefix="xiaoxia-native-namespace-") as root:
            result = subprocess.run(
                [sys.executable, "-I", "-B", __file__, "--probe", root],
                env={
                    **os.environ,
                    "ASTRBOT_ROOT": root,
                    "ASTRBOT_TEST_MODE": "true",
                    "TESTING": "true",
                },
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"same_store": true', result.stdout)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        asyncio.run(native_probe(Path(sys.argv[2])))
    else:
        unittest.main()
