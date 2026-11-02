from __future__ import annotations

import hashlib
import json
from typing import Any

from .astrbot_compat import (
    _plain_request_text,
    append_temp_text,
    logger,
    remove_temp_text,
)
from .continuity import build_context_snapshot
from .injection import (
    MEMORY_COMPANION_INJECTION_FOOTER,
    MEMORY_COMPANION_INJECTION_HEADER,
)
from .memory_revision import current_memory, memory_ref
from .models import (
    SessionContext,
    utc_now,
)


class MemoryCompanionServiceReplyContext:
    """Validate revisions and publish the reply receipt. Shared resources stay with the root owner."""

    async def _validate_reply_memory_refs(
        self,
        ctx: SessionContext,
        refs: list[dict[str, str]],
        slot_map: dict[str, list[Any]],
        *,
        timeline_reads: list[dict[str, str]] | None = None,
    ) -> tuple[bool, str]:
        revision = await self.store.memory_revision()
        records = (
            await self.store.get_memories_by_ids([ref["id"] for ref in refs])
            if refs
            else {}
        )
        timeline_items = {
            item.memory.id: item.memory
            for items in slot_map.values()
            for item in items
            if item.memory.memory_type == "timeline_event"
            and (item.memory.metadata or {}).get("source") == "time_window_timeline"
            and item.memory.id not in records
        }
        timeline_ids = [ref["id"] for ref in refs if ref["id"] in timeline_items]
        if timeline_ids:
            rows = await self.store.get_timeline_by_ids(
                [key.removeprefix("timeline_") for key in timeline_ids]
            )
            for key in timeline_ids:
                row = rows.get(key.removeprefix("timeline_"))
                if (
                    row
                    and row.get("session_id") == ctx.session_id
                    and row.get("scope") == ctx.scope
                ):
                    record = self._timeline_row_as_memory(ctx, row)
                    if record is not None:
                        records[key] = record
        core_ids = {
            key
            for key, record in records.items()
            if record.memory_type == "core_memory"
            or (record.metadata or {}).get("core_memory") is True
        }
        if core_ids:
            allowed_core = {
                record.id for record in await self.core_memories_for_context(ctx)
            }
            if not core_ids.issubset(allowed_core):
                return False, revision
        for ref in refs:
            record = records.get(ref["id"])
            if (
                record is None
                or not current_memory(record)
                or memory_ref(record) != ref
            ):
                return False, revision
            persona_id = (record.metadata or {}).get("persona_id")
            if persona_id not in {None, "", "legacy", ctx.persona_id}:
                return False, revision
        if refs:
            engine = self._retrieval_validation_engine()
            visible, _ = await engine.filter_visible_candidates(
                [record for key, record in records.items() if key not in timeline_ids],
                ctx,
                reason="reply_injection",
                include_core_memory=True,
            )
            if timeline_ids:
                # Explicit timeline evidence is separate from raw Memory rows.
                engine.policy.include_raw_events = True
                timeline_visible, _ = await engine.filter_visible_candidates(
                    [records[key] for key in timeline_ids],
                    ctx,
                    reason="reply_timeline_source",
                )
                visible.extend(timeline_visible)
            if {item.memory.id for item in visible} != {ref["id"] for ref in refs}:
                return False, revision
        # A concurrent write must not combine rows and ACL from different revisions.
        current = revision == await self.store.memory_revision()
        if current and timeline_reads is not None:
            from .source_evidence import message_source_version

            timeline_reads.extend(
                {
                    **ref,
                    "source_id": ref["id"].removeprefix("timeline_"),
                    "source_version": message_source_version(
                        rows[ref["id"].removeprefix("timeline_")]
                    ),
                }
                for ref in refs
                if ref["id"] in timeline_ids
            )
        return current, revision

    async def _publish_reply_memory_context(
        self,
        ctx: SessionContext,
        req: Any,
        *,
        event: Any,
        injection: str,
        slot_map: dict[str, list[Any]],
        strategy_id: str,
    ) -> bool:
        remove_temp_text(
            req, MEMORY_COMPANION_INJECTION_HEADER, MEMORY_COMPANION_INJECTION_FOOTER
        )
        self._mark_memory_companion_injection_state(
            event, req, injected=False, conversation_memory=False, slot_map=slot_map
        )
        rendered = getattr(injection, "continuity_snapshot", None)
        refs = getattr(injection, "memory_refs", None)
        if not isinstance(rendered, dict) or not isinstance(refs, list):
            self._mark_memory_companion_injection_state(
                event,
                req,
                injected=False,
                conversation_memory=False,
                slot_map=slot_map,
                reason_code="continuity_refs_missing",
            )
            return False
        snapshot_refs = rendered.get("continuity_refs", [])
        if {(ref["ref_id"], ref["version"]) for ref in snapshot_refs} != {
            (ref["id"], ref["version"]) for ref in refs
        }:
            self._mark_memory_companion_injection_state(
                event,
                req,
                injected=False,
                conversation_memory=False,
                slot_map=slot_map,
                reason_code="continuity_refs_incomplete",
            )
            return False
        timeline_reads = []
        current, revision = await self._validate_reply_memory_refs(
            ctx, refs, slot_map, timeline_reads=timeline_reads
        )
        if not current:
            self._mark_memory_companion_injection_state(
                event,
                req,
                injected=False,
                conversation_memory=False,
                slot_map=slot_map,
                reason_code="memory_changed_or_unavailable",
            )
            logger.info(
                "[MemoryCompanion] 本轮记忆依据已变化或不可用，继续使用当前对话: session=%s",
                ctx.session_id,
            )
            return False
        history = getattr(req, "contexts", None) or []
        history_projection = []
        for item in history:
            if isinstance(item, dict):
                history_projection.append(item)
            elif callable(getattr(item, "model_dump", None)):
                history_projection.append(
                    item.model_dump(mode="json", exclude_none=True)
                )
            else:
                history_projection.append(
                    {
                        "role": getattr(item, "role", ""),
                        "content": _plain_request_text(item),
                    }
                )
        history_digest = hashlib.sha256(
            json.dumps(
                history_projection,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        immediate_refs = [f"history:{history_digest}"]
        if ctx.message_id:
            immediate_refs.append(f"message:{ctx.message_id}")
        snapshot = build_context_snapshot(
            ctx,
            memory_refs=[{**ref, "purpose": "reply"} for ref in snapshot_refs],
            memory_revision=revision,
            immediate_refs=immediate_refs,
            coverage={
                "strategy_id": strategy_id,
                "mode": "existing_temporal"
                if slot_map.get("time_window_timeline")
                else "relevant",
                "complete": False,
                "available_history_items": len(history_projection),
                "recent_fact_context": "<recent_fact_context>" in injection,
                "recent_cross_window_context": "<recent_cross_window_context>"
                in injection,
                "memory_refs_revalidated": True,
                "history_recovery": "not_performed",
            },
            usage={
                "rendered_items": len(refs),
                "injected_chars": len(injection),
                "injection_digest": hashlib.sha256(
                    injection.encode("utf-8")
                ).hexdigest(),
                "additional_model_calls": 0,
            },
            generated_at=utc_now(),
        ).to_dict()
        role_sources = getattr(self, "_role_input_sources", None)
        role_before = None
        if role_sources is not None:
            from .role_input_sources import request_parts

            role_before = request_parts(req)
        try:
            if not append_temp_text(req, injection):
                prompt = str(getattr(req, "prompt", "") or "")
                req.prompt = f"{prompt}\n\n{injection}" if prompt else str(injection)
        except Exception:
            remove_temp_text(
                req,
                MEMORY_COMPANION_INJECTION_HEADER,
                MEMORY_COMPANION_INJECTION_FOOTER,
            )
            self._mark_memory_companion_injection_state(
                event,
                req,
                injected=False,
                conversation_memory=False,
                slot_map=slot_map,
                reason_code="memory_append_failed",
            )
            raise
        self._mark_memory_companion_injection_state(
            event,
            req,
            injected=True,
            conversation_memory=bool(slot_map.get("conversation_summary")),
            slot_map=slot_map,
            memory_refs=refs,
            continuity_snapshot=snapshot,
        )
        from .source_evidence import remember_reply_sources

        remember_reply_sources(event, ctx, timeline_reads)
        if role_sources is not None:
            try:
                role_sources.published(
                    ctx, req, event, injection, refs, timeline_reads, role_before
                )
            except Exception as exc:
                logger.warning(
                    "[MemoryCompanion] Role input receipt unavailable: %s",
                    type(exc).__name__,
                )
        await self._mark_injected_memories([ref["id"] for ref in refs])
        logger.info(
            "[MemoryCompanion] 已注入当前轮记忆: session=%s snapshot=%s count=%s chars=%s",
            ctx.session_id,
            snapshot["snapshot_id"],
            len(refs),
            len(injection),
        )
        return True
