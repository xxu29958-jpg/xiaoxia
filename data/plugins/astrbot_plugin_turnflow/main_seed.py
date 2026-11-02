import json
from typing import Any

from .turn_state import (
    TurnSession,
)


class ContinuousMessagePluginSeed:
    """Seed; shared runtime resources belong to the facade."""

    def _collect_seed_items(self, uid: str) -> list:
        seed_items = []
        seen = set()
        for session in self.turns.sessions_for_uid(uid):
            if not self._session_is_interruptible(session):
                continue
            for item in session.seedable_items():
                key = (str(item.message_id), item.text, tuple(item.images))
                if key in seen:
                    continue
                seen.add(key)
                seed_items.append(item)
        return seed_items

    @staticmethod
    def _clone_history_snapshot(snapshot: list[dict] | None) -> list[dict] | None:
        if snapshot is None:
            return None
        return json.loads(json.dumps(snapshot, ensure_ascii=False))

    def _inherit_history_baseline(
        self,
        target: TurnSession,
        sources: list[TurnSession],
    ) -> bool:
        candidates = [
            session
            for session in sources
            if session.history_snapshot is not None and session.history_conversation_id
        ]
        if not candidates:
            return False
        source = min(candidates, key=lambda session: session.created_at)
        target.history_snapshot = self._clone_history_snapshot(source.history_snapshot)
        target.history_conversation_id = source.history_conversation_id
        target.history_generation = source.history_generation
        target.history_snapshot_inherited = True
        target.predecessor_turn_ids = [session.turn_id for session in sources]
        return True

    def _collect_seed_items_excluding_message(
        self, uid: str, message_id: Any
    ) -> tuple[list, bool]:
        seed_items = []
        seen = set()
        found = False
        for session in self.turns.sessions_for_uid(uid):
            if not self._session_is_interruptible(session):
                continue
            for item in session.seedable_items():
                if str(item.message_id) == str(message_id):
                    found = True
                    continue
                key = (str(item.message_id), item.text, tuple(item.images))
                if key in seen:
                    continue
                seen.add(key)
                seed_items.append(item)
        return seed_items, found
