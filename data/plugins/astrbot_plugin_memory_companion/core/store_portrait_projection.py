from __future__ import annotations

import asyncio
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .portrait_namespace import portrait_scope_persona


class MemoryStorePortraitProjection:
    """store portrait projection; the facade owns all shared resources."""

    async def upsert_portrait_person_projection(
        self,
        person_ref: dict[str, Any],
        capability_summary: dict[str, Any],
        *,
        source_scope: str = "",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._upsert_portrait_person_projection_sync,
            deepcopy(person_ref),
            deepcopy(capability_summary),
            source_scope,
        )

    def _upsert_portrait_person_projection_sync(
        self,
        person_ref: dict[str, Any],
        capability_summary: dict[str, Any],
        source_scope: str,
    ) -> dict[str, Any]:
        person_id = clean_text(person_ref.get("person_id"), 80)
        identity_key = clean_text(person_ref.get("resolved_identity_key"), 96)
        revision = int(person_ref.get("projection_revision") or 0)
        assurance = clean_text(person_ref.get("identity_assurance"), 40)
        status = clean_text(person_ref.get("profile_status"), 40)
        source_scope = clean_text(source_scope, 80)
        if (
            not person_id
            or not identity_key
            or revision < 1
            or assurance
            not in {"unverified", "observed", "verified", "explicit_linked"}
            or status not in {"active", "suspended", "quarantined", "deleted"}
        ):
            return {"ok": False, "code": "bridge_person_mismatch", "state": "invalid"}
        now = utc_now()
        with self._lock:
            previous = self._conn.execute(
                "SELECT * FROM portrait_people WHERE person_id=?", (person_id,)
            ).fetchone()
            if previous is not None:
                old_revision = int(previous["projection_revision"] or 0)
                if old_revision > revision:
                    return {
                        "ok": False,
                        "code": "bridge_stale_revision",
                        "state": "stale",
                        "projection_revision": old_revision,
                    }
                if (
                    old_revision == revision
                    and previous["resolved_identity_key"] != identity_key
                ):
                    return {
                        "ok": False,
                        "code": "bridge_person_mismatch",
                        "state": "invalid",
                    }
            portrait_revision = (
                int(previous["portrait_revision"] or 0) if previous is not None else 0
            )
            durable_capability_summary = capability_summary
            if (
                previous is not None
                and source_scope
                and not (
                    source_scope == "private" or source_scope.startswith("private@")
                )
            ):
                durable_capability_summary = json_loads(
                    previous["capability_summary"], {}
                )
            self._conn.execute(
                """
                INSERT INTO portrait_people(
                    person_id, resolved_identity_key, projection_revision, identity_assurance,
                    profile_status, capability_summary, portrait_revision, last_synced_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(person_id) DO UPDATE SET
                    resolved_identity_key=excluded.resolved_identity_key,
                    projection_revision=excluded.projection_revision,
                    identity_assurance=excluded.identity_assurance,
                    profile_status=excluded.profile_status,
                    capability_summary=excluded.capability_summary,
                    last_synced_at=excluded.last_synced_at,
                    updated_at=excluded.updated_at
                """,
                (
                    person_id,
                    identity_key,
                    revision,
                    clean_text(person_ref.get("identity_assurance"), 40),
                    clean_text(person_ref.get("profile_status"), 40),
                    json_dumps(durable_capability_summary),
                    portrait_revision,
                    now,
                    now,
                ),
            )
            if source_scope:
                self._conn.execute(
                    """
                    INSERT INTO portrait_scope_capabilities(
                        person_id, source_scope, capability_summary, projection_revision, updated_at
                    ) VALUES(?,?,?,?,?)
                    ON CONFLICT(person_id, source_scope) DO UPDATE SET
                        capability_summary=excluded.capability_summary,
                        projection_revision=excluded.projection_revision,
                        updated_at=excluded.updated_at
                    """,
                    (
                        person_id,
                        source_scope,
                        json_dumps(capability_summary),
                        revision,
                        now,
                    ),
                )
            self._conn.commit()
        return {
            "ok": True,
            "code": "profile_exact",
            "state": "ready",
            "portrait_revision": portrait_revision,
        }

    async def portrait_projection_decision(
        self, person_ref: dict[str, Any]
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._portrait_projection_decision_sync, deepcopy(person_ref)
        )

    def _portrait_projection_decision_sync(
        self, person_ref: dict[str, Any]
    ) -> dict[str, Any]:
        person_id = clean_text(person_ref.get("person_id"), 80)
        identity_key = clean_text(person_ref.get("resolved_identity_key"), 96)
        revision = int(person_ref.get("projection_revision") or 0)
        assurance = clean_text(person_ref.get("identity_assurance"), 40)
        if (
            not person_id
            or not identity_key
            or revision < 1
            or assurance not in {"observed", "verified", "explicit_linked"}
        ):
            return {"ok": False, "code": "bridge_person_mismatch"}
        with self._lock:
            row = self._conn.execute(
                "SELECT resolved_identity_key, projection_revision, identity_assurance, profile_status FROM portrait_people WHERE person_id=?",
                (person_id,),
            ).fetchone()
        if row is None:
            return {"ok": False, "code": "bridge_unavailable"}
        if clean_text(row["resolved_identity_key"], 96) != identity_key:
            return {"ok": False, "code": "bridge_person_mismatch"}
        if int(row["projection_revision"] or 0) != revision:
            return {"ok": False, "code": "bridge_stale_revision"}
        if clean_text(row["identity_assurance"], 40) not in {
            "observed",
            "verified",
            "explicit_linked",
        }:
            return {"ok": False, "code": "bridge_person_mismatch"}
        if clean_text(row["profile_status"], 40) != "active":
            return {"ok": False, "code": "bridge_person_mismatch"}
        return {"ok": True, "code": "profile_exact"}

    def _portrait_suppressed_sync(
        self, person_id: str, dimension: str, claim_hash: str, scope: str
    ) -> bool:
        now = utc_now()
        row = self._conn.execute(
            """
            SELECT 1 FROM portrait_suppressions
            WHERE person_id=? AND dimension=? AND normalized_claim_hash=?
              AND status IN ('active', 'reconfirmation_pending')
              AND (scope='' OR scope=?)
              AND (expires_at='' OR expires_at>?)
            LIMIT 1
            """,
            (person_id, dimension, claim_hash, scope, now),
        ).fetchone()
        return row is not None

    @staticmethod
    def _portrait_timestamp_is_fresh(value: Any, freshness_days: int) -> bool:
        """Treat malformed or stale inferred timestamps as unusable."""
        text = clean_text(value, 80)
        if not text:
            return False
        try:
            observed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return False
        age = datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
        return age.total_seconds() <= max(1, freshness_days) * 86400

    @staticmethod
    def _portrait_scope_allows_row(
        row: sqlite3.Row,
        requested_scope: str,
        legacy_scope: str = "",
    ) -> bool:
        usable_scope = clean_text(row["usable_scope"], 80)
        source_scope = clean_text(row["source_scope"], 80)
        if usable_scope == "self_low_global":
            source_persona = portrait_scope_persona(source_scope)
            requested_persona = portrait_scope_persona(requested_scope)
            return not source_persona or (
                bool(requested_persona) and source_persona == requested_persona
            )
        return (
            usable_scope == "source_only"
            and bool(requested_scope)
            and source_scope
            in {
                requested_scope,
                legacy_scope,
            }
        )

    def _portrait_scope_capability_sync(
        self,
        person_id: str,
        source_scope: str,
        *,
        legacy_scope: str = "",
    ) -> dict[str, Any]:
        source_scope = clean_text(source_scope, 80)
        row = self._conn.execute(
            "SELECT capability_summary FROM portrait_scope_capabilities WHERE person_id=? AND source_scope=?",
            (person_id, source_scope),
        ).fetchone()
        if row is not None:
            value = json_loads(row["capability_summary"], {})
            return value if isinstance(value, dict) else {}
        if source_scope == "private" or source_scope.startswith("group:"):
            person = self._conn.execute(
                "SELECT capability_summary FROM portrait_people WHERE person_id=?",
                (person_id,),
            ).fetchone()
            value = (
                json_loads(person["capability_summary"], {})
                if person is not None
                else {}
            )
            return value if isinstance(value, dict) else {}
        return {}

    def _bump_portrait_revision_sync(self, person_id: str) -> int:
        self._conn.execute(
            "UPDATE portrait_people SET portrait_revision=portrait_revision+1, updated_at=? WHERE person_id=?",
            (utc_now(), person_id),
        )
        row = self._conn.execute(
            "SELECT portrait_revision FROM portrait_people WHERE person_id=?",
            (person_id,),
        ).fetchone()
        return int(row["portrait_revision"] or 0) if row is not None else 0
