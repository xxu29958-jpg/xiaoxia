from __future__ import annotations

from typing import Any

from .identity import (
    session_target_id,
)
from .models import (
    MemoryRecord,
    clean_text,
)
from .service_support import (
    _USER_MEMORY_CONVERSATION_TYPES,
    _USER_MEMORY_PREFERENCE_TYPES,
    _USER_MEMORY_PROFILE_TYPES,
    _USER_MEMORY_RELATIONSHIP_TYPES,
    USER_MEMORY_SUMMARY_VERSION,
)


class MemoryCompanionServiceUserProfile:
    """User profile; state and transaction ownership remain in the facade."""

    async def read_user_memory_summary(
        self,
        user_id: str,
        *,
        session_id: str = "",
        limit: int = 6,
    ) -> dict[str, Any]:
        """Return a bounded, content-free Memory workspace projection for one user.

        Companion remains the authority for relationship and interaction state.
        This method only reports exact-user Memory categories and opaque, redacted
        anchors that can be used to open the Memory workspace.
        """

        identity = clean_text(user_id, 120)
        requested_session = clean_text(session_id, 200)
        base = self._user_memory_summary_base(identity, requested_session)
        if not identity:
            return {**base, "error_code": "missing_user_id"}
        if (
            requested_session
            and session_target_id(requested_session, "private") != identity
        ):
            return {**base, "error_code": "session_identity_mismatch"}
        try:
            bridge_enabled = self.config.bool("private_companion_bridge.enabled", True)
        except Exception:
            return {**base, "error_code": "bridge_config_unavailable"}
        if bridge_enabled is not True:
            return {**base, "error_code": "bridge_disabled"}
        store = getattr(self, "store", None)
        reader = getattr(store, "read_user_memory_summary_records", None)
        if not callable(reader):
            return {**base, "error_code": "store_unavailable"}
        try:
            safe_limit = max(1, min(8, int(limit or 6)))
        except (TypeError, ValueError, OverflowError):
            safe_limit = 6
        try:
            raw = await reader(identity, session_id=requested_session, limit=safe_limit)
        except Exception:
            return {**base, "error_code": "store_query_failed"}
        if not isinstance(raw, dict):
            return {**base, "error_code": "invalid_store_response"}

        type_counts = (
            raw.get("type_counts") if isinstance(raw.get("type_counts"), dict) else {}
        )
        counts = {
            "profile": 0,
            "preference": 0,
            "relationship": 0,
            "private_conversation": 0,
            "other": 0,
            "total": 0,
        }
        for memory_type, value in type_counts.items():
            try:
                count = max(0, int(value))
            except (TypeError, ValueError, OverflowError):
                continue
            counts[self._user_memory_category(memory_type)] += count
            counts["total"] += count

        summaries: list[dict[str, Any]] = []
        records = raw.get("records") if isinstance(raw.get("records"), list) else []
        for record in records[:safe_limit]:
            if not isinstance(record, MemoryRecord):
                continue
            memory_type = clean_text(record.memory_type, 80)
            category = self._user_memory_category(memory_type)
            summaries.append(
                {
                    "category": category,
                    "memory_type": memory_type,
                    "occurred_at": clean_text(
                        record.occurred_at or record.created_at, 80
                    ),
                    "summary": f"{self._user_memory_category_label(category)}（内容已脱敏）",
                    "content_redacted": True,
                    "truncated": True,
                }
            )
        return {
            **base,
            "ok": True,
            "state": "ready",
            "degraded": False,
            "pending": False,
            "counts": counts,
            "summaries": summaries,
            "memory_revision": (
                raw["memory_revision"]
                if type(raw.get("memory_revision")) is int
                and raw["memory_revision"] >= 0
                else None
            ),
        }

    @staticmethod
    def _user_memory_summary_base(user_id: str, session_id: str) -> dict[str, Any]:
        return {
            "contract": USER_MEMORY_SUMMARY_VERSION,
            "ok": False,
            "read_only": True,
            "state": "degraded",
            "degraded": True,
            "pending": True,
            "user_id": user_id,
            "session_id": session_id,
            "counts": {
                "profile": 0,
                "preference": 0,
                "relationship": 0,
                "private_conversation": 0,
                "other": 0,
                "total": 0,
            },
            "summaries": [],
            "workspace": {
                "kind": "memory_user_workspace",
                "route_hint": "user_memory",
                "user_id": user_id,
            },
        }

    @staticmethod
    def _user_memory_category(memory_type: Any) -> str:
        normalized = clean_text(memory_type, 80).lower()
        if normalized in _USER_MEMORY_PROFILE_TYPES:
            return "profile"
        if normalized in _USER_MEMORY_PREFERENCE_TYPES:
            return "preference"
        if normalized in _USER_MEMORY_RELATIONSHIP_TYPES:
            return "relationship"
        if normalized in _USER_MEMORY_CONVERSATION_TYPES:
            return "private_conversation"
        return "other"

    @staticmethod
    def _user_memory_category_label(category: str) -> str:
        return {
            "profile": "用户画像记忆",
            "preference": "用户偏好记忆",
            "relationship": "关系线索记忆",
            "private_conversation": "私聊连续性记忆",
        }.get(category, "其他用户记忆")
