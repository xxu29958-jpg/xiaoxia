"""Construct a private shared block using native memory identity and normalization."""

from copy import deepcopy

from .core_state_versions import CoreStateError
from .models import EntityRef, MemoryRecord, clean_text


def shared_record(existing, ctx, *, label, content, kind, stable_id):
    label, content = clean_text(label, 80), clean_text(content, 4000)
    if not label or not content:
        raise CoreStateError("label_and_content_required")
    if kind not in {"rule", "boundary", "preference", "profile", "fact", "state"}:
        raise CoreStateError("invalid_kind")
    record = (
        deepcopy(existing)
        if existing
        else MemoryRecord(
            id=stable_id(
                "core_memory",
                "private",
                ctx.user_id,
                ctx.bot_id,
                "",  # Preserve the existing private shared block identity across channels.
                label.casefold(),
            ),
            memory_type="core_memory",
            subject=EntityRef(
                kind="user", id=ctx.user_id, name=ctx.user_name, role="user"
            ),
            object=EntityRef.bot_self(ctx.bot_id),
            scope="private",
            session_id=ctx.session_id,
            platform=ctx.platform,
            visibility="private_pair",
            sayability="indirect",
            lifecycle="stable_memory",
            confidence=1.0,
            importance=0.5,
            owner_bot_id=ctx.bot_id,
            validity_status="active",
            salience=0.5,
            durability="pinned",
            sensitivity="private",
            review_status="manual",
            source_plugin="memory_companion_core",
            metadata={
                "core_scope": "private",
                "core_priority": 50,
                "target_id": ctx.user_id,
                "persona_id": "",
                "owner_bot_id": ctx.bot_id,
            },
        )
    )
    record.content = content
    record.tags = ["core_memory", kind, f"core:{label}"]
    record.metadata.update(
        core_memory=True, core_label=label, core_kind=kind, core_enabled=True
    )
    record.ensure_defaults()
    return record
