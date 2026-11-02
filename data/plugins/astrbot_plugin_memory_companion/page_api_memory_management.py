from __future__ import annotations

from typing import Any

from quart import request

from .core.bridge import serialize_memory
from .core.memory_atom import DURABILITY_LEVELS, SENSITIVITY_LEVELS, VALIDITY_STATUSES
from .core.models import clean_text
from .page_api_support import (
    MEMORY_LIFECYCLE_FILTERS,
    MEMORY_TYPE_FILTERS,
    MEMORY_VISIBILITY_FILTERS,
    PLUGIN_NAME,
    logger,
)


class PluginPageApiMemoryManagement:
    """Memory management; shared runtime resources belong to the facade."""

    async def persona_state(self):
        """Return read-only expression coordination and memory-touch diagnostics."""
        try:
            service = self.plugin.service
            touch_trends: list[dict[str, Any]] = []
            phase_state = getattr(service, "_relationship_phase_state", None)
            if isinstance(phase_state, dict):
                for key, state in phase_state.items():
                    if not isinstance(state, dict):
                        continue
                    identity = (
                        state.get("_identity")
                        if isinstance(state.get("_identity"), dict)
                        else {}
                    )
                    identity_parts = [
                        clean_text(identity.get("platform"), 80),
                        clean_text(identity.get("bot_id"), 120),
                        clean_text(identity.get("scope"), 40),
                        clean_text(identity.get("target_id"), 200),
                    ]
                    member_id = clean_text(identity.get("member_id"), 120)
                    if member_id:
                        identity_parts.append(f"member={member_id}")
                    raw_momentum = state.get("momentum", 0.0)
                    momentum = (
                        float(raw_momentum)
                        if type(raw_momentum) in {int, float}
                        else 0.0
                    )
                    trend_band = (
                        "rising"
                        if momentum >= 0.08
                        else "cooling"
                        if momentum <= -0.08
                        else "steady"
                    )
                    touch_trends.append(
                        {
                            "session_key": key,
                            "session_label": " / ".join(
                                part for part in identity_parts if part
                            )
                            or key,
                            "trend_band": trend_band,
                            "touch_count": state.get("touch_count", 0),
                            "legacy_context": clean_text(state.get("phase"), 40),
                            "updated_at": state.get("updated_at", ""),
                        }
                    )
            touch_trends.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
            memory_touch_events: list[dict[str, Any]] = []
            event_queue = getattr(service, "_emotional_event_queue", None)
            if isinstance(event_queue, dict):
                for session_id, queue in event_queue.items():
                    if not isinstance(queue, list):
                        continue
                    for event in queue[-3:]:
                        if not isinstance(event, dict):
                            continue
                        memory_touch_events.append(
                            {
                                "session_id": session_id,
                                "event_type": event.get("event_type"),
                                "energy_delta": event.get("energy_delta"),
                                "mood_hint": event.get("mood_hint"),
                                "content_preview": str(
                                    event.get("content_preview", "")
                                )[:80],
                                "ts": event.get("ts"),
                            }
                        )
            time_of_day = ""
            if hasattr(service, "_compute_time_of_day"):
                time_of_day = service._compute_time_of_day()
            cross_window_state: dict[str, Any] = {
                "total": 0,
                "scar_count": 0,
                "warm_count": 0,
                "vulnerable_count": 0,
            }
            if hasattr(service, "_get_cross_window_emotional_state"):
                cross_window_state = service._get_cross_window_emotional_state()
            return self._ok(
                {
                    "expression_coordination": {
                        "contract": "companion_interaction_expression.v1",
                        "mode": "request_scoped_read_only",
                        "expression_authority": "private_companion",
                        "memory_role": "recall_visibility_and_mention_cap",
                        "persistent": False,
                    },
                    "memory_touch_trends": touch_trends[:20],
                    "memory_touch_events": memory_touch_events[:15],
                    "time_of_day": time_of_day,
                    "cross_window_emotional_state": cross_window_state,
                    "time_of_day_labels": {
                        "late_night": "深夜",
                        "dawn": "凌晨",
                        "early_morning": "清晨",
                        "afternoon": "下午",
                        "evening": "傍晚",
                        "night": "夜间",
                    },
                    "legacy_context_labels": {
                        "acquaintance": "初识",
                        "familiar": "熟悉",
                        "close": "亲近",
                        "intimate": "亲密",
                        "deeply_bonded": "深伴",
                    },
                }
            )
        except Exception as exc:
            return self._err(f"互动协同数据读取失败: {exc}", 500)

    async def acl_matrix(self):
        """Return all windows, ACL rules and policies in one shot for topology visualization."""
        try:
            store = self.plugin.service.store
            scope_control = {
                "private_capture_enabled": self.plugin.service.config.bool(
                    "scope_control.private_capture_enabled", True
                ),
                "group_capture_enabled": self.plugin.service.config.bool(
                    "scope_control.group_capture_enabled", True
                ),
                "private_recall_enabled": self.plugin.service.config.bool(
                    "scope_control.private_recall_enabled", True
                ),
                "group_recall_enabled": self.plugin.service.config.bool(
                    "scope_control.group_recall_enabled", True
                ),
                "private_topology_enabled": self.plugin.service.config.bool(
                    "scope_control.private_topology_enabled", True
                ),
                "group_topology_enabled": self.plugin.service.config.bool(
                    "scope_control.group_topology_enabled", True
                ),
            }
            topology_scopes = {
                scope
                for scope in ("private", "group")
                if scope_control[f"{scope}_topology_enabled"]
            }
            buckets = await store.list_memory_buckets(
                limit=200,
                include_raw_events=self.plugin.service.config.bool(
                    "memory_injection.include_raw_events",
                    False,
                ),
                include_archive=True,
            )
            windows: list[dict[str, Any]] = []
            for b in buckets:
                scope = clean_text(b.get("scope"), 40)
                tid = clean_text(b.get("target_id"), 160)
                if scope in topology_scopes and tid:
                    windows.append(
                        {
                            "scope": scope,
                            "id": tid,
                            "label": clean_text(b.get("label"), 120),
                            "target_name": clean_text(b.get("target_name"), 120),
                            "target_kind": clean_text(b.get("target_kind"), 40),
                            "sample_session_id": clean_text(
                                b.get("sample_session_id"), 200
                            ),
                            "sample_group_id": clean_text(
                                b.get("sample_group_id"), 120
                            ),
                            "memory_count": b.get("memory_count", 0),
                        }
                    )
            rules = await store.list_acl_rules(enabled_only=False)
            policies = await store.list_acl_policies()
            return self._ok(
                {
                    "scope_control": scope_control,
                    "windows": windows,
                    "rules": [
                        {
                            "id": clean_text(r.get("id"), 120),
                            "owner_scope": clean_text(r.get("owner_scope"), 40),
                            "owner_id": clean_text(r.get("owner_id"), 160),
                            "reader_scope": clean_text(r.get("reader_scope"), 40),
                            "reader_id": clean_text(r.get("reader_id"), 160),
                            "effect": r.get("effect") or "allow",
                            "enabled": bool(r.get("enabled", True)),
                        }
                        for r in rules
                        if clean_text(r.get("owner_scope"), 40) in topology_scopes
                        and clean_text(r.get("reader_scope"), 40) in topology_scopes
                    ],
                    "policies": [
                        {
                            "window_scope": clean_text(p.get("window_scope"), 40),
                            "window_id": clean_text(p.get("window_id"), 160),
                            "read_mode": p.get("read_mode")
                            or (
                                "blacklist"
                                if clean_text(p.get("window_scope"), 40) == "group"
                                else "whitelist"
                            ),
                            "share_mode": p.get("share_mode")
                            or (
                                "blacklist"
                                if clean_text(p.get("window_scope"), 40) == "group"
                                else "whitelist"
                            ),
                            "capture_enabled": p.get("capture_enabled"),
                            "recall_enabled": p.get("recall_enabled"),
                        }
                        for p in policies
                        if clean_text(p.get("window_scope"), 40) in topology_scopes
                    ],
                }
            )
        except Exception as exc:
            return self._err(f"权限矩阵读取失败: {exc}", 500)

    async def buckets(self):
        buckets = await self.plugin.service.store.list_memory_buckets(
            limit=self._query_int("limit", 160),
            include_raw_events=self.plugin.service.config.bool(
                "memory_injection.include_raw_events",
                False,
            ),
        )
        for bucket in buckets:
            bucket.pop("pending_count", None)
        return self._ok({"buckets": buckets})

    async def memories(self):
        limit = self._query_int("limit", 50)
        query = clean_text(request.args.get("q", ""), 200)
        scope = clean_text(request.args.get("scope", ""), 40)
        visibility = clean_text(request.args.get("visibility", ""), 40)
        lifecycle = clean_text(request.args.get("lifecycle", ""), 40)
        lifecycle_values = MEMORY_LIFECYCLE_FILTERS.get(lifecycle)
        visibility_values = MEMORY_VISIBILITY_FILTERS.get(visibility)
        requested_type = clean_text(request.args.get("memory_type", ""), 80)
        memory_types = MEMORY_TYPE_FILTERS.get(requested_type)
        if scope == "profile" and not memory_types:
            memory_types = (
                MEMORY_TYPE_FILTERS["profile"]
                + MEMORY_TYPE_FILTERS["preference"]
                + MEMORY_TYPE_FILTERS["relationship"]
            )
        records = await self.plugin.service.store.list_memories(
            limit=max(limit * 2, 50),
            include_pending=False,
            query=query,
            memory_type="" if memory_types else requested_type,
            memory_types=memory_types,
            scope="" if scope in {"profile", "external"} else scope,
            visibility="" if visibility_values else visibility,
            visibility_values=visibility_values,
            source_plugin_exclude="bot_personal_bridge",
            review_status="",
            lifecycle="" if lifecycle_values else lifecycle,
            lifecycle_values=lifecycle_values,
            session_id=clean_text(request.args.get("session_id", ""), 200),
            group_id=clean_text(request.args.get("group_id", ""), 120),
            entity_id=clean_text(request.args.get("entity_id", ""), 120),
        )
        if scope == "external":
            records = [
                record
                for record in records
                if clean_text(getattr(record, "source_plugin", ""), 120) != PLUGIN_NAME
            ]
        records = [
            record
            for record in records
            if clean_text(getattr(record, "source_plugin", ""), 120)
            != "bot_personal_bridge"
        ][:limit]
        return self._ok({"memories": [serialize_memory(record) for record in records]})

    async def memory_detail(self):
        memory_id = clean_text(request.args.get("id", ""), 120)
        if not memory_id:
            return self._err("missing id", 400)
        record = await self.plugin.service.store.get_memory(memory_id)
        if not record:
            return self._err("memory not found", 404)
        payload = serialize_memory(record)
        payload["evidence"] = record.evidence
        payload["metadata"] = record.metadata
        payload["merged_count"] = record.merged_count
        payload["content_fingerprint"] = record.content_fingerprint
        return self._ok({"memory": payload})

    async def memory_update(self):
        payload = await self._json()
        memory_id = clean_text(payload.get("id"), 120)
        if not memory_id:
            return self._err("missing id", 400)
        current = await self.plugin.service.store.get_memory(memory_id)
        if current is None:
            return self._err("memory not found", 404)
        validity_status = self._optional_choice(
            payload, "validity_status", VALIDITY_STATUSES
        )
        durability = self._optional_choice(payload, "durability", DURABILITY_LEVELS)
        sensitivity = self._optional_choice(payload, "sensitivity", SENSITIVITY_LEVELS)
        try:
            valid_from = self._optional_iso_timestamp(payload, "valid_from")
            valid_to = self._optional_iso_timestamp(payload, "valid_to")
            salience = self._optional_unit_score(payload, "salience")
        except ValueError as exc:
            return self._err(clean_text(exc, 240), 400)
        if validity_status is False:
            return self._err("invalid validity_status", 400)
        if durability is False:
            return self._err("invalid durability", 400)
        if sensitivity is False:
            return self._err("invalid sensitivity", 400)
        effective_valid_from = (
            valid_from if valid_from is not None else current.valid_from
        )
        effective_valid_to = valid_to if valid_to is not None else current.valid_to
        if (
            effective_valid_from
            and effective_valid_to
            and self._iso_timestamp_number(effective_valid_from)
            > self._iso_timestamp_number(effective_valid_to)
        ):
            return self._err("valid_from must not be later than valid_to", 400)
        ok = await self.plugin.service.store.update_memory_payload(
            memory_id,
            memory_type=payload.get("memory_type"),
            content=payload.get("content"),
            evidence=payload.get("evidence"),
            importance=payload.get("importance"),
            confidence=payload.get("confidence"),
            visibility=payload.get("visibility"),
            lifecycle=payload.get("lifecycle"),
            validity_status=validity_status,
            valid_from=valid_from,
            valid_to=valid_to,
            salience=salience,
            durability=durability,
            sensitivity=sensitivity,
        )
        if not ok:
            return self._err("memory not found", 404)
        return self._ok({"updated": ok})

    async def memory_rebind_owner(self):
        payload = await self._json()
        try:
            result = await self.plugin.service.rebind_memory_owners(payload)
            return self._ok({"data": result})
        except ValueError as exc:
            return self._err(str(exc), 400)
        except Exception as exc:
            logger.exception("记忆 Bot 归属修正失败")
            return self._err(f"记忆 Bot 归属修正失败: {exc}", 500)

    async def memory_delete(self):
        payload = await self._json()
        ok = await self.plugin.service.store.delete_memory(
            clean_text(payload.get("id"), 120)
        )
        return self._ok({"deleted": ok})

    async def memory_visibility(self):
        payload = await self._json()
        ok = await self.plugin.service.store.update_memory_visibility(
            clean_text(payload.get("id"), 120),
            clean_text(payload.get("visibility"), 40),
        )
        return self._ok({"updated": ok})

    async def memory_lifecycle(self):
        payload = await self._json()
        ok = await self.plugin.service.store.update_memory_lifecycle(
            clean_text(payload.get("id"), 120),
            clean_text(payload.get("lifecycle"), 40),
        )
        return self._ok({"updated": ok})

    @staticmethod
    def _core_memory_payload(record: Any) -> dict[str, Any]:
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        owner_bot_id = clean_text(
            metadata.get("owner_bot_id") or record.owner_bot_id, 120
        )
        try:
            priority = max(0, min(100, int(metadata.get("core_priority", 50))))
        except (TypeError, ValueError):
            priority = 50
        try:
            revision = max(0, int(metadata.get("core_revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        return {
            "id": clean_text(record.id, 120),
            "label": clean_text(metadata.get("core_label"), 80),
            "content": record.content,
            "kind": clean_text(metadata.get("core_kind"), 32) or "fact",
            "scope": clean_text(metadata.get("core_scope"), 24) or "global",
            "target_id": clean_text(metadata.get("target_id"), 160),
            "target_name": clean_text(getattr(record.subject, "name", ""), 80),
            "bot_id": "" if owner_bot_id == "self" else owner_bot_id,
            "persona_id": clean_text(metadata.get("persona_id"), 120),
            "platform": clean_text(record.platform, 80),
            "session_id": clean_text(record.session_id, 220),
            "priority": priority,
            "enabled": metadata.get("core_enabled", True) is True,
            "revision": revision,
            "updated_at": clean_text(record.updated_at, 80),
        }

    async def core_memory_list(self):
        records = await self.plugin.service.list_core_memory_blocks()
        scope = clean_text(request.args.get("scope", ""), 24).lower()
        target_id = clean_text(request.args.get("target_id", ""), 160)
        blocks = [self._core_memory_payload(record) for record in records]
        if scope:
            blocks = [block for block in blocks if block["scope"] == scope]
        if target_id:
            blocks = [block for block in blocks if block["target_id"] == target_id]
        return self._ok({"blocks": blocks})

    async def core_memory_upsert(self):
        payload = await self._json()
        result = await self.plugin.service.save_core_memory_block(payload)
        if result.get("ok"):
            return self._ok(result)
        code = clean_text(result.get("code"), 80) or "core_memory_save_failed"
        status = (
            409
            if code in {"revision_conflict", "memory_type_conflict", "label_conflict"}
            else 400
        )
        return self._err(code, status)

    async def core_memory_delete(self):
        payload = await self._json()
        memory_id = clean_text(payload.get("id"), 120)
        if not memory_id:
            return self._err("missing id", 400)
        result = await self.plugin.service.delete_core_memory_block(memory_id)
        if result.get("ok"):
            return self._ok({"deleted": True})
        code = clean_text(result.get("code"), 80) or "core_memory_delete_failed"
        status = 404 if code == "not_found" else 409
        return self._err(code, status)

    async def acl(self):
        owner_scope = clean_text(request.args.get("scope", ""), 40)
        owner_id = clean_text(request.args.get("id", ""), 160)
        error = self._acl_window_error(owner_scope, owner_id)
        if error:
            return self._err(error, 400)
        can_read = await self.plugin.service.store.list_acl_rules(
            reader_scope=owner_scope,
            reader_id=owner_id,
            enabled_only=True,
        )
        can_be_read_by = await self.plugin.service.store.list_acl_rules(
            owner_scope=owner_scope,
            owner_id=owner_id,
            enabled_only=True,
        )
        policy = await self.plugin.service.store.get_acl_policy(owner_scope, owner_id)
        return self._ok(
            {
                "owner": {"scope": owner_scope, "id": owner_id},
                "policy": policy,
                "can_read": can_read,
                "can_be_read_by": can_be_read_by,
            }
        )

    async def acl_upsert(self):
        payload = await self._json()
        owner_scope = clean_text(payload.get("owner_scope"), 40)
        owner_id = clean_text(payload.get("owner_id"), 160)
        reader_scope = clean_text(payload.get("reader_scope"), 40)
        reader_id = clean_text(payload.get("reader_id"), 160)
        error = self._acl_window_error(owner_scope, owner_id) or self._acl_window_error(
            reader_scope, reader_id
        )
        if error:
            return self._err(error, 400)
        if owner_scope == reader_scope and owner_id == reader_id:
            return self._err("same window does not need ACL", 400)
        rule = await self.plugin.service.store.upsert_acl_rule(
            owner_scope=owner_scope,
            owner_id=owner_id,
            reader_scope=reader_scope,
            reader_id=reader_id,
            effect=self._acl_effect(payload.get("effect")),
            enabled=self._bool(payload.get("enabled"), True),
            note=clean_text(payload.get("note"), 300),
        )
        return self._ok({"rule": rule})

    async def acl_policy(self):
        payload = await self._json()
        window_scope = clean_text(
            payload.get("scope") or payload.get("window_scope"), 40
        )
        window_id = clean_text(payload.get("id") or payload.get("window_id"), 160)
        error = self._acl_window_error(window_scope, window_id)
        if error:
            return self._err(error, 400)
        policy_values: dict[str, Any] = {
            "window_scope": window_scope,
            "window_id": window_id,
            "read_mode": self._acl_mode(payload.get("read_mode")),
            "share_mode": self._acl_mode(payload.get("share_mode")),
        }
        for key in ("capture_enabled", "recall_enabled"):
            if key in payload:
                value = payload.get(key)
                if value is not None and not isinstance(value, (bool, int, str)):
                    return self._err(f"{key} must be a boolean or null", 400)
                policy_values[key] = value
        policy = await self.plugin.service.store.upsert_acl_policy(**policy_values)
        return self._ok({"policy": policy})

    async def acl_delete(self):
        payload = await self._json()
        ok = await self.plugin.service.store.delete_acl_rule(
            clean_text(payload.get("id"), 120)
        )
        return self._ok({"deleted": ok})
