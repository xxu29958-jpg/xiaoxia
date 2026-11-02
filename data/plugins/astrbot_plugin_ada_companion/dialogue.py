"""Evidence-backed conversational threads, separate from biographical facts."""

import json
import math
import os
import time
import uuid
from pathlib import Path


class DialogueState:
    """Persist a small, per-conversation set of explicitly grounded callbacks."""

    def __init__(self, path: Path, linked_sessions=()):
        self.linked_sessions = tuple(dict.fromkeys(linked_sessions))
        self.path = path
        self.healthy = True
        self.data = {"version": 1, "sessions": {}}
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(value, dict)
                    or value.get("version") != 1
                    or not isinstance(value.get("sessions"), dict)
                ):
                    raise ValueError("Unsupported dialogue state")
                for origin, entries in value["sessions"].items():
                    if not isinstance(origin, str) or not isinstance(entries, list):
                        raise ValueError("Invalid dialogue session")
                    for item in entries:
                        if not isinstance(item, dict) or any(
                            not isinstance(item.get(key), str)
                            for key in ("id", "title", "content", "evidence_quote")
                        ):
                            raise ValueError("Invalid dialogue entry")
                        if item.get("kind") not in {"topic", "shared_joke"} or item.get(
                            "status"
                        ) not in {"open", "resolved"}:
                            raise ValueError("Invalid dialogue kind or status")
                        for key in ("created_at", "updated_at", "expires_at"):
                            timestamp = item.get(key)
                            if not isinstance(
                                timestamp, (int, float)
                            ) or not math.isfinite(timestamp):
                                raise ValueError("Invalid dialogue timestamp")
                self.data = value
            except (ValueError, OSError):
                self.healthy = False

    def visible(self, origin: str, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        if not self.healthy:
            return []
        return [
            dict(item)
            for source in (
                self.linked_sessions if origin in self.linked_sessions else (origin,)
            )
            for item in self.data["sessions"].get(source, [])
            if item["status"] == "open" and item["expires_at"] > now
        ][-12:]

    def update(
        self,
        origin: str,
        user_message: str,
        *,
        action: str,
        kind: str = "topic",
        title: str = "",
        content: str = "",
        evidence_quote: str = "",
        item_id: str = "",
        now: float | None = None,
    ) -> dict:
        """Apply one scoped update whose evidence occurs in the current user message.

        Args:
            origin: Complete private-chat identity, including platform.
            user_message: Current human message; never an assistant completion.
            action: list, save, resolve or forget.
            kind: topic or shared_joke for a save.
            title: Short stable label for the conversational thread.
            content: Concise thread or joke description.
            evidence_quote: Exact supporting text from the current user message.
            item_id: Existing scoped ID when changing or closing a thread.
            now: Optional timestamp used by behavioral checks.

        Returns:
            Operation result with visible scoped entries or a fixed error code.
        """
        if not self.healthy:
            return {"ok": False, "error": "state_unreadable"}
        now = time.time() if now is None else now
        if action == "list":
            return {"ok": True, "items": self.visible(origin, now)}
        if action not in {"save", "resolve", "forget"}:
            return {"ok": False, "error": "invalid_action"}
        quote = evidence_quote.strip()
        if (
            len(quote) < (3 if action == "save" else 1)
            or len(quote) > 400
            or quote not in user_message
        ):
            return {"ok": False, "error": "current_user_evidence_required"}
        if origin in self.linked_sessions:
            origin = next(
                (
                    source
                    for source in self.linked_sessions
                    if any(
                        x["id"] == item_id
                        for x in self.data["sessions"].get(source, [])
                    )
                ),
                self.linked_sessions[0],
            )
        items = [dict(x) for x in self.data["sessions"].get(origin, [])]
        existing = next((x for x in items if x["id"] == item_id), None)
        if item_id and existing is None:
            return {"ok": False, "error": "scoped_item_not_found"}
        if action in {"resolve", "forget"} and existing is None:
            return {"ok": False, "error": "item_id_required"}
        if action == "save":
            try:
                existing = self._save_thread(
                    items, existing, kind, title, content, quote, now
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        elif action == "resolve":
            existing.update(status="resolved", resolution_quote=quote, updated_at=now)
        else:
            items.remove(existing)
        retained = [x for x in items if x["expires_at"] > now - 7 * 86400]
        updated = {
            "version": 1,
            "sessions": {**self.data["sessions"], origin: retained},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(self.path)
        self.data = updated
        return {"ok": True, "id": existing["id"], "action": action}

    @staticmethod
    def _save_thread(items, existing, kind, title, content, quote, now):
        """Validate a thread replacement before mutating the transaction copy."""
        if (
            kind not in {"topic", "shared_joke"}
            or not title.strip()
            or not content.strip()
        ):
            raise ValueError("invalid_thread")
        if len(title) > 80 or len(content) > 400:
            raise ValueError("thread_too_long")
        if existing is None:
            existing = next(
                (x for x in items if x["title"] == title.strip() and x["kind"] == kind),
                None,
            )
        if existing is None:
            if (
                sum(x["status"] == "open" and x["expires_at"] > now for x in items)
                >= 12
            ):
                raise ValueError("thread_limit")
            existing = {"id": uuid.uuid4().hex[:12], "created_at": now}
            items.append(existing)
        existing.update(
            kind=kind,
            title=title.strip(),
            content=content.strip(),
            evidence_quote=quote,
            status="open",
            updated_at=now,
            expires_at=now + (30 if kind == "shared_joke" else 7) * 86400,
        )
        return existing
