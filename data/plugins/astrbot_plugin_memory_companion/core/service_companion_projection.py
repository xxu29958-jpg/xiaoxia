from __future__ import annotations

from typing import Any

from .bridge import (
    consume_authenticated_companion_projection,
)
from .models import (
    SessionContext,
    clean_text,
)


class MemoryCompanionServiceCompanionProjection:
    """Companion projection; state and transaction ownership remain in the facade."""

    @staticmethod
    def _apply_private_companion_preferred_address(
        ctx: SessionContext,
        *,
        req: Any = None,
        event: Any = None,
    ) -> None:
        preferred = clean_text(
            getattr(req, "_private_companion_preferred_address", "")
            or getattr(event, "_private_companion_preferred_address", ""),
            80,
        )
        if not preferred:
            return
        ctx.preferred_address = preferred
        ctx.preferred_address_locked = bool(
            getattr(req, "_private_companion_preferred_address_locked", False)
            or getattr(event, "_private_companion_preferred_address_locked", False)
        )

    @staticmethod
    def _apply_companion_relationship_projection(
        ctx: SessionContext,
        *,
        event: Any = None,
        req: Any = None,
    ) -> None:
        """Consume a bounded Companion-owned relationship projection in private scope."""

        if ctx.scope != "private":
            return
        raw_projection: Any = None
        for target in (event, req):
            if target is None:
                continue
            for attr in (
                "private_companion_context",
                "companion_context",
                "memory_companion_context",
            ):
                payload = getattr(target, attr, None)
                if isinstance(payload, dict) and isinstance(
                    payload.get("relationship_projection"), dict
                ):
                    raw_projection = payload["relationship_projection"]
                    break
            if raw_projection is not None:
                break
        consumed = consume_authenticated_companion_projection(
            raw_projection,
            kind="relationship",
            expected_person_id=ctx.user_id,
            expected_scope=ctx.scope,
            expected_session_id=ctx.session_id,
            expected_platform=ctx.platform,
            expected_bot_id=ctx.bot_id,
        )
        projection = consumed.get("projection") if isinstance(consumed, dict) else None
        if not isinstance(projection, dict):
            return
        ctx.relationship_authority_source = "private_companion.relationship_score"
        ctx.companion_relationship_score = int(projection.get("score") or 0)
        ctx.companion_relationship_phase_key = clean_text(
            projection.get("phase_key"), 40
        )
        ctx.companion_relationship_phase_label = clean_text(
            projection.get("phase_label"), 40
        )
        ctx.companion_relationship_tone = clean_text(projection.get("tone"), 160)
        ctx.companion_relationship_address_level = clean_text(
            projection.get("address_level"), 120
        )
        interaction = projection.get("current_interaction")
        if isinstance(interaction, dict):
            ctx.companion_interaction_dynamics_version = clean_text(
                interaction.get("dynamics_version"), 48
            )
            ctx.companion_interaction_band = clean_text(
                interaction.get("expression_band"), 24
            )
            ctx.companion_interaction_recovery_band = clean_text(
                interaction.get("recovery_band"), 24
            )
            try:
                ctx.companion_interaction_expires_at = max(
                    0.0, min(10**12, float(interaction.get("expires_at") or 0.0))
                )
                ctx.companion_interaction_projection_revision = max(
                    0, min(1000000, int(interaction.get("projection_revision") or 0))
                )
            except (TypeError, ValueError):
                ctx.companion_interaction_expires_at = 0.0
                ctx.companion_interaction_projection_revision = 0

    @staticmethod
    def _apply_companion_expression_decision(
        ctx: SessionContext,
        *,
        event: Any = None,
        req: Any = None,
    ) -> None:
        """Consume a bounded, request-scoped Companion expression decision."""

        if ctx.scope != "private":
            return
        raw_decision: Any = None
        for target in (req, event):
            if target is None:
                continue
            candidate = getattr(target, "_private_companion_expression_decision", None)
            if candidate is not None:
                raw_decision = candidate
                break
        consumed = consume_authenticated_companion_projection(
            raw_decision,
            kind="expression",
            expected_person_id=ctx.user_id,
            expected_scope=ctx.scope,
            expected_session_id=ctx.session_id,
            expected_platform=ctx.platform,
            expected_bot_id=ctx.bot_id,
        )
        decision = consumed.get("decision") if isinstance(consumed, dict) else None
        if not isinstance(decision, dict):
            return
        ctx.companion_expression_contract = clean_text(decision.get("contract"), 80)
        ctx.companion_expression_band = clean_text(decision.get("expression_band"), 40)
        ctx.companion_expression_allowed_behaviors = tuple(
            decision.get("allowed_behaviors") or ()
        )
        ctx.companion_expression_safety_mode = clean_text(
            decision.get("safety_mode"), 60
        )
        ctx.companion_expression_blocker = clean_text(decision.get("blocker"), 60)
        ctx.companion_expression_reason_codes = tuple(
            decision.get("reason_codes") or ()
        )
        ctx.companion_expression_pacing = clean_text(decision.get("pacing"), 24)
        ctx.companion_expression_directness = clean_text(decision.get("directness"), 24)
        ctx.companion_expression_validation_style = clean_text(
            decision.get("validation_style"), 24
        )
        ctx.companion_expression_self_disclosure = clean_text(
            decision.get("self_disclosure"), 24
        )
        ctx.companion_expression_humor_mode = clean_text(decision.get("humor_mode"), 24)
        ctx.companion_expression_topic_initiative = clean_text(
            decision.get("topic_initiative"), 24
        )
