from __future__ import annotations

from typing import Any

from .models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceCompanionNotes:
    """Companion notes; state and transaction ownership remain in the facade."""

    async def tool_note_create(
        self, event: Any, title: str, content: str = ""
    ) -> dict[str, Any]:
        ctx = await self.identity.resolve_event_context(event)
        title = clean_text(title, 120)
        content = clean_text(content or title, 3000)
        if not title and not content:
            return {"ok": False, "error": "empty note"}
        record = MemoryRecord(
            id=self.stable_id("companion_note", ctx.session_id, title, content),
            memory_type="companion_note",
            subject=self._bot_entity(ctx),
            object=EntityRef(
                kind="session", id=ctx.session_id, role="companion_context"
            ),
            scope="unknown",
            session_id=ctx.session_id,
            platform=ctx.platform,
            visibility="bot_self",
            sayability="indirect",
            reality_level="persona_life",
            lifecycle="stable_memory",
            content=content,
            evidence=ctx.message_text,
            confidence=0.82,
            importance=0.6,
            review_status="auto",
            tags=["companion_note", "bot_self", title]
            if title
            else ["companion_note", "bot_self"],
            metadata={
                "title": title,
                "tool": "memory_companion_note_create",
                "owner_bot_id": self._bot_subject_id(ctx),
            },
            source_plugin="memory_companion_tool",
        )
        self.importance.calibrate(record, source="companion_note")
        memory_id = await self.store.insert_memory(record)
        self._schedule_memory_embedding(memory_id, record)
        return {"ok": True, "memory_id": memory_id, "title": title}

    @staticmethod
    def _companion_note_title(record: MemoryRecord) -> str:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        return clean_text(metadata.get("title"), 120)

    def _owned_companion_note(
        self, record: MemoryRecord | None, ctx: SessionContext
    ) -> bool:
        if (
            record is None
            or record.memory_type != "companion_note"
            or record.visibility != "bot_self"
        ):
            return False
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        owner_bot_id = clean_text(metadata.get("owner_bot_id"), 120)
        current_bot_id = clean_text(self._bot_subject_id(ctx), 120) or "self"
        subject_id = clean_text(record.subject.id, 120)
        return owner_bot_id == current_bot_id and subject_id in {
            "",
            "self",
            current_bot_id,
        }

    async def _owned_companion_notes(
        self,
        ctx: SessionContext,
        *,
        query: str = "",
        limit: int = 20,
    ) -> list[MemoryRecord]:
        records = await self.store.list_memories(
            limit=5000,
            include_pending=False,
            memory_type="companion_note",
            visibility="bot_self",
        )
        owned = [
            record for record in records if self._owned_companion_note(record, ctx)
        ]
        query = clean_text(query, 500).casefold()
        if query:
            owned = [
                record
                for record in owned
                if query
                in " ".join(
                    (
                        record.id,
                        self._companion_note_title(record),
                        record.content,
                    )
                ).casefold()
            ]
        return owned[: max(1, min(5000, int(limit or 1)))]

    def _serialize_companion_note(
        self, record: MemoryRecord, ctx: SessionContext
    ) -> dict[str, Any]:
        return {
            "id": record.id,
            "title": self._companion_note_title(record),
            "content": record.content,
            "created_at": record.created_at,
            "session_id": record.session_id or ctx.session_id,
        }

    async def tool_note_read(
        self, event: Any, query: str = "", limit: int = 5
    ) -> dict[str, Any]:
        ctx = await self.identity.resolve_event_context(event)
        records = await self._owned_companion_notes(
            ctx,
            query=query,
            limit=max(1, min(20, int(limit or 5))),
        )
        return {
            "ok": True,
            "notes": [
                self._serialize_companion_note(record, ctx) for record in records
            ],
        }

    async def tool_note_delete(
        self,
        event: Any,
        memory_id: str = "",
        *,
        title: str = "",
    ) -> dict[str, Any]:
        ctx = await self.identity.resolve_event_context(event)
        memory_id = clean_text(memory_id, 120)
        title = clean_text(title, 120)
        if not memory_id and not title:
            return {"ok": False, "error": "memory_id or title required"}

        record: MemoryRecord | None = None
        if memory_id:
            candidate = await self.store.get_memory(memory_id)
            if not self._owned_companion_note(candidate, ctx):
                return {"ok": False, "error": "note not found"}
            record = candidate
        else:
            matches = await self._owned_companion_notes(ctx, query=title, limit=5000)
            exact = [
                item
                for item in matches
                if self._companion_note_title(item).casefold() == title.casefold()
            ]
            if len(exact) == 1:
                record = exact[0]
            else:
                candidates = exact or matches
                if not candidates:
                    return {"ok": False, "error": "note not found"}
                return {
                    "ok": False,
                    "error": "ambiguous note title"
                    if exact
                    else "title match requires confirmation",
                    "action": "read notes and retry with memory_id",
                    "matches": [
                        {
                            "memory_id": item.id,
                            "title": self._companion_note_title(item),
                            "content_preview": clean_text(item.content, 180),
                        }
                        for item in candidates[:10]
                    ],
                }

        deleted = await self.store.delete_memory(record.id)
        if not deleted:
            return {"ok": False, "error": "note not found"}
        return {
            "ok": True,
            "deleted": True,
            "memory_id": record.id,
            "title": self._companion_note_title(record),
        }
