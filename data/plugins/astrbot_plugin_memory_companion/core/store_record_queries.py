from __future__ import annotations

import asyncio
import re
from typing import Any

from .identity import parse_scope_from_session
from .memory_atom import (
    VALIDITY_STATUSES,
    validity_where_clause,
)
from .models import (
    MemoryRecord,
    clean_text,
    utc_now,
)
from .store_support import (
    _memory_time_sql,
)


class MemoryStoreRecordQueries:
    """store record queries; the facade owns all shared resources."""

    async def recent_memories(
        self, limit: int = 10, include_pending: bool = True
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._recent_memories_sync, limit, include_pending
        )

    def _recent_memories_sync(
        self, limit: int, include_pending: bool
    ) -> list[MemoryRecord]:
        where = self._recallable_memory_sql()
        if not include_pending:
            where += " AND review_status != 'pending'"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    async def list_memories(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_pending: bool = True,
        query: str = "",
        memory_type: str = "",
        scope: str = "",
        visibility: str = "",
        review_status: str = "",
        lifecycle: str = "",
        session_id: str = "",
        group_id: str = "",
        entity_id: str = "",
        memory_types: list[str] | tuple[str, ...] | None = None,
        lifecycle_values: list[str] | tuple[str, ...] | None = None,
        visibility_values: list[str] | tuple[str, ...] | None = None,
        source_plugin_exclude: str = "",
    ) -> list[MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._list_memories_sync,
            limit,
            offset,
            include_pending,
            query,
            memory_type,
            scope,
            visibility,
            review_status,
            lifecycle,
            session_id,
            group_id,
            entity_id,
            memory_types,
            lifecycle_values,
            visibility_values,
            source_plugin_exclude,
        )

    def _list_memories_sync(
        self,
        limit: int,
        offset: int,
        include_pending: bool,
        query: str,
        memory_type: str,
        scope: str,
        visibility: str,
        review_status: str,
        lifecycle: str,
        session_id: str,
        group_id: str,
        entity_id: str,
        memory_types: list[str] | tuple[str, ...] | None,
        lifecycle_values: list[str] | tuple[str, ...] | None,
        visibility_values: list[str] | tuple[str, ...] | None,
        source_plugin_exclude: str,
    ) -> list[MemoryRecord]:
        params: list[Any] = []
        where = "1=1"
        if not include_pending:
            where += " AND review_status != 'pending'"
        if query:
            like = self._like_pattern(query)
            where += (
                " AND (id LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR evidence LIKE ? ESCAPE '\\' OR subject_id LIKE ? ESCAPE '\\'"
                " OR subject_name LIKE ? ESCAPE '\\' OR object_id LIKE ? ESCAPE '\\' OR object_name LIKE ? ESCAPE '\\'"
                " OR session_id LIKE ? ESCAPE '\\' OR group_id LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 9)
        requested_types = [
            clean_text(value, 80)
            for value in (memory_types or ())
            if clean_text(value, 80)
        ]
        if requested_types:
            marks = ",".join("?" for _ in requested_types)
            where += f" AND memory_type IN ({marks})"
            params.extend(requested_types)
        elif memory_type:
            where += " AND memory_type=?"
            params.append(clean_text(memory_type, 80))
        if scope:
            where += " AND scope=?"
            params.append(clean_text(scope, 40))
        requested_visibilities = [
            clean_text(value, 40)
            for value in (visibility_values or ())
            if clean_text(value, 40)
        ]
        if requested_visibilities:
            marks = ",".join("?" for _ in requested_visibilities)
            where += f" AND visibility IN ({marks})"
            params.extend(requested_visibilities)
        elif visibility:
            where += " AND visibility=?"
            params.append(clean_text(visibility, 40))
        if source_plugin_exclude:
            where += " AND source_plugin != ?"
            params.append(clean_text(source_plugin_exclude, 120))
        if review_status:
            where += " AND review_status=?"
            params.append(clean_text(review_status, 40))
        requested_lifecycles = [
            clean_text(value, 40)
            for value in (lifecycle_values or ())
            if clean_text(value, 40)
        ]
        if requested_lifecycles:
            marks = ",".join("?" for _ in requested_lifecycles)
            where += f" AND lifecycle IN ({marks})"
            params.extend(requested_lifecycles)
        elif lifecycle:
            where += " AND lifecycle=?"
            params.append(clean_text(lifecycle, 40))
        if session_id:
            where += " AND session_id=?"
            params.append(clean_text(session_id, 200))
        if group_id:
            where += " AND group_id=?"
            params.append(clean_text(group_id, 120))
        if entity_id:
            entity = clean_text(entity_id, 120)
            where += " AND (subject_id=? OR object_id=? OR group_id=? OR session_id LIKE ? ESCAPE '\\')"
            params.extend([entity, entity, entity, self._like_pattern(entity)])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM memories
                WHERE {where}
                ORDER BY {_memory_time_sql()} DESC, created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                params + [max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    async def list_memories_by_validity(
        self,
        *,
        validity_statuses: list[str] | tuple[str, ...] | None = None,
        valid_at: str = "",
        owner_bot_id: str = "",
        platform: str = "",
        scope: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """Read a bounded Memory Atom set using reusable validity-time rules."""

        return await self._run_recoverable_database_operation(
            self._list_memories_by_validity_sync,
            validity_statuses,
            valid_at,
            owner_bot_id,
            platform,
            scope,
            limit,
            offset,
        )

    def _list_memories_by_validity_sync(
        self,
        validity_statuses: list[str] | tuple[str, ...] | None,
        valid_at: str,
        owner_bot_id: str,
        platform: str,
        scope: str,
        limit: int,
        offset: int,
    ) -> list[MemoryRecord]:
        statuses = [
            clean_text(value, 24).lower()
            for value in (validity_statuses or ["active"])
            if clean_text(value, 24).lower() in VALIDITY_STATUSES
        ]
        at = clean_text(valid_at, 80) or utc_now()
        validity_where, params = validity_where_clause(statuses=statuses, valid_at=at)
        where = [validity_where]
        owner = clean_text(owner_bot_id, 120)
        platform_value = clean_text(platform, 80)
        scope_value = clean_text(scope, 40)
        if owner:
            where.append("owner_bot_id=?")
            params.append(owner)
        if platform_value:
            where.append("platform=?")
            params.append(platform_value)
        if scope_value:
            where.append("scope=?")
            params.append(scope_value)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM memories
                WHERE {" AND ".join(where)}
                ORDER BY salience DESC, importance DESC,
                         {_memory_time_sql(include_valid_from=True)} DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, int(limit or 1)), max(0, int(offset or 0))],
            ).fetchall()
        return [MemoryRecord.from_row(row) for row in rows]

    async def read_user_memory_summary_records(
        self,
        user_id: str,
        *,
        session_id: str = "",
        limit: int = 8,
    ) -> dict[str, Any]:
        """Read a bounded exact-identity private-memory set for a bridge DTO.

        This is deliberately not a general search: it accepts no display name,
        has no cross-window expansion, and never reads group rows. A supplied
        session must itself resolve to the same private user identity.
        """

        return await self._run_recoverable_database_operation(
            self._read_user_memory_summary_records_sync,
            user_id,
            session_id,
            limit,
        )

    def _read_user_memory_summary_records_sync(
        self,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> dict[str, Any]:
        identity = clean_text(user_id, 120)
        requested_session = clean_text(session_id, 200)
        empty = {"records": [], "total": 0, "type_counts": {}}
        if not identity:
            return empty
        if requested_session:
            scope, target_id = parse_scope_from_session(requested_session)
            if scope != "private" or clean_text(target_id, 120) != identity:
                return empty

        try:
            safe_limit = max(1, min(12, int(limit or 8)))
        except (TypeError, ValueError, OverflowError):
            safe_limit = 8
        session_filters = [
            "%:friendmessage:" + self._escape_like_suffix(identity),
            "%:privatemessage:" + self._escape_like_suffix(identity),
            "%:friend:" + self._escape_like_suffix(identity),
            "%:private:" + self._escape_like_suffix(identity),
        ]
        identity_clause = " OR ".join(
            [
                "subject_id=?",
                "object_id=?",
                *("LOWER(session_id) LIKE ? ESCAPE '\\'" for _ in session_filters),
            ]
        )
        params: list[Any] = [identity, identity, *session_filters]
        session_clause = ""
        if requested_session:
            session_clause = " AND session_id=?"
            params.append(requested_session)
        validity_clause, validity_params = validity_where_clause(valid_at=utc_now())
        params.extend(validity_params)
        where = (
            "scope='private' AND review_status!='pending' "
            "AND visibility IN ('private_pair', 'shareable') "
            f"AND ({identity_clause}){session_clause} "
            f"AND lifecycle!='archived' AND {validity_clause}"
        )
        with self._lock:
            revision = int(self._memory_revision_sync())
            rows = self._conn.execute(
                f"""
                SELECT * FROM memories
                WHERE {where}
                ORDER BY {_memory_time_sql()} DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                [*params, safe_limit],
            ).fetchall()
            total_row = self._conn.execute(
                f"SELECT COUNT(*) AS count FROM memories WHERE {where}",
                params,
            ).fetchone()
            type_rows = self._conn.execute(
                f"SELECT memory_type, COUNT(*) AS count FROM memories WHERE {where} GROUP BY memory_type",
                params,
            ).fetchall()
            # Other MemoryStore instances may write through another connection.
            # Do not pair old rows with a newer revision from a later read.
            if int(self._memory_revision_sync()) != revision:
                raise RuntimeError("memory_summary_snapshot_changed")

        records: list[MemoryRecord] = []
        for row in rows:
            record = MemoryRecord.from_row(row)
            parsed_scope, parsed_target = parse_scope_from_session(
                clean_text(record.session_id, 200)
            )
            direct_identity = identity in {
                clean_text(record.subject.id, 120),
                clean_text(record.object.id, 120),
            }
            session_identity = (
                parsed_scope == "private" and clean_text(parsed_target, 120) == identity
            )
            if requested_session and record.session_id != requested_session:
                continue
            if direct_identity or session_identity:
                records.append(record)
        type_counts = {
            clean_text(row["memory_type"], 80): max(0, int(row["count"] or 0))
            for row in type_rows
            if clean_text(row["memory_type"], 80)
        }
        return {
            "records": records,
            "total": max(0, int(total_row["count"] or 0)) if total_row else 0,
            "type_counts": type_counts,
            "memory_revision": revision,
        }

    @staticmethod
    def _escape_like_suffix(value: str) -> str:
        return (
            clean_text(value, 120)
            .lower()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

    async def get_memory(self, memory_id: str) -> MemoryRecord | None:
        return await self._run_recoverable_database_operation(
            self._get_memory_sync, memory_id
        )

    def _get_memory_sync(self, memory_id: str) -> MemoryRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
        return MemoryRecord.from_row(row) if row else None

    async def get_memories_by_ids(
        self, memory_ids: list[str]
    ) -> dict[str, MemoryRecord]:
        return await self._run_recoverable_database_operation(
            self._get_memories_by_ids_sync, memory_ids
        )

    def _get_memories_by_ids_sync(
        self, memory_ids: list[str]
    ) -> dict[str, MemoryRecord]:
        ids = [clean_text(mid, 120) for mid in memory_ids if clean_text(mid, 120)]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return {row["id"]: MemoryRecord.from_row(row) for row in rows}

    async def list_memory_buckets(
        self,
        limit: int | None = 160,
        *,
        include_raw_events: bool = False,
        include_archive: bool = False,
    ) -> list[dict[str, Any]]:
        """Aggregate navigation buckets over recallable memories.

        ``include_archive=False`` (the default) reads the
        ``recallable_memories`` view, so storage-only ``bot_personal_bridge``
        archive rows can never surface as ordinary private/group windows.
        Callers that need the archive rows themselves (permission topology --
        an admin has to be able to grant access to a window whose only rows are
        archived) opt in explicitly.  Import targets deliberately do not: a
        ``bot_personal:<digest>`` row is an internal synthetic session, not a
        chat a user can import memories into.
        """
        return await asyncio.to_thread(
            self._list_memory_buckets_sync,
            limit,
            include_raw_events,
            include_archive,
        )

    async def preferred_private_session_id(self, user_id: str, bot_id: str = "") -> str:
        return await asyncio.to_thread(
            self._preferred_private_session_id_sync, user_id, bot_id
        )

    def _preferred_private_session_id_sync(self, user_id: str, bot_id: str = "") -> str:
        user_id = clean_text(user_id, 120)
        bot_id = clean_text(bot_id, 120)
        if not user_id:
            return ""
        bot_clause = ""
        params: list[Any] = [user_id, user_id, self._like_pattern(user_id)]
        if bot_id:
            bot_clause = """
                  AND (
                    (subject_kind='bot' AND subject_id=?)
                    OR (object_kind='bot' AND object_id=?)
                    OR CASE
                        WHEN json_valid(metadata)
                        THEN COALESCE(CAST(json_extract(metadata, '$.owner_bot_id') AS TEXT), '')
                        ELSE ''
                       END=?
                  )
            """
            params.extend([bot_id, bot_id, bot_id])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    session_id,
                    SUM(CASE WHEN COALESCE(import_batch_id, '')='' THEN 1 ELSE 0 END) AS native_count,
                    COUNT(*) AS total_count,
                    MAX({_memory_time_sql()}) AS latest_at
                FROM memories
                WHERE scope='private' AND session_id!=''
                  AND (subject_id=? OR object_id=? OR session_id LIKE ? ESCAPE '\\')
                  {bot_clause}
                GROUP BY session_id
                ORDER BY native_count DESC, total_count DESC, latest_at DESC
                LIMIT 64
                """,
                params,
            ).fetchall()
        for row in rows:
            parsed_scope, parsed_target = parse_scope_from_session(
                clean_text(row["session_id"], 200)
            )
            if parsed_scope == "private" and clean_text(parsed_target, 120) == user_id:
                return clean_text(row["session_id"], 200)
        return ""

    def _list_memory_buckets_sync(
        self,
        limit: int | None,
        include_raw_events: bool = False,
        include_archive: bool = False,
    ) -> list[dict[str, Any]]:
        source = "memories" if include_archive else self.RECALLABLE_MEMORY_VIEW
        with self._lock:
            rows = self._conn.execute(
                f"""
                WITH normalized AS (
                    SELECT
                        scope,
                        CASE
                            WHEN scope='group' THEN
                                CASE
                                    WHEN group_id!='' THEN group_id
                                    WHEN object_kind='group' AND object_id!='' THEN object_id
                                    ELSE session_id
                                END
                            ELSE
                                CASE
                                    WHEN subject_kind='user' AND subject_id!='' AND subject_id!='self' THEN subject_id
                                    WHEN object_kind='user' AND object_id!='' AND object_id!='self' THEN object_id
                                    ELSE session_id
                                END
                        END AS target_id,
                        CASE
                            WHEN scope='group' THEN
                                CASE
                                    WHEN object_kind='group' AND object_name!='' THEN object_name
                                    ELSE ''
                                END
                            ELSE
                                CASE
                                    WHEN subject_kind='user' AND subject_id!='' AND subject_id!='self' THEN subject_name
                                    WHEN object_kind='user' AND object_id!='' AND object_id!='self' THEN object_name
                                    ELSE ''
                                END
                        END AS target_name,
                        session_id AS sample_session_id,
                        group_id AS sample_group_id,
                        CASE
                            WHEN json_valid(metadata)
                                 AND COALESCE(CAST(json_extract(metadata, '$.owner_bot_id') AS TEXT), '') NOT IN ('', 'self')
                            THEN CAST(json_extract(metadata, '$.owner_bot_id') AS TEXT)
                            WHEN subject_kind='bot' AND subject_id NOT IN ('', 'self') THEN subject_id
                            WHEN object_kind='bot' AND object_id NOT IN ('', 'self') THEN object_id
                            ELSE ''
                        END AS sample_bot_id,
                        review_status,
                        lifecycle,
                        visibility,
                        CASE
                            WHEN lifecycle!='archived'
                                 AND visibility!='internal'
                                 AND (?=1 OR lifecycle!='raw_event')
                            THEN 1
                            ELSE 0
                        END AS is_searchable,
                        occurred_at,
                        {_memory_time_sql()} AS sort_time
                    FROM {source}
                    WHERE scope IN ('private', 'group')
                      AND review_status!='pending'
                ), ranked AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY scope, target_id, sample_bot_id
                            ORDER BY
                                CASE
                                    WHEN is_searchable=1 THEN 0
                                    ELSE 1
                                END ASC,
                                sort_time DESC,
                                sample_session_id DESC
                        ) AS sample_rank,
                        ROW_NUMBER() OVER (
                            PARTITION BY scope, target_id, sample_bot_id
                            ORDER BY
                                CASE WHEN target_name!='' THEN 0 ELSE 1 END ASC,
                                sort_time DESC,
                                sample_session_id DESC
                        ) AS name_rank,
                        ROW_NUMBER() OVER (
                            PARTITION BY scope, target_id, sample_bot_id
                            ORDER BY sort_time DESC, sample_session_id DESC
                        ) AS latest_rank
                    FROM normalized
                    WHERE target_id!=''
                )
                SELECT
                    scope,
                    target_id,
                    MAX(CASE WHEN name_rank=1 THEN target_name ELSE '' END) AS target_name,
                    MAX(CASE WHEN name_rank=1 THEN occurred_at ELSE '' END) AS name_latest_at,
                    MAX(CASE WHEN name_rank=1 THEN sort_time ELSE 0 END) AS name_latest_sort,
                    MAX(CASE WHEN sample_rank=1 THEN sample_session_id ELSE '' END) AS sample_session_id,
                    MAX(CASE WHEN sample_rank=1 THEN sample_group_id ELSE '' END) AS sample_group_id,
                    sample_bot_id,
                    COUNT(*) AS memory_count,
                    SUM(CASE WHEN lifecycle='archived' THEN 1 ELSE 0 END) AS archived_count,
                    SUM(
                        is_searchable
                    ) AS searchable_count,
                    MAX(CASE WHEN sample_rank=1 AND is_searchable=1 THEN occurred_at ELSE '' END) AS active_latest_at,
                    MAX(CASE WHEN is_searchable=1 THEN sort_time ELSE 0 END) AS active_latest_sort,
                    MAX(CASE WHEN latest_rank=1 THEN occurred_at ELSE '' END) AS latest_at,
                    MAX(sort_time) AS latest_sort
                FROM ranked
                GROUP BY scope, target_id, sample_bot_id
                ORDER BY
                    scope ASC,
                    CASE WHEN MAX(is_searchable)>0 THEN 0 ELSE 1 END ASC,
                    MAX(CASE WHEN is_searchable=1 THEN sort_time ELSE 0 END) DESC,
                    MAX(sort_time) DESC
                """,
                (1 if include_raw_events else 0,),
            ).fetchall()
            merged: dict[tuple[str, str], dict[str, Any]] = {}
            for raw in rows:
                bucket = dict(raw)
                scope = clean_text(bucket.get("scope"), 40)
                target_id = clean_text(bucket.get("target_id"), 200)
                parsed_scope, parsed_target = parse_scope_from_session(target_id)
                if parsed_scope == scope and clean_text(parsed_target, 120):
                    target_id = clean_text(parsed_target, 120)
                key = (scope, target_id)
                sample_context = {
                    "bot_id": clean_text(bucket.get("sample_bot_id"), 120),
                    "session_id": clean_text(bucket.get("sample_session_id"), 200),
                    "group_id": clean_text(bucket.get("sample_group_id"), 120),
                    "memory_count": int(bucket.get("memory_count") or 0),
                    "archived_count": int(bucket.get("archived_count") or 0),
                    "searchable_count": int(bucket.get("searchable_count") or 0),
                    "active_latest_at": clean_text(bucket.get("active_latest_at"), 80),
                    "latest_at": clean_text(bucket.get("latest_at"), 80),
                    "active_latest_sort": float(bucket.get("active_latest_sort") or 0),
                    "latest_sort": float(bucket.get("latest_sort") or 0),
                }
                candidate_priority = (
                    bool(sample_context["active_latest_at"]),
                    sample_context["active_latest_sort"]
                    or sample_context["latest_sort"],
                )
                current = merged.get(key)
                if current is None:
                    bucket["target_id"] = target_id
                    bucket["sample_contexts"] = [sample_context]
                    bucket["_sample_has_active"] = candidate_priority[0]
                    bucket["_sample_latest_sort"] = candidate_priority[1]
                    bucket["_name_latest_at"] = clean_text(
                        bucket.get("name_latest_at"), 80
                    )
                    bucket["_name_latest_sort"] = float(
                        bucket.get("name_latest_sort") or 0
                    )
                    merged[key] = bucket
                    continue
                current["memory_count"] = int(current.get("memory_count") or 0) + int(
                    bucket.get("memory_count") or 0
                )
                current["archived_count"] = int(
                    current.get("archived_count") or 0
                ) + int(bucket.get("archived_count") or 0)
                current["searchable_count"] = int(
                    current.get("searchable_count") or 0
                ) + int(bucket.get("searchable_count") or 0)
                if sample_context["active_latest_sort"] > float(
                    current.get("active_latest_sort") or 0
                ):
                    current["active_latest_at"] = sample_context["active_latest_at"]
                    current["active_latest_sort"] = sample_context["active_latest_sort"]
                if sample_context["latest_sort"] > float(
                    current.get("latest_sort") or 0
                ):
                    current["latest_at"] = sample_context["latest_at"]
                    current["latest_sort"] = sample_context["latest_sort"]
                current_priority = (
                    bool(current.get("_sample_has_active")),
                    float(current.get("_sample_latest_sort") or 0),
                )
                if candidate_priority > current_priority:
                    current["sample_session_id"] = bucket.get("sample_session_id")
                    current["sample_group_id"] = bucket.get("sample_group_id")
                    current["sample_bot_id"] = bucket.get("sample_bot_id")
                    current["_sample_has_active"] = candidate_priority[0]
                    current["_sample_latest_sort"] = candidate_priority[1]
                contexts = current.setdefault("sample_contexts", [])
                existing_context = next(
                    (
                        item
                        for item in contexts
                        if clean_text(item.get("bot_id"), 120)
                        == sample_context["bot_id"]
                    ),
                    None,
                )
                if existing_context is None:
                    contexts.append(sample_context)
                else:
                    existing_priority = (
                        bool(clean_text(existing_context.get("active_latest_at"), 80)),
                        float(existing_context.get("active_latest_sort") or 0)
                        or float(existing_context.get("latest_sort") or 0),
                    )
                    existing_context["memory_count"] = (
                        int(existing_context.get("memory_count") or 0)
                        + sample_context["memory_count"]
                    )
                    existing_context["archived_count"] = (
                        int(existing_context.get("archived_count") or 0)
                        + sample_context["archived_count"]
                    )
                    existing_context["searchable_count"] = (
                        int(existing_context.get("searchable_count") or 0)
                        + sample_context["searchable_count"]
                    )
                    if sample_context["active_latest_sort"] > float(
                        existing_context.get("active_latest_sort") or 0
                    ):
                        existing_context["active_latest_at"] = sample_context[
                            "active_latest_at"
                        ]
                        existing_context["active_latest_sort"] = sample_context[
                            "active_latest_sort"
                        ]
                    if sample_context["latest_sort"] > float(
                        existing_context.get("latest_sort") or 0
                    ):
                        existing_context["latest_at"] = sample_context["latest_at"]
                        existing_context["latest_sort"] = sample_context["latest_sort"]
                    if candidate_priority > existing_priority:
                        existing_context["session_id"] = sample_context["session_id"]
                        existing_context["group_id"] = sample_context["group_id"]
                candidate_name = clean_text(bucket.get("target_name"), 120)
                candidate_name_at = clean_text(bucket.get("name_latest_at"), 80)
                candidate_name_sort = float(bucket.get("name_latest_sort") or 0)
                current_name = clean_text(current.get("target_name"), 120)
                current_name_sort = float(current.get("_name_latest_sort") or 0)
                if (
                    candidate_name
                    and candidate_name
                    not in {target_id, clean_text(bucket.get("sample_session_id"), 200)}
                    and (not current_name or candidate_name_sort > current_name_sort)
                ):
                    current["target_name"] = candidate_name
                    current["_name_latest_at"] = candidate_name_at
                    current["_name_latest_sort"] = candidate_name_sort
            buckets = list(merged.values())
            for bucket in buckets:
                contexts = bucket.get("sample_contexts") or []
                contexts.sort(
                    key=lambda item: (
                        bool(clean_text(item.get("active_latest_at"), 80)),
                        float(item.get("active_latest_sort") or 0)
                        or float(item.get("latest_sort") or 0),
                    ),
                    reverse=True,
                )
                bucket.pop("_sample_has_active", None)
                bucket.pop("_sample_latest_sort", None)
                bucket.pop("_name_latest_at", None)
                bucket.pop("_name_latest_sort", None)
                bucket.pop("name_latest_at", None)
                bucket.pop("name_latest_sort", None)
            buckets.sort(
                key=lambda item: (
                    bool(int(item.get("searchable_count") or 0)),
                    float(item.get("active_latest_sort") or 0)
                    or float(item.get("latest_sort") or 0),
                    clean_text(item.get("scope"), 40),
                    clean_text(item.get("target_id"), 160),
                ),
                reverse=True,
            )
            if limit is not None:
                buckets = buckets[: max(1, int(limit))]
            for bucket in buckets:
                bucket.pop("active_latest_at", None)
                bucket.pop("active_latest_sort", None)
                bucket.pop("latest_sort", None)
                for context in bucket.get("sample_contexts") or []:
                    context.pop("active_latest_at", None)
                    context.pop("active_latest_sort", None)
                    context.pop("latest_sort", None)
                bucket["target_name"] = self._resolve_bucket_target_name_sync(
                    clean_text(bucket.get("scope"), 40),
                    clean_text(bucket.get("target_id"), 160),
                    clean_text(bucket.get("target_name"), 120),
                )
                bucket["target_kind"] = self._bucket_target_kind(
                    clean_text(bucket.get("scope"), 40),
                    clean_text(bucket.get("target_id"), 160),
                    clean_text(bucket.get("sample_session_id"), 200),
                )
        return buckets

    @staticmethod
    def _bucket_target_kind(scope: str, target_id: str, session_id: str = "") -> str:
        if scope == "group":
            return "group"
        if scope != "private":
            return "unknown"
        target_id = clean_text(target_id, 160)
        session_id = clean_text(session_id, 200).lower()
        if target_id.isdigit():
            return "qq"
        if "live2d" in session_id:
            return "legacy_live2d"
        if target_id.startswith("private_companion:"):
            return "internal"
        if re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", target_id
        ):
            return "legacy_session"
        return "private_session"

    def _resolve_bucket_target_name_sync(
        self, scope: str, target_id: str, fallback: str = ""
    ) -> str:
        fallback = self._clean_window_display_name(fallback)
        target_id = clean_text(target_id, 160)
        if fallback and fallback != target_id:
            return fallback
        if not target_id:
            return fallback

        entity_kind = "group" if scope == "group" else "user"
        row = self._conn.execute(
            """
            SELECT display_name FROM identities
            WHERE entity_kind=? AND entity_id=? AND display_name!=''
            ORDER BY confidence DESC, updated_at DESC
            LIMIT 1
            """,
            (entity_kind, target_id),
        ).fetchone()
        if row:
            name = self._clean_window_display_name(row["display_name"])
            if name:
                return name

        if scope == "group":
            row = self._conn.execute(
                """
                SELECT object_name AS name FROM relationship_edges
                WHERE object_kind='group' AND object_id=? AND object_name!=''
                ORDER BY confidence DESC, updated_at DESC
                LIMIT 1
                """,
                (target_id,),
            ).fetchone()
            if row:
                name = self._clean_window_display_name(row["name"])
                if name:
                    return name
        elif scope == "private":
            row = self._conn.execute(
                """
                SELECT name FROM (
                    SELECT subject_name AS name, confidence, updated_at FROM relationship_edges
                    WHERE subject_kind='user' AND subject_id=? AND subject_name!=''
                    UNION ALL
                    SELECT object_name AS name, confidence, updated_at FROM relationship_edges
                    WHERE object_kind='user' AND object_id=? AND object_name!=''
                )
                ORDER BY confidence DESC, updated_at DESC
                LIMIT 1
                """,
                (target_id, target_id),
            ).fetchone()
            if row:
                name = self._clean_window_display_name(row["name"])
                if name:
                    return name
        return fallback

    @staticmethod
    def _clean_window_display_name(value: Any) -> str:
        text = clean_text(value, 120)
        if not text:
            return ""
        text = re.sub(
            r"\s+(?:Avatar|Owner\s*ID|Admin\s*IDs?|Member\s*Count|Max\s*Member\s*Count|Description)\s*[:：].*$",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"^(?:Group\s*ID|Group\s*Name|Name|User\s*ID|User\s*Name|Nick(?:name)?|QQ)\s*[:：]\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return clean_text(text, 80)
