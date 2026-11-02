from __future__ import annotations

import inspect
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from core import bot_personal_contract
from core.bridge import MemoryCompanionBridge


ROOT = Path(__file__).resolve().parents[1]


class BridgeNegativeProbeTests(unittest.TestCase):
    def test_contract_failure_is_negative_cached_and_not_reprobed(self):
        bridge = MemoryCompanionBridge(object())
        with patch.object(
            bot_personal_contract,
            "contract_self_check",
            side_effect=[RuntimeError("private prompt must not escape"), []],
        ) as checker:
            first = bridge.probe_capability_snapshot()
            second = bridge.probe_capability_snapshot()

        self.assertEqual("negative", first["capability_state"])
        self.assertEqual("negative", first["state"])
        self.assertFalse(first["available"])
        self.assertEqual("contract_self_check_exception", first["error_code"])
        self.assertEqual(1, checker.call_count)
        self.assertEqual("negative", second["capability_state"])

    def test_negative_probe_expires_and_allows_recovery(self):
        bridge = MemoryCompanionBridge(object())
        with patch.object(bot_personal_contract, "contract_self_check", side_effect=RuntimeError("broken")):
            first = bridge.probe_capability_snapshot()
        self.assertEqual("negative", first["state"])

        bridge._capability_cache._negative_at = time.monotonic() - 61.0
        with patch.object(bot_personal_contract, "contract_self_check", return_value=[]):
            recovered = bridge.probe_capability_snapshot()
        self.assertEqual("available", recovered["state"])
        self.assertTrue(recovered["available"])




if __name__ == "__main__":
    unittest.main()
