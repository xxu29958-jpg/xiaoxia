from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from .models import (
    clean_text,
    new_id,
    utc_now,
)
from .store_support import (
    _ACL_UNSET,
)


class MemoryStoreAclPolicies:
    """store acl policies; the facade owns all shared resources."""

    async def list_acl_rules(
        self,
        *,
        owner_scope: str = "",
        owner_id: str = "",
        reader_scope: str = "",
        reader_id: str = "",
        effect: str = "",
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_acl_rules_sync,
            owner_scope,
            owner_id,
            reader_scope,
            reader_id,
            effect,
            enabled_only,
        )

    def _list_acl_rules_sync(
        self,
        owner_scope: str,
        owner_id: str,
        reader_scope: str,
        reader_id: str,
        effect: str,
        enabled_only: bool,
    ) -> list[dict[str, Any]]:
        where = "1=1"
        params: list[Any] = []
        if enabled_only:
            where += " AND enabled=1"
        if owner_scope:
            where += " AND owner_scope=?"
            params.append(clean_text(owner_scope, 40))
        if owner_id:
            where += " AND owner_id=?"
            params.append(clean_text(owner_id, 160))
        if reader_scope:
            where += " AND reader_scope=?"
            params.append(clean_text(reader_scope, 40))
        if reader_id:
            where += " AND reader_id=?"
            params.append(clean_text(reader_id, 160))
        if effect:
            where += " AND effect=?"
            params.append(self._normalize_acl_effect(effect))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM memory_acl_rules
                WHERE {where}
                ORDER BY updated_at DESC, created_at DESC
                """,
                params,
            ).fetchall()
        return [self._acl_rule_from_row(row) for row in rows]

    async def upsert_acl_rule(
        self,
        *,
        owner_scope: str,
        owner_id: str,
        reader_scope: str,
        reader_id: str,
        effect: str = "allow",
        enabled: bool = True,
        note: str = "",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._upsert_acl_rule_sync,
            owner_scope,
            owner_id,
            reader_scope,
            reader_id,
            effect,
            enabled,
            note,
        )

    def _upsert_acl_rule_sync(
        self,
        owner_scope: str,
        owner_id: str,
        reader_scope: str,
        reader_id: str,
        effect: str,
        enabled: bool,
        note: str,
        _commit: bool = True,
    ) -> dict[str, Any]:
        now = utc_now()
        owner_scope = clean_text(owner_scope, 40)
        owner_id = clean_text(owner_id, 160)
        reader_scope = clean_text(reader_scope, 40)
        reader_id = clean_text(reader_id, 160)
        effect = self._normalize_acl_effect(effect)
        data = {
            "id": new_id("acl"),
            "owner_scope": owner_scope,
            "owner_id": owner_id,
            "reader_scope": reader_scope,
            "reader_id": reader_id,
            "effect": effect,
            "enabled": 1 if enabled else 0,
            "note": clean_text(note, 300),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_acl_rules(
                    id, owner_scope, owner_id, reader_scope, reader_id, effect, enabled, note, created_at, updated_at
                )
                VALUES(:id, :owner_scope, :owner_id, :reader_scope, :reader_id, :effect, :enabled, :note, :created_at, :updated_at)
                ON CONFLICT(owner_scope, owner_id, reader_scope, reader_id) DO UPDATE SET
                    effect=excluded.effect,
                    enabled=excluded.enabled,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                data,
            )
            row = self._conn.execute(
                """
                SELECT * FROM memory_acl_rules
                WHERE owner_scope=? AND owner_id=? AND reader_scope=? AND reader_id=?
                """,
                (owner_scope, owner_id, reader_scope, reader_id),
            ).fetchone()
            if _commit:
                self._conn.commit()
        return self._acl_rule_from_row(row) if row else data

    async def delete_acl_rule(self, rule_id: str) -> bool:
        return await asyncio.to_thread(self._delete_acl_rule_sync, rule_id)

    def _delete_acl_rule_sync(self, rule_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memory_acl_rules WHERE id=?",
                (clean_text(rule_id, 120),),
            )
            self._conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def _acl_rule_from_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item.get("enabled"))
        item["effect"] = item.get("effect") or "allow"
        return item

    async def get_acl_policy(self, window_scope: str, window_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._get_acl_policy_sync, window_scope, window_id
        )

    def _get_acl_policy_sync(self, window_scope: str, window_id: str) -> dict[str, Any]:
        window_scope = clean_text(window_scope, 40)
        window_id = clean_text(window_id, 160)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_acl_policies WHERE window_scope=? AND window_id=?",
                (window_scope, window_id),
            ).fetchone()
        if not row:
            return self._default_acl_policy(window_scope, window_id)
        return self._acl_policy_from_row(row)

    async def list_acl_policies(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_acl_policies_sync)

    def _list_acl_policies_sync(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memory_acl_policies").fetchall()
        return [self._acl_policy_from_row(row) for row in rows]

    async def upsert_acl_policy(
        self,
        *,
        window_scope: str,
        window_id: str,
        read_mode: str = "",
        share_mode: str = "",
        capture_enabled: Any = _ACL_UNSET,
        recall_enabled: Any = _ACL_UNSET,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._upsert_acl_policy_sync,
            window_scope,
            window_id,
            read_mode,
            share_mode,
            capture_enabled,
            recall_enabled,
        )

    def _upsert_acl_policy_sync(
        self,
        window_scope: str,
        window_id: str,
        read_mode: str,
        share_mode: str,
        capture_enabled: Any = _ACL_UNSET,
        recall_enabled: Any = _ACL_UNSET,
        _commit: bool = True,
    ) -> dict[str, Any]:
        window_scope = clean_text(window_scope, 40)
        window_id = clean_text(window_id, 160)
        current = self._get_acl_policy_sync(window_scope, window_id)
        read_mode = self._normalize_acl_mode(read_mode or current.get("read_mode"))
        share_mode = self._normalize_acl_mode(share_mode or current.get("share_mode"))
        next_capture = (
            current.get("capture_enabled")
            if capture_enabled is _ACL_UNSET
            else self._normalize_acl_feature_override(capture_enabled)
        )
        next_recall = (
            current.get("recall_enabled")
            if recall_enabled is _ACL_UNSET
            else self._normalize_acl_feature_override(recall_enabled)
        )
        now = utc_now()
        data = {
            "id": current.get("id") or new_id("acl_policy"),
            "window_scope": window_scope,
            "window_id": window_id,
            "read_mode": read_mode,
            "share_mode": share_mode,
            "capture_enabled": next_capture,
            "recall_enabled": next_recall,
            "created_at": current.get("created_at") or now,
            "updated_at": now,
        }
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_acl_policies(
                    id, window_scope, window_id, read_mode, share_mode,
                    capture_enabled, recall_enabled, created_at, updated_at
                )
                VALUES(:id, :window_scope, :window_id, :read_mode, :share_mode,
                       :capture_enabled, :recall_enabled, :created_at, :updated_at)
                ON CONFLICT(window_scope, window_id) DO UPDATE SET
                    read_mode=excluded.read_mode,
                    share_mode=excluded.share_mode,
                    capture_enabled=excluded.capture_enabled,
                    recall_enabled=excluded.recall_enabled,
                    updated_at=excluded.updated_at
                """,
                data,
            )
            row = self._conn.execute(
                "SELECT * FROM memory_acl_policies WHERE window_scope=? AND window_id=?",
                (window_scope, window_id),
            ).fetchone()
            if _commit:
                self._conn.commit()
                # Only a committed row may become the in-process authority; the
                # transactional branch is staged until its writer commits.
                self._publish_acl_projection_sync(
                    (window_scope, window_id),
                    (next_capture, next_recall),
                )
            else:
                self._stage_acl_projection_sync(
                    (window_scope, window_id),
                    (next_capture, next_recall),
                )
        return self._acl_policy_from_row(row) if row else data

    @staticmethod
    def _default_acl_policy(window_scope: str, window_id: str) -> dict[str, Any]:
        default_mode = (
            "blacklist" if clean_text(window_scope, 40) == "group" else "whitelist"
        )
        return {
            "id": "",
            "window_scope": window_scope,
            "window_id": window_id,
            "read_mode": default_mode,
            "share_mode": default_mode,
            "capture_enabled": None,
            "recall_enabled": None,
            "created_at": "",
            "updated_at": "",
        }

    @classmethod
    def _acl_policy_from_row(cls, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["read_mode"] = cls._normalize_acl_mode(item.get("read_mode"))
        item["share_mode"] = cls._normalize_acl_mode(item.get("share_mode"))
        for key in ("capture_enabled", "recall_enabled"):
            value = item.get(key)
            item[key] = None if value is None else bool(value)
        return item

    @staticmethod
    def _normalize_acl_feature_override(value: Any) -> bool | None:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
        return bool(value)

    def get_scope_feature_override_sync(
        self, scope: str, window_id: str
    ) -> dict[str, bool | None]:
        """Return the window override from the in-process ACL projection.

        Synchronous callers (capture, recall and topology checks) run on the
        event loop thread, so this must stay free of SQL I/O and of the store
        write lock.  The projection is loaded once at startup, republished by
        the write path after its commit, and reconciled in the background, so
        a missing entry means the window genuinely has no stored policy.
        """
        scope = clean_text(scope, 40)
        window_id = clean_text(window_id, 160)
        if scope not in {"private", "group"} or not window_id:
            return {"capture_enabled": None, "recall_enabled": None}
        cached = self._acl_feature_override_cache.get((scope, window_id))
        if cached is None:
            return {"capture_enabled": None, "recall_enabled": None}
        capture_enabled, recall_enabled = cached
        return {
            "capture_enabled": capture_enabled,
            "recall_enabled": recall_enabled,
        }

    @staticmethod
    def _normalize_acl_effect(effect: Any) -> str:
        return (
            "deny"
            if clean_text(effect, 20).lower() in {"deny", "block", "blacklist"}
            else "allow"
        )

    @staticmethod
    def _normalize_acl_mode(mode: Any) -> str:
        return (
            "blacklist"
            if clean_text(mode, 20).lower() in {"blacklist", "deny", "block"}
            else "whitelist"
        )

    def _load_acl_projection_rows_sync(
        self,
    ) -> dict[tuple[str, str], tuple[bool | None, bool | None]]:
        """Read the whole window policy table; callers must hold ``self._lock``."""
        try:
            rows = self._conn.execute(
                "SELECT window_scope, window_id, capture_enabled, recall_enabled "
                "FROM memory_acl_policies"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "no such table" not in message and "no such column" not in message:
                raise
            return {}
        projection: dict[tuple[str, str], tuple[bool | None, bool | None]] = {}
        for row in rows:
            scope = clean_text(row["window_scope"], 40)
            window_id = clean_text(row["window_id"], 160)
            if scope not in {"private", "group"} or not window_id:
                continue
            projection[(scope, window_id)] = (
                None
                if row["capture_enabled"] is None
                else bool(row["capture_enabled"]),
                None if row["recall_enabled"] is None else bool(row["recall_enabled"]),
            )
        return projection

    def rebuild_acl_projection_sync(self) -> int:
        """Reload the authoritative window ACL projection from the database."""
        with self._lock:
            projection = self._load_acl_projection_rows_sync()
        with self._acl_projection_lock:
            self._acl_feature_override_cache = projection
        return len(projection)

    def clear_acl_projection_sync(self) -> None:
        """Drop the projection after the policy table itself was emptied."""
        with self._acl_projection_lock:
            self._acl_feature_override_cache = {}
            self._acl_projection_pending = {}

    def _publish_acl_projection_sync(
        self,
        key: tuple[str, str],
        values: tuple[bool | None, bool | None],
    ) -> None:
        """Publish an override whose write transaction already committed."""
        with self._acl_projection_lock:
            self._acl_projection_pending.pop(key, None)
        self._acl_feature_override_cache[key] = values

    def _stage_acl_projection_sync(
        self,
        key: tuple[str, str],
        values: tuple[bool | None, bool | None],
    ) -> None:
        """Hold an override until the transaction that wrote it commits."""
        with self._acl_projection_lock:
            self._acl_projection_pending[key] = values

    def _flush_acl_projection_pending_sync(self) -> None:
        with self._acl_projection_lock:
            if not self._acl_projection_pending:
                return
            pending = self._acl_projection_pending
            self._acl_projection_pending = {}
        for key, values in pending.items():
            self._acl_feature_override_cache[key] = values

    def _discard_acl_projection_pending_sync(self) -> None:
        with self._acl_projection_lock:
            self._acl_projection_pending.clear()

    def _restore_acl_projection_pending_sync(
        self,
        snapshot: dict[tuple[str, str], tuple[bool | None, bool | None]],
    ) -> None:
        with self._acl_projection_lock:
            self._acl_projection_pending.clear()
            self._acl_projection_pending.update(snapshot)

    def reconcile_caches_sync(self, *, force: bool = False) -> dict[str, Any]:
        """Rebuild projections and broadcast invalidation when data changed.

        Runs in a worker thread: comparing the stored revision needs the store
        lock, which must never be taken from the event loop thread.
        """
        revision = self._memory_revision_sync()
        if not force and revision == self._acl_projection_revision:
            return {
                "revision": revision,
                "changed": False,
                "projection_entries": len(self._acl_feature_override_cache),
                "caches": [],
            }
        projection_entries = self.rebuild_acl_projection_sync()
        cleared = self.invalidate_registered_caches(reason="retrieval_revision")
        self.haystack_cache.clear()
        self._acl_projection_revision = revision
        return {
            "revision": revision,
            "changed": True,
            "projection_entries": projection_entries,
            "caches": cleared,
        }
