from __future__ import annotations

import hashlib
import math
import time
from datetime import datetime
from typing import Any

from .astrbot_compat import (
    logger,
)
from .identity import (
    normalize_session_context_fields,
)
from .models import (
    EntityRef,
    SessionContext,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .service_support import (
    LOCAL_TZ,
)
from .visibility import VisibilityPolicy


class MemoryCompanionServiceRelationshipPhase:
    """service relationship phase; the facade owns all shared resources."""

    def _compute_time_of_day(self) -> str:
        """Compute time-of-day category for atmosphere hints."""
        try:
            hour = datetime.now(LOCAL_TZ).hour
        except Exception:
            return ""
        if 23 <= hour or hour < 4:
            return "late_night"
        if 4 <= hour < 7:
            return "dawn"
        if 7 <= hour < 11:
            return "early_morning"
        if 11 <= hour < 17:
            return "afternoon"
        if 17 <= hour < 20:
            return "evening"
        return "night"

    def _maybe_record_persona_touch(
        self,
        ctx: SessionContext,
        results: list[Any],
        *,
        emotional_tone: str = "neutral",
    ) -> None:
        """Record a lightweight persona touch log when emotional resonance is high."""
        if not results:
            return
        message_id = clean_text(ctx.message_id, 160)
        if not message_id:
            return
        emotional_w = 0.0
        relationship_w = 0.0
        scar_w = 0.0
        resonance = 0.0
        for item in results:
            memory = getattr(item, "memory", None)
            if memory is None:
                continue
            metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
            try:
                item_emotional_w = float(metadata.get("emotional_weight") or 0.0)
                item_relationship_w = float(metadata.get("relationship_weight") or 0.0)
                item_scar_w = float(metadata.get("scar_weight") or 0.0)
            except Exception:
                item_emotional_w = item_relationship_w = item_scar_w = 0.0
            emotional_w = max(emotional_w, item_emotional_w)
            relationship_w = max(relationship_w, item_relationship_w)
            scar_w = max(scar_w, item_scar_w)
            resonance = max(
                resonance,
                item_emotional_w * 0.5 + item_relationship_w * 0.3,
                item_scar_w * 0.7,
            )
        if resonance < 0.45:
            return
        touch_type = (
            "scar" if scar_w >= 0.55 else "warm" if emotional_w >= 0.50 else "resonance"
        )
        self._update_relationship_phase_momentum(
            ctx,
            message_id=message_id,
            touch_type=touch_type,
            emotional_w=emotional_w,
            relationship_w=relationship_w,
            scar_w=scar_w,
            emotional_tone=emotional_tone,
        )

    def _phase_key(self, ctx: SessionContext) -> str:
        identity = self._phase_identity(ctx)
        digest = hashlib.sha1(
            json_dumps(identity).encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        return f"relationship_v2:{digest}"

    def _phase_identity(self, ctx: SessionContext) -> dict[str, str]:
        normalized = self._normalized_session_context(ctx)
        scope = clean_text(normalized.scope, 40) or "unknown"
        target_id = normalized.group_id if scope == "group" else normalized.user_id
        return {
            "platform": clean_text(normalized.platform, 80),
            "bot_id": clean_text(normalized.bot_id, 120),
            "scope": scope,
            "target_id": clean_text(target_id or normalized.session_id, 200),
            "member_id": clean_text(normalized.user_id, 120)
            if scope == "group"
            else "",
        }

    def _load_relationship_phase_state(self) -> None:
        try:
            if self._RELATIONSHIP_PHASE_FILE.exists():
                data = json_loads(
                    self._RELATIONSHIP_PHASE_FILE.read_text(encoding="utf-8"), {}
                )
                if isinstance(data, dict):
                    self._relationship_phase_state = data
                    self._cleanup_relationship_phase_state(force=True)
        except Exception:
            pass

    def _cleanup_relationship_phase_state(self, *, force: bool = False) -> None:
        """Bound persisted relationship state by age and count without touching active state."""

        now = time.time()
        if not force and now - self._relationship_phase_last_cleanup < 60.0:
            return
        self._relationship_phase_last_cleanup = now
        stale_keys: list[str] = []
        ranked: list[tuple[float, str]] = []
        for key, state in self._relationship_phase_state.items():
            if not isinstance(state, dict):
                stale_keys.append(key)
                continue
            updated_at = clean_text(state.get("updated_at"), 80)
            timestamp = 0.0
            if updated_at:
                try:
                    timestamp = datetime.fromisoformat(
                        updated_at.replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError, OverflowError):
                    timestamp = 0.0
            if timestamp and now - timestamp > self._RELATIONSHIP_PHASE_STATE_TTL:
                stale_keys.append(key)
            else:
                ranked.append((timestamp, key))
        for key in stale_keys:
            self._relationship_phase_state.pop(key, None)
        overflow = len(ranked) - self._RELATIONSHIP_PHASE_STATE_MAX
        if overflow > 0:
            for _timestamp, key in sorted(ranked)[:overflow]:
                self._relationship_phase_state.pop(key, None)

    def _save_relationship_phase_state(self) -> None:
        self._cleanup_relationship_phase_state()
        temp_path = self._RELATIONSHIP_PHASE_FILE.with_suffix(
            self._RELATIONSHIP_PHASE_FILE.suffix + ".tmp"
        )
        try:
            temp_path.write_text(
                json_dumps(self._relationship_phase_state), encoding="utf-8"
            )
            temp_path.replace(self._RELATIONSHIP_PHASE_FILE)
        except Exception as exc:
            logger.warning("[MemoryCompanion] 关系阶段状态保存失败: %s", exc)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _get_relationship_phase(self, ctx: SessionContext) -> dict[str, Any]:
        identity = self._phase_identity(ctx)
        key = self._phase_key(ctx)
        if key not in self._relationship_phase_state and not identity["bot_id"]:
            matches = [
                state
                for state in self._relationship_phase_state.values()
                if isinstance(state, dict)
                and isinstance(state.get("_identity"), dict)
                and all(
                    state["_identity"].get(field, "") == identity[field]
                    for field in ("platform", "scope", "target_id", "member_id")
                )
            ]
            if len(matches) == 1:
                return matches[0]
        legacy_key = clean_text(
            f"{ctx.scope}:{ctx.current_target_id or ctx.session_id}", 120
        )
        if (
            key not in self._relationship_phase_state
            and legacy_key in self._relationship_phase_state
        ):
            self._relationship_phase_state[key] = self._relationship_phase_state.pop(
                legacy_key
            )
        if key not in self._relationship_phase_state:
            self._relationship_phase_state[key] = {
                "phase": "acquaintance",
                "momentum": 0.0,
                "last_transition_at": "",
                "touch_count": 0,
                "updated_at": utc_now(),
            }
        state = self._relationship_phase_state[key]
        state["_identity"] = identity
        return state

    def _peek_relationship_phase(self, ctx: SessionContext) -> dict[str, Any]:
        """Project an existing phase without creating or saving state."""
        fallback = {"observed": False, "phase": "unknown", "momentum_band": "unknown"}
        try:
            identity = self._phase_identity(ctx)
            key = self._phase_key(ctx)
            state = self._relationship_phase_state.get(key)
            if type(state) is not dict and not identity["bot_id"]:
                matches = [
                    candidate
                    for candidate in self._relationship_phase_state.values()
                    if type(candidate) is dict
                    and type(candidate.get("_identity")) is dict
                    and all(
                        candidate["_identity"].get(field, "") == identity[field]
                        for field in ("platform", "scope", "target_id", "member_id")
                    )
                ]
                if len(matches) == 1:
                    state = matches[0]
            if type(state) is not dict:
                legacy_key = clean_text(
                    f"{ctx.scope}:{ctx.current_target_id or ctx.session_id}", 120
                )
                legacy_state = self._relationship_phase_state.get(legacy_key)
                if type(legacy_state) is dict:
                    state = legacy_state
            if type(state) is not dict:
                return fallback
            phase = state.get("phase")
            momentum = state.get("momentum")
            touch_count = state.get("touch_count")
            if type(phase) is not str or phase not in self._PHASES:
                return fallback
            if type(momentum) not in {int, float} or type(momentum) is bool:
                return fallback
            if not math.isfinite(momentum):
                return fallback
            if type(touch_count) is not int:
                return fallback
        except Exception:
            return fallback
        momentum_band = (
            "rising"
            if momentum >= 0.08
            else "cooling"
            if momentum <= -0.08
            else "steady"
        )
        return {
            "observed": True,
            "phase": phase,
            "momentum_band": momentum_band,
            "touch_count": max(0, min(256, touch_count)),
        }

    def _update_relationship_phase_momentum(
        self,
        ctx: SessionContext,
        *,
        message_id: str = "",
        touch_type: str = "",
        emotional_w: float = 0.0,
        relationship_w: float = 0.0,
        scar_w: float = 0.0,
        emotional_tone: str = "neutral",
    ) -> bool:
        message_id = clean_text(message_id or ctx.message_id, 160)
        if not message_id:
            return False
        if (
            ctx.relationship_authority_source == "private_companion.relationship_score"
            or ctx.companion_expression_contract
        ):
            return False
        state = self._get_relationship_phase(ctx)
        recent_ids = state.get("recent_touch_message_ids")
        if not isinstance(recent_ids, list):
            recent_ids = []
        recent_ids = [
            clean_text(item, 160) for item in recent_ids if clean_text(item, 160)
        ]
        if message_id in recent_ids:
            return False
        delta = 0.0
        if touch_type == "warm":
            delta = 0.02 + emotional_w * 0.01
        elif touch_type == "resonance":
            delta = 0.015 + relationship_w * 0.008
        elif touch_type == "scar":
            delta = -0.05
            if emotional_tone == "vulnerable":
                delta *= 0.5
        state["momentum"] = max(
            self._PHASE_MOMENTUM_MIN,
            min(self._PHASE_MOMENTUM_MAX, state.get("momentum", 0.0) + delta),
        )
        state["touch_count"] = state.get("touch_count", 0) + 1
        recent_ids.append(message_id)
        state["recent_touch_message_ids"] = recent_ids[
            -self._RELATIONSHIP_TOUCH_HISTORY_LIMIT :
        ]
        state["updated_at"] = utc_now()
        self._maybe_transition_phase(ctx, state)
        self._save_relationship_phase_state()
        return True

    def _maybe_transition_phase(
        self, ctx: SessionContext, state: dict[str, Any]
    ) -> None:
        current = state.get("phase", "acquaintance")
        momentum = state.get("momentum", 0.0)
        try:
            idx = self._PHASES.index(current)
        except ValueError:
            idx = 0
        # Check upgrade
        if idx < len(self._PHASES) - 1:
            threshold = self._PHASE_THRESHOLDS[
                min(idx + 1, len(self._PHASE_THRESHOLDS) - 1)
            ]
            if momentum >= threshold and state.get("touch_count", 0) >= 5:
                state["phase"] = self._PHASES[idx + 1]
                state["last_transition_at"] = utc_now()
                state["momentum"] = max(0.0, momentum - threshold * 0.5)
                logger.info(
                    "[MemoryCompanion] 关系阶段晋升: session=%s phase=%s->%s momentum=%.3f",
                    ctx.session_id,
                    current,
                    state["phase"],
                    state["momentum"],
                )
        # Check downgrade (only if momentum very negative)
        if idx > 0 and momentum < self._PHASE_MOMENTUM_MIN * 0.8:
            state["phase"] = self._PHASES[idx - 1] if idx > 0 else current
            state["last_transition_at"] = utc_now()
            logger.info(
                "[MemoryCompanion] 关系阶段回退: session=%s phase=%s->%s momentum=%.3f",
                ctx.session_id,
                current,
                state["phase"],
                state["momentum"],
            )

    def _detect_address_phase(self, text: str) -> str:
        """Detect the address phase from user message text."""
        if not text:
            return ""
        lower = text.lower()
        for phase, terms in self._ADDRESS_TERMS.items():
            for term in terms:
                if term in lower:
                    return phase
        return ""

    def _update_address_evolution(self, ctx: SessionContext, text: str) -> None:
        """Track address evolution in the relationship phase state."""
        if (
            ctx.relationship_authority_source == "private_companion.relationship_score"
            or ctx.companion_expression_contract
        ):
            return
        phase = self._detect_address_phase(text)
        if not phase:
            return
        state = self._get_relationship_phase(ctx)
        address_log = state.get("address_log")
        if not isinstance(address_log, list):
            address_log = []
            state["address_log"] = address_log
        current_phase = state.get("current_address_phase", "")
        if phase != current_phase:
            address_log.append(
                {
                    "ts": utc_now(),
                    "phase": phase,
                    "previous": current_phase,
                }
            )
            if len(address_log) > 10:
                address_log[:] = address_log[-10:]
            state["current_address_phase"] = phase
            self._save_relationship_phase_state()

    def _address_hint_for_injection(self, ctx: SessionContext) -> str:
        """Generate address hint for injection context.

        Combines user-side address phase detection with bot-side address suggestions
        based on the current relationship phase, creating a bidirectional address
        evolution system.
        """
        preferred_address = clean_text(ctx.preferred_address, 80)
        if (
            ctx.scope == "private"
            and ctx.preferred_address_locked
            and preferred_address
        ):
            return (
                f"当前对象已明确固定称呼为“{preferred_address}”。需要直接称呼时只使用这一项，"
                "不必每句都带称呼；关系阶段建议、历史称呼、显示名和旧记忆只作识别资料，"
                "不能另造或追加亲昵称呼。若对方本轮明确要求改称呼，以本轮最新要求为准。"
            )
        if ctx.companion_expression_contract:
            return ""
        if ctx.relationship_authority_source == "private_companion.relationship_score":
            label = clean_text(
                ctx.companion_relationship_phase_label, 40
            ) or clean_text(
                ctx.companion_relationship_phase_key,
                40,
            )
            tone = clean_text(ctx.companion_relationship_tone, 160)
            address = clean_text(ctx.companion_relationship_address_level, 120)
            return (
                f"陪伴插件是长期亲密度权威：当前阶段为“{label or '未标注'}”。"
                f"基础语气按“{tone or '自然、尊重当前节奏'}”，称呼尺度按“{address or '沿用已确认称呼'}”。"
                "Memory 仅提供记忆触动趋势和相关事实，不得用自身阶段另行升级称呼、语气或权限。"
            )
        state = self._get_relationship_phase(ctx)
        user_address_phase = state.get("current_address_phase", "")
        relationship_phase = state.get("phase", "acquaintance")
        user_hints = {
            "casual": "对方用比较随意的语气称呼你，可以更放松地回应。",
            "intimate": "对方用了亲密称呼，记忆中如果有共同经历可以更自然地融入，用'我也记得'的语气。",
            "playful": "对方在开玩笑，可以用轻松的方式提起有趣的旧事。",
        }
        # A mismatch needs one calibrated instruction, not a concatenation of
        # contradictory address advice from different historical signals.
        if user_address_phase == "intimate" and relationship_phase in (
            "acquaintance",
            "familiar",
        ):
            return "对方用了更亲密的称呼；自然接住这份亲近，但先保持轻量回应，不要直接翻共同历史或过度跟进。"
        if user_address_phase == "formal" and relationship_phase in (
            "close",
            "intimate",
            "deeply_bonded",
        ):
            return "对方突然变正式了；自然配合对方当前的节奏，不主动加重亲密称呼。"
        if user_address_phase and user_address_phase != "formal":
            return user_hints.get(user_address_phase, "")
        bot_suggestion = self._BOT_ADDRESS_SUGGESTIONS.get(relationship_phase, {})
        return clean_text(bot_suggestion.get("hint"), 240)

    def _apply_scar_scene_gate(
        self,
        ctx: SessionContext,
        slot_map: dict[str, list[Any]],
        *,
        companion_bot_energy: float = 0.0,
        time_of_day: str = "",
    ) -> dict[str, list[Any]]:
        """Gate scar memories based on time-of-day and bot energy."""
        if not slot_map:
            return slot_map
        is_late_night = time_of_day in ("late_night", "dawn")
        low_energy = 0 < companion_bot_energy < 40
        if not is_late_night and not low_energy:
            return slot_map
        result: dict[str, list[Any]] = {}
        for slot, items in slot_map.items():
            gated: list[Any] = []
            for item in items or []:
                memory = getattr(item, "memory", None)
                if memory is None:
                    gated.append(item)
                    continue
                metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
                try:
                    scar_w = float(metadata.get("scar_weight") or 0.0)
                except Exception:
                    scar_w = 0.0
                if scar_w >= 0.55 and (is_late_night or low_energy):
                    policy = clean_text(metadata.get("mention_policy"), 60)
                    if policy != "avoid_unless_asked":
                        metadata = dict(metadata)
                        metadata["mention_policy"] = "tone_only"
                        metadata["_scene_gated"] = True
                        try:
                            memory.metadata = metadata
                        except Exception:
                            pass
                gated.append(item)
            result[slot] = gated
        return result

    async def note_identity(self, ctx: SessionContext) -> None:
        identities: list[dict[str, Any]] = []
        if ctx.user_id:
            identities.append(
                {
                    "platform": ctx.platform,
                    "entity": EntityRef(
                        kind="user",
                        id=ctx.user_id,
                        name=ctx.user_name,
                        role="current_sender",
                    ),
                    "aliases": [ctx.user_name] if ctx.user_name else [],
                    "profile": {
                        "last_session": ctx.session_id,
                        "last_scope": ctx.scope,
                    },
                    "confidence": 0.7,
                }
            )
        if ctx.group_id:
            identities.append(
                {
                    "platform": ctx.platform,
                    "entity": EntityRef(
                        kind="group", id=ctx.group_id, name=ctx.group_name, role="group"
                    ),
                    "profile": {"last_session": ctx.session_id},
                    "confidence": 0.7,
                }
            )
        if identities:
            await self.store.upsert_identities(identities)

    async def note_relationships(
        self, ctx: SessionContext, source_memory_id: str = ""
    ) -> None:
        if ctx.scope == "group" and ctx.user_id and ctx.group_id:
            await self.store.upsert_relationship(
                subject=EntityRef(
                    kind="user", id=ctx.user_id, name=ctx.user_name, role="group_member"
                ),
                object=EntityRef(
                    kind="group", id=ctx.group_id, name=ctx.group_name, role="group"
                ),
                relation_type="member_of_group",
                scope="group",
                session_id=ctx.session_id,
                group_id=ctx.group_id,
                visibility="group_public",
                evidence=clean_text(ctx.message_text, 500),
                confidence=0.8,
                review_status="auto",
                source_memory_id=source_memory_id,
                metadata={"observed_from": "group_message"},
            )

    def visibility_policy(self, *, admin_read_all: bool = False) -> VisibilityPolicy:
        return VisibilityPolicy(
            allow_self_timeline_everywhere=self.config.bool(
                "visibility.allow_self_timeline_everywhere", True
            ),
            allow_group_public_in_private=self.config.bool(
                "visibility.allow_group_public_in_private", False
            ),
            hide_pending_review=self.config.bool(
                "visibility.hide_pending_review", True
            ),
            include_raw_events=self.config.bool(
                "memory_injection.include_raw_events", False
            ),
            enable_acl_rules=self.config.bool("visibility.enable_acl_rules", True)
            and not admin_read_all,
            admin_read_all=admin_read_all,
        )

    def session_context_from_bridge(
        self, session_context: SessionContext | dict[str, Any] | None
    ) -> SessionContext:
        if isinstance(session_context, SessionContext):
            return self._normalized_session_context(session_context)
        payload = session_context or {}
        normalized = normalize_session_context_fields(
            session_id=str(payload.get("session_id") or ""),
            scope=str(payload.get("scope") or "unknown"),
            platform=str(payload.get("platform") or ""),
            user_id=str(payload.get("user_id") or ""),
            group_id=str(payload.get("group_id") or ""),
        )
        return SessionContext(
            session_id=normalized["session_id"],
            scope=normalized["scope"],
            platform=normalized["platform"],
            user_id=normalized["user_id"],
            user_name=str(payload.get("user_name") or ""),
            group_id=normalized["group_id"],
            group_name=str(payload.get("group_name") or ""),
            bot_id=str(payload.get("bot_id") or ""),
            persona_id=str(payload.get("persona_id") or ""),
            message_id=str(payload.get("message_id") or ""),
            message_text=str(payload.get("message_text") or ""),
            strict_session_only=bool(payload.get("strict_session_only", False)),
            preferred_address=str(payload.get("preferred_address") or ""),
            preferred_address_locked=bool(
                payload.get("preferred_address_locked", False)
            ),
        )

    def _normalized_session_context(self, ctx: SessionContext) -> SessionContext:
        normalized = normalize_session_context_fields(
            session_id=ctx.session_id,
            scope=ctx.scope,
            platform=ctx.platform,
            user_id=ctx.user_id,
            group_id=ctx.group_id,
        )
        return SessionContext(
            session_id=normalized["session_id"],
            scope=normalized["scope"],
            platform=normalized["platform"],
            user_id=normalized["user_id"],
            user_name=ctx.user_name,
            group_id=normalized["group_id"],
            group_name=ctx.group_name,
            bot_id=ctx.bot_id,
            persona_id=ctx.persona_id,
            message_id=ctx.message_id,
            message_text=ctx.message_text,
            strict_session_only=ctx.strict_session_only,
            preferred_address=ctx.preferred_address,
            preferred_address_locked=ctx.preferred_address_locked,
        )

    def stable_id(self, *parts: Any) -> str:
        raw = "|".join(clean_text(part, 500) for part in parts if part is not None)
        digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]
        return f"mem_{digest}"
