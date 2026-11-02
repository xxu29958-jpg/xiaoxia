from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    utc_now,
)


class MemoryStoreEmotionRecords:
    """Emotion records; state and transaction ownership remain in the facade."""

    async def upsert_emotion_event(self, value: Any) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._upsert_emotion_event_sync, value
        )

    def _upsert_emotion_event_sync(self, value: Any) -> dict[str, Any]:
        from .emotion_event_contract import normalize_emotion_event

        event = normalize_emotion_event(value, producer_plugin="memory_companion")
        with self._lock:
            with self._transaction_sync():
                existing_rows = self._conn.execute(
                    "SELECT * FROM emotion_events WHERE event_id=?",
                    (event["event_id"],),
                ).fetchall()
                event_domain = self._emotion_event_identity_domain(event)
                if any(
                    self._emotion_event_identity_domain(self._emotion_event_row(row))
                    != event_domain
                    for row in existing_rows
                ):
                    raise ValueError(
                        "emotion event_id is already bound to another identity domain"
                    )
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO emotion_events(
                        event_id, revision, trace_id, producer_plugin, origin_kind, platform,
                        bot_id, scope, session_id, actor_ref, target_ref, quoted_target_ref,
                        event_type, intensity, confidence, valence_hint, arousal_hint,
                        vulnerability_hint, source_rule, occurred_at, expires_at, dedupe_key,
                        payload_hash, privacy_level, applied_interaction, applied_energy_delta,
                        correction_of, status, reason_codes, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event["event_id"],
                        event["revision"],
                        event["trace_id"],
                        event["producer_plugin"],
                        event["origin_kind"],
                        event["platform"],
                        event["bot_id"],
                        event["scope"],
                        event["session_id"],
                        json_dumps(event["actor_ref"]),
                        json_dumps(event["target_ref"]),
                        json_dumps(event["quoted_target_ref"]),
                        event["event_type"],
                        event["intensity"],
                        event["confidence"],
                        event["valence_hint"],
                        event["arousal_hint"],
                        event["vulnerability_hint"],
                        event["source_rule"],
                        event["occurred_at"],
                        event["expires_at"],
                        event["dedupe_key"],
                        event["payload_hash"],
                        event["privacy_level"],
                        event["applied_interaction"],
                        event["applied_energy_delta"],
                        event["correction_of"],
                        event["status"],
                        json_dumps(event["reason_codes"]),
                        utc_now(),
                    ),
                )
        return event

    async def get_emotion_trace(
        self, trace_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return await self._run_recoverable_database_operation(
            self._get_emotion_trace_sync, trace_id, limit
        )

    def _get_emotion_trace_sync(
        self, trace_id: str, limit: int
    ) -> list[dict[str, Any]]:
        trace = clean_text(trace_id, 96)
        if not trace:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM emotion_events WHERE trace_id=? ORDER BY revision ASC LIMIT ?",
                (trace, max(1, min(500, int(limit or 100)))),
            ).fetchall()
        return [self._emotion_event_row(row) for row in rows]

    async def get_emotion_trace_diagnostic(
        self,
        trace_id: str,
        *,
        bot_id: str = "",
        scope: str = "",
        session_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._run_recoverable_database_operation(
            self._get_emotion_trace_diagnostic_sync,
            trace_id,
            bot_id,
            scope,
            session_id,
            limit,
        )

    def _get_emotion_trace_diagnostic_sync(
        self,
        trace_id: str,
        bot_id: str,
        scope: str,
        session_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        trace = clean_text(trace_id, 96)
        if not trace:
            return []
        clauses = ["e.trace_id=?"]
        params: list[Any] = [trace]
        for column, value, size in (
            ("bot_id", bot_id, 160),
            ("scope", scope, 24),
            ("session_id", session_id, 220),
        ):
            cleaned = clean_text(value, size)
            if cleaned:
                clauses.append(f"e.{column}=?")
                params.append(cleaned)
        params.append(max(1, min(100, int(limit or 100))))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT e.* FROM emotion_events e WHERE {' AND '.join(clauses)} ORDER BY e.revision ASC LIMIT ?",
                params,
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                event = self._emotion_event_row(row)
                delivery_rows = self._conn.execute(
                    """
                    SELECT consumer_id, attempts, first_delivered_at, last_delivered_at, acked_at
                    FROM emotion_event_deliveries
                    WHERE event_id=? AND revision=?
                    ORDER BY consumer_id ASC LIMIT 20
                    """,
                    (event["event_id"], event["revision"]),
                ).fetchall()
                deliveries = []
                for delivery in delivery_rows:
                    values = dict(delivery)
                    values["consumer_id"] = clean_text(
                        values.get("consumer_id"), 120
                    ).split("@", 1)[0]
                    deliveries.append(values)
                result.append(self._emotion_diagnostic_projection(event, deliveries))
        return result

    async def get_emotion_trace_summary(
        self,
        *,
        bot_id: str = "",
        scope: str = "",
        session_id: str = "",
        cursor: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        return await self._run_recoverable_database_operation(
            self._get_emotion_trace_summary_sync,
            bot_id,
            scope,
            session_id,
            cursor,
            limit,
        )

    def _get_emotion_trace_summary_sync(
        self,
        bot_id: str,
        scope: str,
        session_id: str,
        cursor: str,
        limit: int,
    ) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        for column, value, size in (
            ("bot_id", bot_id, 160),
            ("scope", scope, 24),
            ("session_id", session_id, 220),
        ):
            cleaned = clean_text(value, size)
            if cleaned:
                clauses.append(f"e.{column}=?")
                params.append(cleaned)
        try:
            offset = max(0, min(100000, int(cursor or 0)))
        except (TypeError, ValueError):
            offset = 0
        bounded = max(1, min(100, int(limit or 20)))
        params.extend((bounded + 1, offset))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT e.*
                FROM emotion_events e
                WHERE {" AND ".join(clauses)}
                  AND {self._emotion_latest_revision_clause("e", "newer")}
                ORDER BY e.occurred_at DESC, e.event_id DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        page = [self._emotion_event_row(row) for row in rows[:bounded]]
        return {
            "items": [
                {
                    "trace_id": clean_text(item.get("trace_id"), 96),
                    "event_id": clean_text(item.get("event_id"), 96),
                    "revision": int(item.get("revision") or 1),
                    "event_type": clean_text(item.get("event_type"), 48),
                    "status": clean_text(item.get("status"), 24),
                    "occurred_at": clean_text(item.get("occurred_at"), 48),
                    "session_hash": self._diagnostic_hash(item.get("session_id")),
                }
                for item in page
            ],
            "next_cursor": str(offset + bounded) if len(rows) > bounded else "",
            "has_more": len(rows) > bounded,
        }

    @classmethod
    def _emotion_diagnostic_projection(
        cls, event: dict[str, Any], deliveries: list[sqlite3.Row]
    ) -> dict[str, Any]:
        return {
            "schema_version": "emotion_trace_diagnostic.v1",
            "event_id": clean_text(event.get("event_id"), 96),
            "trace_id": clean_text(event.get("trace_id"), 96),
            "revision": int(event.get("revision") or 1),
            "producer_plugin": clean_text(event.get("producer_plugin"), 80),
            "origin_kind": clean_text(event.get("origin_kind"), 40),
            "event_type": clean_text(event.get("event_type"), 48),
            "intensity": float(event.get("intensity") or 0.0),
            "confidence": float(event.get("confidence") or 0.0),
            "status": clean_text(event.get("status"), 24),
            "source_rule": clean_text(event.get("source_rule"), 80),
            "occurred_at": clean_text(event.get("occurred_at"), 48),
            "applied_interaction": clean_text(event.get("applied_interaction"), 32),
            "applied_energy_delta": float(event.get("applied_energy_delta") or 0.0),
            "correction_of": clean_text(event.get("correction_of"), 96),
            "actor": cls._diagnostic_ref(event.get("actor_ref")),
            "target": cls._diagnostic_ref(event.get("target_ref")),
            "quoted_target": cls._diagnostic_ref(event.get("quoted_target_ref")),
            "scope": clean_text(event.get("scope"), 24),
            "session_hash": cls._diagnostic_hash(event.get("session_id")),
            "deliveries": [
                {
                    "consumer_id": clean_text(row["consumer_id"], 80),
                    "attempts": max(0, int(row["attempts"] or 0)),
                    "first_delivered_at": clean_text(row["first_delivered_at"], 48),
                    "last_delivered_at": clean_text(row["last_delivered_at"], 48),
                    "acked": bool(row["acked_at"]),
                    "acked_at": clean_text(row["acked_at"], 48),
                }
                for row in deliveries
            ],
        }

    @classmethod
    def _diagnostic_ref(cls, value: Any) -> dict[str, str]:
        source = value if isinstance(value, dict) else {}
        return {
            "kind": clean_text(source.get("kind"), 24),
            "role": clean_text(source.get("role"), 40),
            "id_hash": cls._diagnostic_hash(source.get("id")),
        }

    @staticmethod
    def _diagnostic_hash(value: Any) -> str:
        cleaned = clean_text(value, 220)
        return (
            hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:12]
            if cleaned
            else ""
        )

    async def list_emotion_events(
        self,
        *,
        bot_id: str = "",
        session_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._run_recoverable_database_operation(
            self._list_emotion_events_sync, bot_id, session_id, limit
        )

    def _list_emotion_events_sync(
        self, bot_id: str, session_id: str, limit: int
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if clean_text(bot_id, 160):
            clauses.append("bot_id=?")
            params.append(clean_text(bot_id, 160))
        if clean_text(session_id, 220):
            clauses.append("session_id=?")
            params.append(clean_text(session_id, 220))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(500, int(limit or 50))))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM emotion_events{where} ORDER BY occurred_at DESC, revision DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._emotion_event_row(row) for row in rows]
