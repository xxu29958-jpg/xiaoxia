import asyncio
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

EXTRA_TURN_ID = "continuous_message_turn_id"
EXTRA_TURN_ITEMS = "continuous_message_turn_items"
EXTRA_TURN_HISTORY = "continuous_message_history_snapshot"
EXTRA_SENT_IDS = "continuous_message_sent_message_ids"
EXTRA_CAPTION_HANDLED = "continuous_message_caption_handled"


@dataclass
class TurnItem:
    index: int
    message_id: Any
    item_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    text: str = ""
    images: list[str] = field(default_factory=list)
    image_captions: dict[str, str] = field(default_factory=dict)
    kind: str = "text"
    timestamp: float = field(default_factory=time.time)
    seeded: bool = False
    parts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "message_id": self.message_id,
            "item_id": self.item_id,
            "text": self.text,
            "images": list(self.images),
            "parts": deepcopy(self.parts),
            "image_captions": dict(self.image_captions),
            "kind": self.kind,
            "timestamp": self.timestamp,
            "seeded": self.seeded,
        }

    def clone_seed(self, index: int) -> "TurnItem":
        return TurnItem(
            index=index,
            message_id=self.message_id,
            text=self.text,
            images=list(self.images),
            parts=deepcopy(self.parts),
            image_captions=dict(self.image_captions),
            kind=self.kind,
            timestamp=self.timestamp,
            seeded=True,
        )


@dataclass
class TurnSession:
    uid: str
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    items: list[TurnItem] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_message_at: float = field(default_factory=time.time)
    flush_event: asyncio.Event = field(default_factory=asyncio.Event)
    timer_task: asyncio.Task | None = None
    is_typing: bool = False
    invalidated: bool = False
    sent_message_ids: list[Any] = field(default_factory=list)
    history_snapshot: list[dict] | None = None
    history_conversation_id: str | None = None
    history_generation: int = 0
    history_snapshot_inherited: bool = False
    history_reconciled: bool = False
    collection_started_at: float = field(default_factory=time.monotonic)
    predecessor_turn_ids: list[str] = field(default_factory=list)
    cancelled_reason: str | None = None
    delivery_state: str = "collecting"
    send_depth: int = 0
    send_succeeded: bool = False
    cleanup_task: asyncio.Task | None = None

    def add_item(
        self,
        message_id: Any,
        text: str,
        images: list[str],
        parts: list[dict[str, Any]] | None = None,
    ) -> TurnItem:
        kind = "mixed" if text and images else "image" if images else "text"
        if parts is None:
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            parts.extend({"type": "image", "url": url} for url in (images or []))
        item = TurnItem(
            index=len(self.items) + 1,
            message_id=message_id,
            text=text or "",
            images=list(images or []),
            parts=deepcopy(parts),
            kind=kind,
        )
        self.items.append(item)
        self.last_message_at = time.time()
        return item

    def seed_from(self, items: list[TurnItem]) -> None:
        self.items = []
        for item in items:
            if item.text or item.images:
                self.items.append(item.clone_seed(len(self.items) + 1))
        self.last_message_at = time.time()

    def remove_message(self, message_id: Any) -> bool:
        before = len(self.items)
        self.items = [
            item for item in self.items if str(item.message_id) != str(message_id)
        ]
        for idx, item in enumerate(self.items, start=1):
            item.index = idx
        return len(self.items) < before

    def all_images(self) -> list[str]:
        return [url for item in self.items for url in item.images]

    def plain_texts(self) -> list[str]:
        return [item.text for item in self.items if item.text]

    def items_as_dicts(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.items]

    def has_content(self) -> bool:
        return any(item.text or item.images for item in self.items)

    def seedable_items(self) -> list[TurnItem]:
        return [item for item in self.items if item.text or item.images]


class TurnStateStore:
    def __init__(self) -> None:
        self.sessions: dict[str, TurnSession] = {}
        self.active_turns: dict[str, TurnSession] = {}
        self.pending_turns: dict[str, TurnSession] = {}
        self.invalid_turns: set[str] = set()

    def create_session(self, uid: str) -> TurnSession:
        session = TurnSession(uid=uid)
        self.sessions[uid] = session
        return session

    def get_session(self, uid: str) -> TurnSession | None:
        return self.sessions.get(uid)

    def pop_session(
        self, uid: str, expected: TurnSession | None = None
    ) -> TurnSession | None:
        session = self.sessions.get(uid)
        if session is None or (expected is not None and session is not expected):
            return None
        return self.sessions.pop(uid)

    def activate(self, session: TurnSession) -> None:
        if self.sessions.get(session.uid) is session:
            self.sessions.pop(session.uid, None)
        self.pending_turns.pop(session.turn_id, None)
        self.active_turns[session.turn_id] = session

    def mark_pending(self, session: TurnSession) -> None:
        if self.sessions.get(session.uid) is session:
            self.sessions.pop(session.uid, None)
        self.pending_turns[session.turn_id] = session

    def finish(self, turn_id: str) -> None:
        self.active_turns.pop(turn_id, None)
        self.pending_turns.pop(turn_id, None)
        self.invalid_turns.discard(turn_id)

    def invalidate(self, turn_id: str) -> None:
        self.invalid_turns.add(turn_id)
        session = self.active_turns.get(turn_id) or self.pending_turns.get(turn_id)
        if session:
            session.invalidated = True

    def invalidate_uid(self, uid: str) -> list[str]:
        invalidated: list[str] = []
        all_turns = list(self.active_turns.items()) + list(self.pending_turns.items())
        for turn_id, session in all_turns:
            if session.uid == uid and turn_id not in self.invalid_turns:
                self.invalidate(turn_id)
                invalidated.append(turn_id)
        return invalidated

    def sessions_for_uid(self, uid: str) -> list[TurnSession]:
        return [
            session
            for session in (
                list(self.active_turns.values()) + list(self.pending_turns.values())
            )
            if session.uid == uid
        ]

    def is_valid(self, turn_id: str | None) -> bool:
        return bool(turn_id) and turn_id not in self.invalid_turns

    def get_turn(self, turn_id: str | None) -> TurnSession | None:
        if not turn_id:
            return None
        return self.active_turns.get(turn_id) or self.pending_turns.get(turn_id)
