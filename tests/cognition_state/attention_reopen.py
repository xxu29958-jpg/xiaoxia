"""Fresh-process read of the same isolated native store at a supplied clock."""

# ruff: noqa: E402 -- refuse unisolated imports before loading AstrBot

import asyncio
import json
import os
import sys
from pathlib import Path

assert os.environ.get("ASTRBOT_ROOT"), "Only isolated roots are allowed"

from astrbot_plugin_memory_companion.core.appraisal_state import AppraisalState
from astrbot_plugin_memory_companion.core.store import MemoryStore


async def main():
    store = MemoryStore(Path(sys.argv[1]))
    store.initialize()
    try:
        result = await AppraisalState(store).snapshot(
            sys.argv[2],
            authorize=lambda: True,
            mode="attention",
            query="山歌 值夜班",
            now=float(sys.argv[3]),
        )
        print("RESULT:" + json.dumps(result, ensure_ascii=False))
    finally:
        store.close()


asyncio.run(main())
