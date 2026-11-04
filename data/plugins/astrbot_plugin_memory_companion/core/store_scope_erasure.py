from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from pathlib import Path
from threading import RLock
from typing import Any

from .models import (
    clean_text,
)
from .store_erasure_domains import CLEAR_TABLES, validate_scope, working_scope


class MemoryStoreScopeErasure:
    """Scope erasure; state and transaction ownership remain in the facade."""

    _conn: sqlite3.Connection
    _lock: RLock
    backup: Callable[[str], Path]
    _transaction_sync: Callable[[], AbstractContextManager]
    clear_acl_projection_sync: Callable[[], object]
    _timeline_reference_memories_sync: Callable[[set[str]], Iterable[tuple[str, dict]]]
    _delete_memory_fts_row: Callable[[str], object]
    _delete_memories_referencing_timeline_ids_sync: Callable[[list[str]], int]

    async def clear_all_memory_data(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._clear_all_memory_data_sync)

    def _clear_all_memory_data_sync(self) -> dict[str, Any]:
        backup = self.backup(".before_clear_all")
        deleted: dict[str, int] = {}
        with self._lock:
            with self._transaction_sync():
                for table in CLEAR_TABLES:
                    try:
                        cur = self._conn.execute(f"DELETE FROM {table}")
                    except sqlite3.Error:
                        if table == "memory_fts":
                            continue
                        raise
                    deleted[table] = int(cur.rowcount or 0)
            # The policy table was just emptied; the projection has to follow,
            # otherwise window switches would keep answering with deleted rows.
            self.clear_acl_projection_sync()
        return {"backup": str(backup), "deleted": deleted}

    async def preview_scoped_memory_clear(
        self,
        *,
        target_type: str,
        group_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._scoped_memory_clear_sync,
            target_type,
            group_id,
            user_id,
            False,
        )

    async def clear_scoped_memory(
        self,
        *,
        target_type: str,
        group_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._scoped_memory_clear_sync,
            target_type,
            group_id,
            user_id,
            True,
        )

    def _scoped_memory_clear_sync(
        self,
        target_type: str,
        group_id: str,
        user_id: str,
        execute: bool,
    ) -> dict[str, Any]:
        target_type, group_id, user_id = validate_scope(target_type, group_id, user_id)

        memory_where, memory_params = self._scoped_memory_where(
            target_type, group_id, user_id
        )
        timeline_where, timeline_params = self._scoped_timeline_where(
            target_type, group_id, user_id
        )
        relation_where, relation_params = self._scoped_relation_where(
            target_type, group_id, user_id
        )
        knowledge_node_where, knowledge_node_params = self._scoped_knowledge_node_where(
            target_type, group_id, user_id
        )
        knowledge_edge_where, knowledge_edge_params = self._scoped_knowledge_edge_where(
            target_type, group_id, user_id
        )
        injection_where, injection_params = self._scoped_session_log_where(
            target_type, group_id, user_id
        )
        thread_where, thread_params = self._scoped_thread_where(
            target_type, group_id, user_id
        )

        with self._lock:
            memory_ids = [
                row["id"]
                for row in self._conn.execute(
                    f"SELECT id FROM memories WHERE {memory_where}",
                    memory_params,
                ).fetchall()
            ]
            timeline_ids = [
                row["id"]
                for row in self._conn.execute(
                    f"SELECT id FROM timeline WHERE {timeline_where}", timeline_params
                ).fetchall()
            ]
            linked_memory_ids = [
                memory_id
                for memory_id, _metadata in self._timeline_reference_memories_sync(
                    set(timeline_ids)
                )
            ]
            all_memory_ids = list(dict.fromkeys([*memory_ids, *linked_memory_ids]))
            counts = {
                "working_objects": working_scope(self._conn, target_type, user_id),
                "memories": len(all_memory_ids),
                "timeline": len(timeline_ids),
                "relationship_edges": self._count_relationship_edges_for_scope_or_memory_ids(
                    relation_where,
                    relation_params,
                    all_memory_ids,
                ),
                "knowledge_nodes": self._count_where(
                    "knowledge_nodes", knowledge_node_where, knowledge_node_params
                ),
                "knowledge_edges": self._count_knowledge_edges_for_scope_or_memory_ids(
                    knowledge_edge_where,
                    knowledge_edge_params,
                    all_memory_ids,
                ),
                "injection_logs": self._count_where(
                    "injection_logs", injection_where, injection_params
                ),
                "summary_failures": self._count_where(
                    "summary_failures", injection_where, injection_params
                ),
                "summary_batches": self._count_where(
                    "summary_batches", injection_where, injection_params
                ),
                "cross_window_threads": self._count_where(
                    "cross_window_threads", thread_where, thread_params
                ),
            }
            if not execute:
                return {
                    "target_type": target_type,
                    "group_id": group_id,
                    "user_id": user_id,
                    "preview": True,
                    "counts": counts,
                }

            backup = self.backup(f".before_clear_{target_type}")
            deleted: dict[str, int] = {}
            with self._transaction_sync():
                deleted["working_objects"] = working_scope(
                    self._conn, target_type, user_id, execute=True
                )
                if all_memory_ids:
                    self._delete_many_by_ids(
                        "review_queue", "memory_id", all_memory_ids, deleted
                    )
                    self._delete_many_by_ids(
                        "memory_embeddings", "memory_id", all_memory_ids, deleted
                    )
                    self._delete_many_by_ids(
                        "knowledge_edges", "source_memory_id", all_memory_ids, deleted
                    )
                    self._delete_many_by_ids(
                        "relationship_edges",
                        "source_memory_id",
                        all_memory_ids,
                        deleted,
                    )
                    for memory_id in all_memory_ids:
                        self._delete_memory_fts_row(memory_id)
                deleted["memories"] = self._delete_where(
                    "memories", memory_where, memory_params
                )
                deleted["memories"] += (
                    self._delete_memories_referencing_timeline_ids_sync(timeline_ids)
                )
                deleted["timeline"] = self._delete_where(
                    "timeline", timeline_where, timeline_params
                )
                deleted["relationship_edges"] = deleted.get(
                    "relationship_edges", 0
                ) + self._delete_where(
                    "relationship_edges", relation_where, relation_params
                )
                deleted["knowledge_edges"] = deleted.get(
                    "knowledge_edges", 0
                ) + self._delete_where(
                    "knowledge_edges",
                    knowledge_edge_where,
                    knowledge_edge_params,
                )
                deleted["knowledge_nodes"] = self._delete_where(
                    "knowledge_nodes", knowledge_node_where, knowledge_node_params
                )
                deleted["injection_logs"] = self._delete_where(
                    "injection_logs", injection_where, injection_params
                )
                deleted["summary_failures"] = self._delete_where(
                    "summary_failures", injection_where, injection_params
                )
                deleted["summary_batches"] = self._delete_where(
                    "summary_batches", injection_where, injection_params
                )
                deleted["cross_window_threads"] = self._delete_where(
                    "cross_window_threads", thread_where, thread_params
                )
        return {
            "target_type": target_type,
            "group_id": group_id,
            "user_id": user_id,
            "preview": False,
            "backup": str(backup),
            "counts": counts,
            "deleted": deleted,
        }

    def _count_where(self, table: str, where: str, params: list[Any]) -> int:
        row = self._conn.execute(
            f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", params
        ).fetchone()
        return int(row["c"] if row else 0)

    def _delete_where(self, table: str, where: str, params: list[Any]) -> int:
        cur = self._conn.execute(f"DELETE FROM {table} WHERE {where}", params)
        return int(cur.rowcount or 0)

    def _delete_many_by_ids(
        self, table: str, column: str, ids: list[str], deleted: dict[str, int]
    ) -> None:
        total = 0
        for index in range(0, len(ids), 500):
            chunk = ids[index : index + 500]
            placeholders = ",".join("?" for _ in chunk)
            cur = self._conn.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})", chunk
            )
            total += int(cur.rowcount or 0)
        deleted[table] = deleted.get(table, 0) + total

    @staticmethod
    def _like_id(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}"

    def _session_target_where(
        self, column: str, scope: str, target_id: str
    ) -> tuple[str, list[Any]]:
        target_id = clean_text(target_id, 120)
        lowered = target_id.lower()
        if scope == "group":
            tokens = (":groupmessage:", ":group:")
        else:
            tokens = (":friendmessage:", ":privatemessage:", ":friend:", ":private:")
        clauses = [f"{column}=?"]
        params: list[Any] = [target_id]
        for token in tokens:
            clauses.append(f"LOWER({column}) LIKE ? ESCAPE '\\'")
            params.append(self._like_id(f"{token}{lowered}"))
        return f"({' OR '.join(clauses)})", params

    def _count_knowledge_edges_for_scope_or_memory_ids(
        self,
        where: str,
        params: list[Any],
        memory_ids: list[str],
    ) -> int:
        edge_ids = {
            row["id"]
            for row in self._conn.execute(
                f"SELECT id FROM knowledge_edges WHERE {where}", params
            ).fetchall()
        }
        for index in range(0, len(memory_ids), 500):
            chunk = memory_ids[index : index + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id FROM knowledge_edges WHERE source_memory_id IN ({placeholders})",
                chunk,
            ).fetchall()
            edge_ids.update(row["id"] for row in rows)
        return len(edge_ids)

    def _count_relationship_edges_for_scope_or_memory_ids(
        self,
        where: str,
        params: list[Any],
        memory_ids: list[str],
    ) -> int:
        edge_ids = {
            row["id"]
            for row in self._conn.execute(
                f"SELECT id FROM relationship_edges WHERE {where}", params
            ).fetchall()
        }
        for index in range(0, len(memory_ids), 500):
            chunk = memory_ids[index : index + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id FROM relationship_edges WHERE source_memory_id IN ({placeholders})",
                chunk,
            ).fetchall()
            edge_ids.update(row["id"] for row in rows)
        return len(edge_ids)

    def _scoped_memory_where(
        self, target_type: str, group_id: str, user_id: str
    ) -> tuple[str, list[Any]]:
        if target_type == "group":
            session_where, session_params = self._session_target_where(
                "session_id", "group", group_id
            )
            return (
                f"scope='group' AND (group_id=? OR object_id=? OR {session_where})",
                [group_id, group_id, *session_params],
            )
        if target_type == "private":
            session_where, session_params = self._session_target_where(
                "session_id", "private", user_id
            )
            return (
                f"scope='private' AND (subject_id=? OR object_id=? OR {session_where})",
                [user_id, user_id, *session_params],
            )
        session_where, session_params = self._session_target_where(
            "session_id", "group", group_id
        )
        return (
            f"scope='group' AND (group_id=? OR {session_where}) AND (subject_id=? OR object_id=?)",
            [group_id, *session_params, user_id, user_id],
        )

    def _scoped_timeline_where(
        self, target_type: str, group_id: str, user_id: str
    ) -> tuple[str, list[Any]]:
        if target_type == "group":
            session_where, session_params = self._session_target_where(
                "session_id", "group", group_id
            )
            return (
                f"scope='group' AND ({session_where} OR object_id=?)",
                [*session_params, group_id],
            )
        if target_type == "private":
            session_where, session_params = self._session_target_where(
                "session_id", "private", user_id
            )
            return (
                f"scope='private' AND (subject_id=? OR object_id=? OR {session_where})",
                [user_id, user_id, *session_params],
            )
        session_where, session_params = self._session_target_where(
            "session_id", "group", group_id
        )
        return (
            f"scope='group' AND {session_where} AND (subject_id=? OR object_id=?)",
            [*session_params, user_id, user_id],
        )

    def _scoped_relation_where(
        self, target_type: str, group_id: str, user_id: str
    ) -> tuple[str, list[Any]]:
        if target_type == "group":
            session_where, session_params = self._session_target_where(
                "session_id", "group", group_id
            )
            return (
                f"scope='group' AND (group_id=? OR {session_where})",
                [group_id, *session_params],
            )
        if target_type == "private":
            session_where, session_params = self._session_target_where(
                "session_id", "private", user_id
            )
            return (
                f"scope='private' AND (subject_id=? OR object_id=? OR {session_where})",
                [user_id, user_id, *session_params],
            )
        session_where, session_params = self._session_target_where(
            "session_id", "group", group_id
        )
        return (
            f"scope='group' AND (group_id=? OR {session_where}) AND (subject_id=? OR object_id=?)",
            [group_id, *session_params, user_id, user_id],
        )

    def _scoped_knowledge_node_where(
        self, target_type: str, group_id: str, user_id: str
    ) -> tuple[str, list[Any]]:
        if target_type == "group":
            session_where, session_params = self._session_target_where(
                "session_id", "group", group_id
            )
            return (
                f"scope='group' AND (group_id=? OR {session_where})",
                [group_id, *session_params],
            )
        if target_type == "private":
            session_where, session_params = self._session_target_where(
                "session_id", "private", user_id
            )
            return (f"scope='private' AND {session_where}", session_params)
        session_where, session_params = self._session_target_where(
            "session_id", "group", group_id
        )
        return (
            f"scope='group' AND (group_id=? OR {session_where}) AND node_type='user' AND node_key=?",
            [group_id, *session_params, user_id.lower()],
        )

    def _scoped_knowledge_edge_where(
        self, target_type: str, group_id: str, user_id: str
    ) -> tuple[str, list[Any]]:
        if target_type == "group":
            session_where, session_params = self._session_target_where(
                "session_id", "group", group_id
            )
            return (
                f"scope='group' AND (group_id=? OR {session_where})",
                [group_id, *session_params],
            )
        if target_type == "private":
            session_where, session_params = self._session_target_where(
                "session_id", "private", user_id
            )
            return (f"scope='private' AND {session_where}", session_params)
        session_where, session_params = self._session_target_where(
            "session_id", "group", group_id
        )
        return (
            f"""scope='group' AND (group_id=? OR {session_where}) AND (
                source_node_id IN (SELECT id FROM knowledge_nodes WHERE node_type='user' AND node_key=?)
                OR target_node_id IN (SELECT id FROM knowledge_nodes WHERE node_type='user' AND node_key=?)
            )""",
            [group_id, *session_params, user_id.lower(), user_id.lower()],
        )

    def _scoped_session_log_where(
        self, target_type: str, group_id: str, user_id: str
    ) -> tuple[str, list[Any]]:
        if target_type == "group":
            session_where, session_params = self._session_target_where(
                "session_id", "group", group_id
            )
            return (f"scope='group' AND {session_where}", session_params)
        if target_type == "private":
            session_where, session_params = self._session_target_where(
                "session_id", "private", user_id
            )
            return (f"scope='private' AND {session_where}", session_params)
        return ("1=0", [])

    def _scoped_thread_where(
        self, target_type: str, group_id: str, user_id: str
    ) -> tuple[str, list[Any]]:
        if target_type == "group_member":
            return ("1=0", [])
        value = group_id if target_type in {"group", "group_member"} else user_id
        scope = "group" if target_type == "group" else "private"
        from_where, from_params = self._session_target_where(
            "from_session", scope, value
        )
        to_where, to_params = self._session_target_where("to_session", scope, value)
        return (f"{from_where} OR {to_where}", [*from_params, *to_params])
