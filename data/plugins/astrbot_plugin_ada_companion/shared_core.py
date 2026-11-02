"""Own shared core-preference reads and serialized edits through native storage."""

import asyncio


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
    ):
        """Manage one owner's core blocks through all verified native scopes.

        Args:
            origin: Actual private event origin, not a model argument.
            service: The active MemoryCompanion service.
            canonical: Configured owner session for new blocks.
            action: List, set, or delete shared blocks.
            label: Existing stable block label.
            content: Complete replacement text for set.
            kind: Native core-memory category.

        Returns:
            Native records or write receipts, including explicit partial errors.
        """
        if origin not in self.links.owners or canonical not in self.links.owners:
            return {"ok": False, "error": "unlinked_session"}
        if service is None:
            return {"ok": False, "error": "memory_unavailable"}
        if action not in {"list", "set", "delete"}:
            return {"ok": False, "error": "invalid_action"}
        async with self.core_lock:
            records, errors, contexts = await self._read(service, canonical)
            if action == "list":
                return {
                    "ok": not errors,
                    "blocks": [
                        {
                            "id": record.id,
                            "label": record.metadata.get("core_label", ""),
                            "content": record.content,
                            "kind": record.metadata.get("core_kind", "preference"),
                            "revision": record.metadata.get("core_revision", 0),
                            "source": native.session_id.split(":", 1)[0],
                        }
                        for record, native in records.values()
                    ],
                    "errors": errors,
                }
            # A partial read cannot safely decide whether a block is new or absent.
            if errors or "canonical" not in contexts:
                return {
                    "ok": False,
                    "error": "shared_memory_read_failed",
                    "errors": errors,
                }
            label = label.strip()[:80]
            if not label or (action == "set" and not content.strip()):
                return {"ok": False, "error": "label_and_content_required"}
            matches = [
                (record, native)
                for record, native in records.values()
                if str(record.metadata.get("core_label", "")).casefold()
                == label.casefold()
            ]
            if action == "delete" and not matches:
                return {"ok": False, "error": "not_found"}
            if expected is not None and (
                not matches or any(record.content != expected for record, _ in matches)
            ):
                return {"ok": False, "error": "这条记忆已变化，请重新读取后再修改。"}
            writes = []
            for record, native in matches or [(None, contexts["canonical"])]:
                try:
                    if action == "delete":
                        receipt = await service.delete_core_memory_block(record.id)
                    else:
                        meta = record.metadata if record else {}
                        receipt = await service.save_core_memory_block(
                            {
                                "id": record.id if record else "",
                                "expected_revision": int(
                                    meta.get("core_revision") or 0
                                ),
                                "label": label,
                                "content": content,
                                "kind": kind,
                                "priority": int(meta.get("core_priority", 50)),
                                "enabled": True,
                                "scope": "private",
                                "target_id": native.user_id,
                                "target_name": native.user_name,
                                "bot_id": native.bot_id,
                                "persona_id": str(meta.get("persona_id") or ""),
                                "platform": native.platform,
                                "session_id": native.session_id,
                            }
                        )
                    writes.append(receipt)
                except Exception as exc:
                    writes.append({"ok": False, "error": type(exc).__name__})
            return {"ok": all(x.get("ok") for x in writes), "writes": writes}

    async def _read(self, service, canonical):
        """Resolve all native scopes before a shared list or mutation."""
        records, errors, contexts = {}, [], {}
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
