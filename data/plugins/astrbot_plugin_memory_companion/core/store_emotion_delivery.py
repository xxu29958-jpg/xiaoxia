from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)


class MemoryStoreEmotionDelivery:
    """Emotion delivery; state and transaction ownership remain in the facade."""

    async def list_emotion_event_deliveries(
        self,
        *,
        consumer_id: str,
        bot_id: str,
        scope: str,
        platform: str,
        user_id: str,
        session_id: str,
        allow_cross_window: bool = False,
        cursor: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._list_emotion_event_deliveries_sync,
            consumer_id,
            bot_id,
            scope,
            platform,
            user_id,
            session_id,
            allow_cross_window,
            cursor,
            limit,
        )

    def _list_emotion_event_deliveries_sync(
        self,
        consumer_id: str,
        bot_id: str,
        scope: str,
        platform: str,
        user_id: str,
        session_id: str,
        allow_cross_window: bool,
        cursor: str,
        limit: int,
    ) -> dict[str, Any]:
        domain = self._emotion_delivery_domain(
            consumer_id=consumer_id,
            bot_id=bot_id,
            scope=scope,
            platform=platform,
            user_id=user_id,
            session_id=session_id,
            allow_cross_window=allow_cross_window,
        )
        if domain is None:
            return self._empty_emotion_delivery("delivery_domain_required")
        try:
            bounded_limit = max(1, min(20, int(limit or 10)))
        except (TypeError, ValueError):
            bounded_limit = 10
        cursor_value = clean_text(cursor, 400)
        cursor_position = self._parse_emotion_delivery_cursor(cursor_value)
        if cursor_value and cursor_position is None:
            return self._empty_emotion_delivery()
        delivery_consumer_id = self._emotion_delivery_consumer_key(
            domain["consumer_id"], domain
        )
        clauses = [
            "e.origin_kind='memory_recall'",
            "e.status NOT IN ('ignored','expired')",
            "COALESCE(d.acked_at, '')=''",
            "e.bot_id=?",
            "LOWER(e.scope)=?",
            "e.platform=?",
            "json_valid(e.actor_ref)",
            "json_extract(e.actor_ref, '$.kind')='user'",
            "CAST(json_extract(e.actor_ref, '$.id') AS TEXT)=?",
            "json_valid(e.target_ref)",
            "json_extract(e.target_ref, '$.kind')='bot'",
            "CAST(json_extract(e.target_ref, '$.id') AS TEXT)=?",
            """(
                e.expires_at=''
                OR (
                    (instr(e.expires_at, 'Z') > 0 OR substr(e.expires_at, -6, 1) IN ('+', '-'))
                    AND julianday(e.expires_at) IS NOT NULL
                    AND julianday(e.expires_at) > julianday('now')
                )
            )""",
        ]
        params: list[Any] = [
            delivery_consumer_id,
            domain["bot_id"],
            domain["scope"],
            domain["platform"],
            domain["user_id"],
            domain["bot_id"],
        ]
        if not domain["allow_cross_window"]:
            clauses.append("e.session_id=?")
            params.append(domain["session_id"])
        if cursor_position is not None:
            occurred_at, event_id, revision = cursor_position
            clauses.append(
                """(
                    e.occurred_at < ?
                    OR (e.occurred_at=? AND e.event_id < ?)
                    OR (e.occurred_at=? AND e.event_id=? AND e.revision < ?)
                )"""
            )
            params.extend(
                (
                    occurred_at,
                    occurred_at,
                    event_id,
                    occurred_at,
                    event_id,
                    revision,
                )
            )
        params.append(bounded_limit + 1)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT e.*
                FROM emotion_events e
                LEFT JOIN emotion_event_deliveries d
                  ON d.event_id=e.event_id AND d.revision=e.revision AND d.consumer_id=?
                WHERE {" AND ".join(clauses)}
                  AND {self._emotion_latest_revision_clause("e", "newer")}
                ORDER BY e.occurred_at DESC, e.event_id DESC, e.revision DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            events = [
                event
                for event in (self._emotion_event_row(row) for row in rows)
                if (
                    self._emotion_event_in_delivery_domain(event, domain)
                    and not self._emotion_event_is_expired(event)
                )
            ]
            page = events[:bounded_limit]
            has_more = len(events) > bounded_limit
            delivered_at = utc_now()
            for event in page:
                self._conn.execute(
                    """
                    INSERT INTO emotion_event_deliveries(
                        event_id, revision, consumer_id, attempts,
                        first_delivered_at, last_delivered_at, acked_at
                    ) VALUES(?,?,?,1,?,?,'')
                    ON CONFLICT(event_id, revision, consumer_id) DO UPDATE SET
                        attempts=emotion_event_deliveries.attempts+1,
                        last_delivered_at=excluded.last_delivered_at
                    """,
                    (
                        event["event_id"],
                        event["revision"],
                        delivery_consumer_id,
                        delivered_at,
                        delivered_at,
                    ),
                )
            self._conn.commit()
        projection = [self._emotion_delivery_projection(event) for event in page]
        return {
            "schema_version": "emotion_afterglow_delivery.v1",
            "events": projection,
            "next_cursor": self._emotion_delivery_cursor(page[-1])
            if page and has_more
            else "",
            "has_more": has_more,
        }

    async def ack_emotion_event_deliveries(
        self,
        *,
        consumer_id: str,
        event_refs: list[dict[str, Any]],
        bot_id: str,
        scope: str,
        platform: str,
        user_id: str,
        session_id: str,
        allow_cross_window: bool = False,
    ) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._ack_emotion_event_deliveries_sync,
            consumer_id,
            event_refs,
            bot_id,
            scope,
            platform,
            user_id,
            session_id,
            allow_cross_window,
        )

    def _ack_emotion_event_deliveries_sync(
        self,
        consumer_id: str,
        event_refs: list[dict[str, Any]],
        bot_id: str,
        scope: str,
        platform: str,
        user_id: str,
        session_id: str,
        allow_cross_window: bool,
    ) -> dict[str, Any]:
        domain = self._emotion_delivery_domain(
            consumer_id=consumer_id,
            bot_id=bot_id,
            scope=scope,
            platform=platform,
            user_id=user_id,
            session_id=session_id,
            allow_cross_window=allow_cross_window,
        )
        if domain is None or not isinstance(event_refs, list):
            return {
                "acked": 0,
                "consumer_id": clean_text(consumer_id, 80),
                "error_code": "delivery_domain_required",
            }
        unique_refs: set[tuple[str, int]] = set()
        for item in event_refs[:100]:
            if not isinstance(item, dict):
                continue
            event_id = clean_text(item.get("event_id"), 96)
            try:
                revision = max(1, min(1000000, int(item.get("revision") or 1)))
            except (TypeError, ValueError):
                continue
            if event_id:
                unique_refs.add((event_id, revision))
        acked_at = utc_now()
        acked = 0
        delivery_consumer_id = self._emotion_delivery_consumer_key(
            domain["consumer_id"], domain
        )
        with self._lock:
            for event_id, revision in unique_refs:
                row = self._conn.execute(
                    "SELECT * FROM emotion_events WHERE event_id=? AND revision=?",
                    (event_id, revision),
                ).fetchone()
                if row is None:
                    continue
                event = self._emotion_event_row(row)
                if not self._emotion_event_in_delivery_domain(
                    event, domain
                ) or self._emotion_event_is_expired(event):
                    continue
                result = self._conn.execute(
                    """
                    UPDATE emotion_event_deliveries
                    SET acked_at=?
                    WHERE event_id=? AND revision=? AND consumer_id=? AND acked_at=''
                    """,
                    (acked_at, event_id, revision, delivery_consumer_id),
                )
                acked += max(0, int(result.rowcount or 0))
            self._conn.commit()
        return {
            "acked": acked,
            "consumer_id": domain["consumer_id"],
            "acked_at": acked_at,
        }

    @staticmethod
    def _empty_emotion_delivery(error_code: str = "") -> dict[str, Any]:
        result = {
            "schema_version": "emotion_afterglow_delivery.v1",
            "events": [],
            "next_cursor": "",
            "has_more": False,
        }
        if error_code:
            result["error_code"] = error_code
        return result

    @staticmethod
    def _emotion_delivery_domain(
        *,
        consumer_id: Any,
        bot_id: Any,
        scope: Any,
        platform: Any,
        user_id: Any,
        session_id: Any,
        allow_cross_window: Any,
    ) -> dict[str, Any] | None:
        domain = {
            "consumer_id": clean_text(consumer_id, 80),
            "bot_id": clean_text(bot_id, 160),
            "scope": clean_text(scope, 24).lower(),
            "platform": clean_text(platform, 80),
            "user_id": clean_text(user_id, 160),
            "session_id": clean_text(session_id, 220),
            "allow_cross_window": allow_cross_window,
        }
        if (
            not all(
                domain[key]
                for key in (
                    "consumer_id",
                    "bot_id",
                    "platform",
                    "user_id",
                    "session_id",
                )
            )
            or domain["scope"] != "private"
            or type(domain["allow_cross_window"]) is not bool
        ):
            return None
        return domain

    @staticmethod
    def _emotion_event_in_delivery_domain(
        event: dict[str, Any], domain: dict[str, Any]
    ) -> bool:
        actor = (
            event.get("actor_ref") if isinstance(event.get("actor_ref"), dict) else {}
        )
        target = (
            event.get("target_ref") if isinstance(event.get("target_ref"), dict) else {}
        )
        return (
            clean_text(event.get("origin_kind"), 40) == "memory_recall"
            and clean_text(event.get("bot_id"), 160) == domain["bot_id"]
            and clean_text(event.get("scope"), 24).lower() == domain["scope"]
            and clean_text(event.get("platform"), 80) == domain["platform"]
            and clean_text(actor.get("kind"), 24) == "user"
            and clean_text(actor.get("id"), 160) == domain["user_id"]
            and clean_text(target.get("kind"), 24) == "bot"
            and clean_text(target.get("id"), 160) == domain["bot_id"]
            and (
                domain["allow_cross_window"]
                or clean_text(event.get("session_id"), 220) == domain["session_id"]
            )
        )

    @staticmethod
    def _emotion_event_identity_domain(event: dict[str, Any]) -> tuple[str, ...]:
        actor = (
            event.get("actor_ref") if isinstance(event.get("actor_ref"), dict) else {}
        )
        target = (
            event.get("target_ref") if isinstance(event.get("target_ref"), dict) else {}
        )
        return (
            clean_text(event.get("producer_plugin"), 80),
            clean_text(event.get("origin_kind"), 40),
            clean_text(event.get("platform"), 80),
            clean_text(event.get("bot_id"), 160),
            clean_text(event.get("scope"), 24).lower(),
            clean_text(event.get("session_id"), 220),
            clean_text(actor.get("kind"), 24),
            clean_text(actor.get("id"), 160),
            clean_text(target.get("kind"), 24),
            clean_text(target.get("id"), 160),
        )

    @staticmethod
    def _emotion_delivery_consumer_key(consumer_id: str, domain: dict[str, Any]) -> str:
        """Namespace delivery state so legacy duplicate event IDs cannot share acks."""

        identity = json_dumps(
            {
                "consumer_id": clean_text(consumer_id, 80),
                "bot_id": clean_text(domain.get("bot_id"), 160),
                "scope": clean_text(domain.get("scope"), 24).lower(),
                "platform": clean_text(domain.get("platform"), 80),
                "user_id": clean_text(domain.get("user_id"), 160),
                "session_id": clean_text(domain.get("session_id"), 220),
            }
        )
        return f"{clean_text(consumer_id, 80)}@{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _emotion_latest_revision_clause(event_alias: str, newer_alias: str) -> str:
        def ref_value(alias: str, column: str, key: str) -> str:
            return (
                f"COALESCE(CASE WHEN json_valid({alias}.{column}) "
                f"THEN CAST(json_extract({alias}.{column}, '$.{key}') AS TEXT) END, '')"
            )

        return f"""NOT EXISTS (
            SELECT 1
            FROM emotion_events {newer_alias}
            WHERE {newer_alias}.event_id={event_alias}.event_id
              AND {newer_alias}.producer_plugin={event_alias}.producer_plugin
              AND {newer_alias}.origin_kind={event_alias}.origin_kind
              AND {newer_alias}.platform={event_alias}.platform
              AND {newer_alias}.bot_id={event_alias}.bot_id
              AND LOWER({newer_alias}.scope)=LOWER({event_alias}.scope)
              AND {newer_alias}.session_id={event_alias}.session_id
              AND {ref_value(newer_alias, "actor_ref", "kind")}={ref_value(event_alias, "actor_ref", "kind")}
              AND {ref_value(newer_alias, "actor_ref", "id")}={ref_value(event_alias, "actor_ref", "id")}
              AND {ref_value(newer_alias, "target_ref", "kind")}={ref_value(event_alias, "target_ref", "kind")}
              AND {ref_value(newer_alias, "target_ref", "id")}={ref_value(event_alias, "target_ref", "id")}
              AND {newer_alias}.revision>{event_alias}.revision
        )"""

    @staticmethod
    def _emotion_event_is_expired(
        event: dict[str, Any], *, now: datetime | None = None
    ) -> bool:
        expires_at = clean_text(event.get("expires_at"), 48)
        if not expires_at:
            return False
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return True
            return parsed.astimezone(timezone.utc) <= (
                now or datetime.now(timezone.utc)
            )
        except (TypeError, ValueError, OverflowError):
            return True

    @staticmethod
    def _emotion_delivery_cursor(event: dict[str, Any]) -> str:
        return json_dumps(
            {
                "occurred_at": clean_text(event.get("occurred_at"), 48),
                "event_id": clean_text(event.get("event_id"), 96),
                "revision": max(1, int(event.get("revision") or 1)),
            }
        )

    @staticmethod
    def _parse_emotion_delivery_cursor(value: Any) -> tuple[str, str, int] | None:
        cursor = clean_text(value, 400)
        if not cursor:
            return None
        payload = json_loads(cursor, {})
        if isinstance(payload, dict):
            occurred_at = clean_text(payload.get("occurred_at"), 48)
            event_id = clean_text(payload.get("event_id"), 96)
            revision = payload.get("revision")
            try:
                normalized_revision = max(1, min(1_000_000, int(revision)))
            except (TypeError, ValueError):
                normalized_revision = 0
            if occurred_at and event_id and normalized_revision:
                return occurred_at, event_id, normalized_revision
        legacy = cursor.rsplit("|", 2)
        if len(legacy) != 3:
            return None
        occurred_at = clean_text(legacy[0], 48)
        event_id = clean_text(legacy[1], 96)
        try:
            revision = max(1, min(1_000_000, int(legacy[2])))
        except (TypeError, ValueError):
            return None
        return (occurred_at, event_id, revision) if occurred_at and event_id else None

    @staticmethod
    def _emotion_delivery_projection(event: dict[str, Any]) -> dict[str, Any]:
        from .affect_modulation import normalize_affect_modulation

        projection = {
            "event_id": clean_text(event.get("event_id"), 96),
            "revision": max(1, int(event.get("revision") or 1)),
            "trace_id": clean_text(event.get("trace_id"), 96),
            "event_type": clean_text(event.get("event_type"), 48),
            "intensity": float(event.get("intensity") or 0.0),
            "confidence": float(event.get("confidence") or 0.0),
            "energy_delta": float(event.get("applied_energy_delta") or 0.0),
            "valence": float(event.get("valence_hint") or 0.0),
            "arousal": float(event.get("arousal_hint") or 0.0),
            "vulnerability": float(event.get("vulnerability_hint") or 0.0),
            "occurred_at": clean_text(event.get("occurred_at"), 48),
            "expires_at": clean_text(event.get("expires_at"), 48),
        }
        projection["affect_modulation"] = normalize_affect_modulation(
            {
                "valence": projection["valence"],
                "arousal": projection["arousal"],
                "vulnerability": projection["vulnerability"],
                "confidence": projection["confidence"],
                "source_event_ids": [projection["event_id"]],
            }
        )
        return projection

    @staticmethod
    def _emotion_event_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("actor_ref", "target_ref", "quoted_target_ref"):
            result[key] = json_loads(result.get(key), {})
        result["reason_codes"] = json_loads(result.get("reason_codes"), [])
        result.pop("created_at", None)
        result["schema_version"] = "companion_emotion_event.v1"
        return result
