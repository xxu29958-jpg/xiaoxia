"""Own shared core-preference reads and serialized edits through native storage."""

import asyncio
import time
import uuid


class SharedCoreMemory:
    def __init__(self, links):
        self.links = links
        self.core_lock = asyncio.Lock()

    async def execute(
        self,
        origin,
        service,
        canonical,
        *,
        action="list",
        label="",
        content="",
        kind="preference",
        expected=None,
        event=None,
    ):
        """Read or atomically edit all copies through verified native contexts."""
        if origin not in self.links.owners or canonical not in self.links.owners:
            return {"ok": False, "error": "unlinked_session"}
        if service is None:
            return {"ok": False, "error": "memory_unavailable"}
        if action not in {"list", "history", "set", "delete"}:
            return {"ok": False, "error": "invalid_action"}
        if not service.config.bool("core_memory.enabled", True):
            return {"ok": False, "error": "core_memory_disabled"}
        if event is not None and not service.config.bool(
            "core_memory.llm_management_enabled", True
        ):
            return {"ok": False, "error": "llm_management_disabled"}
        async with self.core_lock:
            records, errors, contexts = await self._read(service, canonical)
            if action == "list":
                return {
                    "ok": not errors,
                    "blocks": [self._block(r, c) for r, c in records.values()],
                    "errors": errors,
                }
            if errors or "canonical" not in contexts:
                return {
                    "ok": False,
                    "error": "shared_memory_read_failed",
                    "errors": errors,
                }
            label = " ".join(label.split())[:80]
            matches = [
                (r, c)
                for r, c in records.values()
                if str(r.metadata.get("core_label", "")).casefold() == label.casefold()
            ]
            try:
                if action == "history":
                    return await self._history(
                        service.store, matches, contexts["scopes"]
                    )
                return await self._change(
                    service,
                    origin,
                    canonical,
                    matches,
                    contexts,
                    action,
                    label,
                    content,
                    kind,
                    expected,
                    event,
                )
            except Exception as exc:
                return {
                    "ok": False,
                    "error": str(exc)
                    if isinstance(exc, ValueError)
                    else type(exc).__name__,
                }

    @staticmethod
    def _block(record, native):
        return {
            "id": record.id,
            "label": record.metadata.get("core_label", ""),
            "content": record.content,
            "kind": record.metadata.get("core_kind", "preference"),
            "revision": record.metadata.get("core_revision", 0),
            "source": native.session_id.split(":", 1)[0],
            "provenance": record.metadata.get("core_source", {"kind": "legacy"}),
        }

    async def _change(
        self,
        service,
        origin,
        canonical,
        matches,
        contexts,
        action,
        label,
        content,
        kind,
        expected,
        event,
    ):
        if not label or (action == "set" and not content.strip()):
            return {"ok": False, "error": "label_and_content_required"}
        kind = " ".join(kind.split())[:32].lower() or "fact"
        if expected is not None and (
            not matches or any(r.content != expected for r, _ in matches)
        ):
            return {"ok": False, "error": "这条记忆已变化，请重新读取后再修改。"}
        source = (
            service.store.core_event_source(event)
            if event is not None
            else {"kind": "management", "origin": origin, "received_ns": time.time_ns()}
        )
        operation = (
            service.store.core_operation(source, label)
            if event is not None
            else uuid.uuid4().hex
        )
        changes = []
        targets = matches or (
            [(None, contexts["canonical"])] if action == "set" else []
        )
        for record, native in targets:
            candidate = (
                service.store.prepare_shared_core(
                    record,
                    native,
                    label=label,
                    content=content,
                    kind=kind,
                    stable_id=service.stable_id,
                )
                if action == "set"
                else record
            )
            changes.append(
                (
                    candidate,
                    int(record.metadata.get("core_revision") or 0) if record else 0,
                )
            )
        snapshot = {
            peer: self.links.context_variants(peer) for peer in self.links.owners
        }

        def authorize():
            return (
                origin in self.links.owners
                and canonical in self.links.owners
                and self.links.healthy
                and service.config.bool("core_memory.enabled", True)
                and (
                    event is None
                    or service.config.bool("core_memory.llm_management_enabled", True)
                )
                and snapshot
                == {
                    peer: self.links.context_variants(peer)
                    for peer in self.links.owners
                }
            )

        return await service.store.change_core_memories(
            owner=self.links.shared_scope,
            operation=operation,
            intent={
                "action": action,
                "label": label.casefold(),
                "content": " ".join(content.split())[:4000] if action == "set" else "",
                "kind": kind if action == "set" else "",
            },
            changes=changes,
            scopes=contexts["scopes"],
            source=source,
            authorize=authorize,
        )

    @staticmethod
    async def _history(store, matches, scopes):
        rows = await store.core_memory_history([r.id for r, _ in matches], scopes)
        history = []
        for row in rows:
            source = row["source"]
            text = source.get("text", "")
            history.append(
                {
                    "id": row["memory_id"],
                    "revision": row["revision"],
                    "content": row["record"]["content"],
                    "current": row["current"],
                    "recorded_at": row["recorded_at"],
                    "source": {
                        **source,
                        "text": text[:2000],
                        "text_truncated": len(text) > 2000,
                    },
                }
            )
        return {
            "ok": True,
            "history": history,
            "coverage": "At most 20 revisions per current block; source text at most 2000 characters. Legacy history before adoption is unavailable.",
        }

    async def _read(self, service, canonical):
        """Resolve all native scopes before a shared list or mutation."""
        records, errors, contexts = {}, [], {"scopes": []}
        for peer in self.links.owners:
            try:
                variants = self.links.context_variants(peer)
            except ValueError:
                errors.append(
                    {"channel": peer.split(":", 1)[0], "error": "identity_not_observed"}
                )
                continue
            for index, payload in enumerate(variants):
                payload = {**payload, "persona_id": "Ada"}
                native = service.session_context_from_bridge(payload)
                contexts["scopes"].append(payload)
                if peer == canonical and index == 0:
                    contexts["canonical"] = native
                try:
                    for record in await service.core_memories_for_context(
                        native, limit=1000
                    ):
                        if record.metadata.get("core_scope") == "private":
                            records[record.id] = (record, native)
                except Exception as exc:
                    errors.append(
                        {
                            "channel": peer.split(":", 1)[0],
                            "error": type(exc).__name__,
                        }
                    )
        return records, errors, contexts
