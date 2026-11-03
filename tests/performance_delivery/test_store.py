"""Delivery fencing and recovery must preserve unknown and already accepted facts."""

import tempfile
import unittest
from pathlib import Path

from data.plugins.astrbot_plugin_ada_companion.performance_store import (
    PerformanceError,
    PerformanceStore,
)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.now = 100.0
        self.path = Path(self.temporary.name) / "requests.sqlite3"
        self.store = PerformanceStore(self.path, clock=lambda: self.now)
        self.request = self.store.submit(
            "owner", {"origin": "target"}, "message", "title", {}, {}
        )

    def preparing(self):
        claim = self.store.claim()
        assert claim is not None
        self.store.observe_generation(
            claim["id"], claim["token"], {"id": "job", "state": "completed"}
        )
        self.store.preparing(claim["id"], claim["token"])
        return claim

    def test_cancel_before_send_fences_side_effect(self):
        claim = self.preparing()
        self.store.cancel("owner", claim["id"])
        with self.assertRaisesRegex(PerformanceError, "performance_cancelled"):
            self.store.begin_send(claim["id"], claim["token"], {"format": "mp3"})

    def test_send_crash_is_unknown_and_late_actual_receipt_can_reconcile(self):
        claim = self.preparing()
        self.store.begin_send(
            claim["id"], claim["token"], {"format": "mp3", "sha256": "audio"}
        )
        self.now += 61
        resumed = PerformanceStore(self.path, clock=lambda: self.now)
        self.assertIsNone(resumed.claim())
        saved = resumed.get("owner", claim["id"])
        self.assertEqual(saved["state"], "unknown")
        self.assertEqual(saved["artifact"]["sha256"], "audio")
        resumed.cancel("owner", claim["id"])
        resumed.receipt(
            claim["id"],
            claim["token"],
            {"kind": "server_accepted", "message_id": "123"},
        )
        saved = resumed.get("owner", claim["id"])
        self.assertEqual(saved["state"], "accepted")
        self.assertTrue(saved["cancel_requested"])
        self.assertIsNone(resumed.claim())

    def test_preparation_recovery_rejects_old_sender(self):
        old = self.preparing()
        self.now += 61
        new = self.store.claim()
        assert new is not None
        self.assertNotEqual(old["token"], new["token"])
        with self.assertRaisesRegex(PerformanceError, "stale_performance_claim"):
            self.store.begin_send(old["id"], old["token"], {"format": "mp3"})

    def test_same_event_is_one_request_and_other_owner_cannot_query_or_cancel(self):
        repeated = self.store.submit(
            "owner", {"origin": "target"}, "message", "different display title", {}, {}
        )
        self.assertEqual(repeated["id"], self.request["id"])
        for action in (self.store.get, self.store.cancel):
            with self.assertRaisesRegex(PerformanceError, "performance_not_found"):
                action("foreign", self.request["id"])


if __name__ == "__main__":
    unittest.main()
