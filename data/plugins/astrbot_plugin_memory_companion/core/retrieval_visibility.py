from __future__ import annotations

from typing import Any

from .identity import session_target_id
from .models import (
    MemoryRecord,
    SessionContext,
    clean_text,
)


class RetrievalEngineVisibility:
    """Visibility; shared runtime resources belong to the facade."""

    def _slot_for_memory(self, memory: MemoryRecord, ctx: SessionContext) -> str:
        tags = {str(tag).lower() for tag in (memory.tags or [])}
        memory_type = (memory.memory_type or "").lower()
        reality = (memory.reality_level or "").lower()
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        subject = getattr(memory, "subject", None)
        subject_id = clean_text(getattr(subject, "id", ""), 120).lower()
        context_bot_id = clean_text(getattr(ctx, "bot_id", ""), 120).lower()
        bot_subject = clean_text(
            getattr(subject, "kind", ""), 40
        ).lower() == "bot" and (
            not subject_id
            or subject_id in {"self", context_bot_id}
            or not context_bot_id
        )
        # Bot-owned records carry a stronger slot signal than generic promise
        # weights.  Calibrating a schedule often raises ``open_loop_weight``
        # because its text contains "tomorrow" or "remind"; that must not move
        # a Bot schedule into the overflowable open-loop bucket.
        if (
            memory.visibility == "bot_self"
            or reality in {"bot_action", "persona_life", "fictional_content"}
            or bot_subject
            or memory_type == "schedule_fragment"
            or memory_type
            in {
                "self_action",
                "persona_life",
                "proactive_message",
                "search_action",
                "creative_work",
                "image_action",
                "qzone_action",
                "reading_memory",
                "companion_note",
            }
        ):
            return "self_timeline"
        # conversation_summary 优先归 summary 槽：避免被 open_loop 权重判定
        # 吸进 open_loop 槽（limit=1）导致 summary 槽空置。群聊总结已在
        # _memory_is_open_loop 内排除该判定，私聊总结同样应优先归 summary 槽。
        if memory_type == "conversation_summary" or "summary" in tags:
            return "conversation_summary"
        if self._memory_is_open_loop(memory, metadata):
            return "open_loop"
        if (
            memory_type
            in {
                "user_profile",
                "user_preference",
                "user_habit",
                "relationship_claim",
                "explicit_memory",
                "manual_memory",
            }
            or "stable_fact" in tags
            or "relationship_claim" in tags
        ):
            return "user_profile"
        if memory_type == "conversation_summary" or "summary" in tags:
            return "conversation_summary"
        if (
            ctx.scope == "private"
            and (memory.visibility == "private_pair" or memory.scope == "private")
        ) or (
            ctx.scope == "group"
            and (memory.visibility == "group_public" or memory.scope == "group")
        ):
            return "current_window"
        return "stable_memory"

    @staticmethod
    def _memory_is_open_loop(memory: MemoryRecord, metadata: dict[str, Any]) -> bool:
        def weight(key: str) -> float:
            try:
                return max(0.0, min(1.0, float(metadata.get(key) or 0.0)))
            except Exception:
                return 0.0

        tags = {clean_text(tag, 80).lower() for tag in (memory.tags or [])}
        memory_type = clean_text(memory.memory_type, 80).lower()
        if memory_type == "conversation_summary" and (
            memory.scope == "group" or memory.visibility == "group_public"
        ):
            return False
        phase = clean_text(metadata.get("relationship_phase"), 80).lower()
        if (
            max(
                weight("open_loop_weight"),
                weight("promise_weight"),
                weight("emotional_debt_weight"),
            )
            >= 0.35
        ):
            return True
        if max(weight("scar_weight"), weight("emotional_weight")) >= 0.58 and phase in {
            "conflict",
            "repair",
            "comfort",
            "sensitive",
        }:
            return True
        if memory_type in {"open_loop", "promise", "todo_memory"}:
            return True
        return bool(
            tags & {"open_loop", "promise", "todo", "unfinished", "emotional_debt"}
        )

    async def _acl_state(self) -> dict[str, object]:
        rules = await self.store.list_acl_rules(enabled_only=True)
        policies = await self.store.list_acl_policies()
        allow_pairs: set[tuple[str, str, str, str]] = set()
        deny_pairs: set[tuple[str, str, str, str]] = set()
        disabled_scopes = {
            scope
            for scope, enabled in (
                ("private", self.private_topology_enabled),
                ("group", self.group_topology_enabled),
            )
            if not enabled
        }
        for rule in rules:
            owner_scope = clean_text(rule.get("owner_scope"), 40)
            owner_id = clean_text(rule.get("owner_id"), 160)
            reader_scope = clean_text(rule.get("reader_scope"), 40)
            reader_id = clean_text(rule.get("reader_id"), 160)
            effect = (
                "deny"
                if clean_text(rule.get("effect"), 20).lower() == "deny"
                else "allow"
            )
            if not (owner_scope and owner_id and reader_scope and reader_id):
                continue
            if owner_scope in disabled_scopes or reader_scope in disabled_scopes:
                continue
            pair = (owner_scope, owner_id, reader_scope, reader_id)
            if effect == "deny":
                deny_pairs.add(pair)
            else:
                allow_pairs.add(pair)
        policy_map: dict[tuple[str, str], dict[str, str]] = {}
        for policy in policies:
            scope = clean_text(policy.get("window_scope"), 40)
            window_id = clean_text(policy.get("window_id"), 160)
            if not scope or not window_id:
                continue
            if scope in disabled_scopes:
                continue
            policy_map[(scope, window_id)] = {
                "read_mode": self._normalize_acl_mode(policy.get("read_mode")),
                "share_mode": self._normalize_acl_mode(policy.get("share_mode")),
            }
        return {
            "allow": allow_pairs,
            "deny": deny_pairs,
            "policies": policy_map,
            "disabled_scopes": disabled_scopes,
        }

    def _empty_acl_state(self) -> dict[str, object]:
        return {"allow": set(), "deny": set(), "policies": {}, "disabled_scopes": set()}

    def _acl_deny_reason(
        self,
        memory: MemoryRecord,
        ctx: SessionContext,
        acl_state: dict[str, object],
    ) -> str:
        owner = self._memory_owner(memory)
        reader = self._reader_window(ctx)
        if not owner or not reader or owner == reader:
            return ""
        disabled_scopes = acl_state.get("disabled_scopes", set())
        if owner[0] in disabled_scopes or reader[0] in disabled_scopes:
            return f"topology_disabled:{owner[0]}:{owner[1]}->{reader[0]}:{reader[1]}"
        deny_pairs = acl_state.get("deny", set())
        pair = (owner[0], owner[1], reader[0], reader[1])
        if pair in deny_pairs:
            return f"acl_denied:{owner[0]}:{owner[1]}->{reader[0]}:{reader[1]}"
        return ""

    def _acl_visibility_reason(
        self,
        memory: MemoryRecord,
        ctx: SessionContext,
        default_reason: str,
        acl_state: dict[str, object],
    ) -> str:
        if default_reason not in {
            "other_group_public",
            "other_private_pair",
            "private_pair_not_current_private",
        }:
            return ""
        owner = self._memory_owner(memory)
        reader = self._reader_window(ctx)
        if not owner or not reader or owner == reader:
            return ""
        disabled_scopes = acl_state.get("disabled_scopes", set())
        if owner[0] in disabled_scopes or reader[0] in disabled_scopes:
            return ""
        pair = (owner[0], owner[1], reader[0], reader[1])
        deny_pairs = acl_state.get("deny", set())
        if pair in deny_pairs:
            return ""
        allow_pairs = acl_state.get("allow", set())
        if pair in allow_pairs:
            return f"acl_allowed:{owner[0]}:{owner[1]}->{reader[0]}:{reader[1]}"
        policies = acl_state.get("policies", {})
        owner_policy = self._acl_policy_for(policies, owner)
        reader_policy = self._acl_policy_for(policies, reader)
        if (
            owner_policy.get("share_mode") == "blacklist"
            and reader_policy.get("read_mode") == "blacklist"
        ):
            if self._requires_explicit_allow(owner, reader):
                return ""
            return (
                f"acl_blacklist_default:{owner[0]}:{owner[1]}->{reader[0]}:{reader[1]}"
            )
        return ""

    def _acl_privacy_guard_reason(
        self,
        memory: MemoryRecord,
        ctx: SessionContext,
        default_reason: str,
        acl_state: dict[str, object],
    ) -> str:
        if default_reason not in {
            "other_group_public",
            "other_private_pair",
            "private_pair_not_current_private",
        }:
            return ""
        owner = self._memory_owner(memory)
        reader = self._reader_window(ctx)
        if (
            not owner
            or not reader
            or owner == reader
            or not self._requires_explicit_allow(owner, reader)
        ):
            return ""
        pair = (owner[0], owner[1], reader[0], reader[1])
        if pair in acl_state.get("allow", set()) or pair in acl_state.get(
            "deny", set()
        ):
            return ""
        policies = acl_state.get("policies", {})
        owner_policy = self._acl_policy_for(policies, owner)
        reader_policy = self._acl_policy_for(policies, reader)
        if (
            owner_policy.get("share_mode") == "blacklist"
            and reader_policy.get("read_mode") == "blacklist"
        ):
            return f"acl_privacy_guard_requires_allow:{owner[0]}:{owner[1]}->{reader[0]}:{reader[1]}"
        return ""

    def _requires_explicit_allow(
        self, owner: tuple[str, str], reader: tuple[str, str]
    ) -> bool:
        return owner[0] == "private" and reader[0] == "group"

    def _acl_policy_for(
        self, policies: object, window: tuple[str, str]
    ) -> dict[str, str]:
        if isinstance(policies, dict):
            policy = policies.get(window)
            if isinstance(policy, dict):
                return {
                    "read_mode": self._normalize_acl_mode(policy.get("read_mode")),
                    "share_mode": self._normalize_acl_mode(policy.get("share_mode")),
                }
        default_mode = "blacklist" if window[0] == "group" else "whitelist"
        return {"read_mode": default_mode, "share_mode": default_mode}

    def _normalize_acl_mode(self, mode: object) -> str:
        return (
            "blacklist" if clean_text(mode, 20).lower() == "blacklist" else "whitelist"
        )

    def _memory_owner(self, memory: MemoryRecord) -> tuple[str, str] | None:
        if memory.scope == "group" or memory.visibility == "group_public":
            owner_id = memory.group_id
            if not owner_id and memory.object.kind == "group":
                owner_id = memory.object.id
            if not owner_id and memory.subject.kind == "group":
                owner_id = memory.subject.id
            if not owner_id:
                owner_id = (
                    session_target_id(memory.session_id, "group") or memory.session_id
                )
            return ("group", clean_text(owner_id, 160)) if owner_id else None
        if memory.scope == "private" or memory.visibility == "private_pair":
            owner_id = ""
            for entity in (memory.subject, memory.object):
                if entity.kind == "user" and entity.id and entity.id != "self":
                    owner_id = entity.id
                    break
            if not owner_id:
                owner_id = (
                    session_target_id(memory.session_id, "private") or memory.session_id
                )
            return ("private", clean_text(owner_id, 160)) if owner_id else None
        return None

    def _reader_window(self, ctx: SessionContext) -> tuple[str, str] | None:
        if ctx.scope == "group":
            reader_id = ctx.group_id or ctx.session_id
            return ("group", clean_text(reader_id, 160)) if reader_id else None
        if ctx.scope == "private":
            reader_id = ctx.user_id or ctx.session_id
            return ("private", clean_text(reader_id, 160)) if reader_id else None
        return None
