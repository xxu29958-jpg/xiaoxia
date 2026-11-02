from __future__ import annotations

import asyncio
from typing import Any

from .astrbot_compat import logger
from .models import (
    EntityRef,
    clean_text,
    json_dumps,
    json_loads,
    new_id,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value


class MemoryStoreRelationships:
    """Relationships; state and transaction ownership remain in the facade."""

    async def upsert_identity(
        self,
        *,
        platform: str,
        entity: EntityRef,
        aliases: list[str] | None = None,
        profile: dict[str, Any] | None = None,
        confidence: float = 0.6,
    ) -> str:
        return await asyncio.to_thread(
            self._upsert_identity_sync,
            platform,
            entity,
            aliases or [],
            profile or {},
            confidence,
        )

    async def upsert_identities(self, identities: list[dict[str, Any]]) -> list[str]:
        """批量写入多条身份记录，合并为单事务提交（减少独立 commit）。"""
        return await asyncio.to_thread(
            self._guard_operation_sync,
            self._upsert_identities_sync,
            identities,
        )

    def _upsert_identity_row_locked(
        self,
        platform: str,
        entity: EntityRef,
        aliases: list[str],
        profile: dict[str, Any],
        confidence: float,
    ) -> str:
        """写入单行身份记录；调用方须已持有 ``self._lock`` 且处于事务中。"""
        now = utc_now()
        entity_id = clean_text(entity.id, 120)
        if not entity_id:
            entity_id = "unknown"
        row_id = f"{platform or 'unknown'}:{entity.kind}:{entity_id}"
        aliases = [clean_text(alias, 80) for alias in aliases if clean_text(alias, 80)]
        if entity.name and entity.name not in aliases:
            aliases.append(entity.name)
        old = self._conn.execute(
            "SELECT aliases, profile, created_at FROM identities WHERE id=?",
            (row_id,),
        ).fetchone()
        created_at = now
        if old:
            created_at = old["created_at"] or now
            merged_aliases = list(
                dict.fromkeys(json_loads(old["aliases"], []) + aliases)
            )
            merged_profile = json_loads(old["profile"], {})
            merged_profile.update(profile)
        else:
            merged_aliases = aliases
            merged_profile = profile
        self._conn.execute(
            """
            INSERT INTO identities(
                id, platform, entity_kind, entity_id, display_name, role, aliases,
                profile, confidence, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform, entity_kind, entity_id) DO UPDATE SET
                display_name=excluded.display_name,
                role=excluded.role,
                aliases=excluded.aliases,
                profile=excluded.profile,
                confidence=max(identities.confidence, excluded.confidence),
                updated_at=excluded.updated_at
            """,
            (
                row_id,
                platform,
                entity.kind,
                entity_id,
                clean_text(entity.name, 80),
                clean_text(entity.role, 80),
                json_dumps(merged_aliases),
                json_dumps(merged_profile),
                confidence,
                created_at,
                now,
            ),
        )
        return row_id

    def _upsert_identity_sync(
        self,
        platform: str,
        entity: EntityRef,
        aliases: list[str],
        profile: dict[str, Any],
        confidence: float,
    ) -> str:
        with self._lock:
            row_id = self._upsert_identity_row_locked(
                platform, entity, aliases, profile, confidence
            )
            self._conn.commit()
        return row_id

    def _upsert_identities_sync(self, identities: list[dict[str, Any]]) -> list[str]:
        """批量身份写入：单事务（BEGIN IMMEDIATE + 一次 commit）。"""
        if not identities:
            return []
        with self._lock:
            with self._transaction_sync():
                return [
                    self._upsert_identity_row_locked(
                        platform=identity.get("platform") or "",
                        entity=identity.get("entity")
                        or EntityRef(kind="user", id="unknown"),
                        aliases=identity.get("aliases") or [],
                        profile=identity.get("profile") or {},
                        confidence=float(identity.get("confidence") or 0.0),
                    )
                    for identity in identities
                ]

    async def list_identities(
        self, limit: int = 1000, *, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_identities_sync, limit, offset)

    def _list_identities_sync(self, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM identities ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (max(1, int(limit)), max(0, int(offset))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["aliases"] = json_loads(item.get("aliases"), [])
            item["profile"] = json_loads(item.get("profile"), {})
            result.append(item)
        return result

    async def upsert_relationship(
        self,
        *,
        subject: EntityRef,
        object: EntityRef,
        relation_type: str,
        scope: str = "",
        session_id: str = "",
        group_id: str = "",
        visibility: str = "internal",
        evidence: str = "",
        confidence: float = 0.6,
        review_status: str = "auto",
        source_memory_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await self._run_tracked_operation(
            self._upsert_relationship_sync,
            subject,
            object,
            relation_type,
            scope,
            session_id,
            group_id,
            visibility,
            evidence,
            confidence,
            review_status,
            source_memory_id,
            metadata or {},
            closed_result="",
        )

    def _upsert_relationship_sync(
        self,
        subject: EntityRef,
        object: EntityRef,
        relation_type: str,
        scope: str,
        session_id: str,
        group_id: str,
        visibility: str,
        evidence: str,
        confidence: float,
        review_status: str,
        source_memory_id: str,
        metadata: dict[str, Any],
        _commit: bool = True,
    ) -> str:
        now = utc_now()
        row_id = new_id("rel")
        evidence = redact_sensitive_text(evidence)
        metadata = redact_sensitive_value(metadata)
        with self._lock:
            old = self._conn.execute(
                """
                SELECT id, metadata, created_at FROM relationship_edges
                WHERE subject_kind=? AND subject_id=? AND object_kind=? AND object_id=?
                  AND relation_type=? AND scope=? AND session_id=?
                """,
                (
                    subject.kind,
                    subject.id,
                    object.kind,
                    object.id,
                    clean_text(relation_type, 80),
                    clean_text(scope, 40),
                    clean_text(session_id, 200),
                ),
            ).fetchone()
            if old:
                row_id = old["id"]
                merged_metadata = json_loads(old["metadata"], {})
                merged_metadata.update(metadata)
            else:
                merged_metadata = metadata
            self._conn.execute(
                """
                INSERT INTO relationship_edges(
                    id, subject_kind, subject_id, subject_name, object_kind, object_id,
                    object_name, relation_type, scope, session_id, group_id, visibility,
                    evidence, confidence, review_status, source_memory_id, metadata,
                    created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(subject_kind, subject_id, object_kind, object_id, relation_type, scope, session_id)
                DO UPDATE SET
                    subject_name=excluded.subject_name,
                    object_name=excluded.object_name,
                    visibility=excluded.visibility,
                    evidence=excluded.evidence,
                    confidence=max(relationship_edges.confidence, excluded.confidence),
                    review_status=excluded.review_status,
                    source_memory_id=excluded.source_memory_id,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    row_id,
                    clean_text(subject.kind, 40),
                    clean_text(subject.id, 120),
                    clean_text(subject.name, 80),
                    clean_text(object.kind, 40),
                    clean_text(object.id, 120),
                    clean_text(object.name, 80),
                    clean_text(relation_type, 80),
                    clean_text(scope, 40),
                    clean_text(session_id, 200),
                    clean_text(group_id, 120),
                    clean_text(visibility, 40),
                    clean_text(evidence, 1000),
                    max(0.0, min(1.0, float(confidence or 0.0))),
                    clean_text(review_status, 40),
                    clean_text(source_memory_id, 120),
                    json_dumps(merged_metadata),
                    now,
                    now,
                ),
            )
            if _commit:
                self._conn.commit()
        return row_id

    async def capture_write_batch(
        self, ops: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """批量执行采集写链，合并为单事务一次 commit。

        ops 为有序写操作描述列表，元素形如：
          - {"kind": "relationship", "source_memory_id": str,
             "params": {详见 _upsert_relationship_sync 关键字参数}}
          - {"kind": "profile", "record": MemoryRecord}
          - {"kind": "memory", "record": MemoryRecord}
        可附加字段：
          - "requires_ok": list[int] 仅当这些索引对应的 op 均成功才执行
          - "source_memory_id_from": int 从该索引 op 的结果中取 memory_id
        profile 项内部已含 SAVEPOINT（_transaction_sync），在外层事务中自动降级嵌套；
        memory 项通过 _commit=False 复用内部无 commit 变体。
        返回与 ops 等长的结果列表；单项失败被 SAVEPOINT 隔离，不拖累整批。
        """
        return await asyncio.to_thread(
            self._guard_operation_sync,
            self._capture_write_batch_sync,
            ops,
        )

    def _capture_write_batch_sync(
        self, ops: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not ops:
            return results
        with self._lock:
            with self._transaction_sync():
                for index, op in enumerate(ops):
                    kind = "unknown"
                    try:
                        if not isinstance(op, dict):
                            raise TypeError("batch operation must be a mapping")
                        kind = clean_text(op.get("kind"), 40) or "unknown"
                        required_indexes = op.get("requires_ok") or []
                        if any(
                            not isinstance(i, int)
                            or i < 0
                            or i >= len(results)
                            or not results[i].get("ok")
                            for i in required_indexes
                        ):
                            results.append(
                                {"ok": False, "code": "skip_required_op_failed"}
                            )
                            continue
                        with self._transaction_sync():  # 单项 SAVEPOINT 隔离失败
                            if kind == "relationship":
                                params = dict(op["params"])
                                if op.get("source_memory_id_from") is not None:
                                    src_index = op["source_memory_id_from"]
                                    src = (
                                        results[src_index].get("memory_id") or ""
                                        if src_index < len(results)
                                        else ""
                                    )
                                    params["source_memory_id"] = src
                                elif op.get("source_memory_id"):
                                    params["source_memory_id"] = op["source_memory_id"]
                                elif "source_memory_id" not in params:
                                    params["source_memory_id"] = ""
                                row_id = self._upsert_relationship_sync(
                                    _commit=False, **params
                                )
                                results.append({"ok": True, "row_id": row_id})
                            elif kind == "profile":
                                results.append(
                                    self._upsert_profile_candidate_sync(op["record"])
                                )
                            elif kind == "memory":
                                memory_id = self._insert_memory_sync(
                                    op["record"],
                                    op.get("review_reason") or "",
                                    _commit=False,
                                )
                                results.append({"ok": True, "memory_id": memory_id})
                            else:
                                results.append(
                                    {"ok": False, "code": f"unknown_batch_op:{kind}"}
                                )
                    except Exception as exc:
                        logger.warning(
                            "[MemoryCompanion] 批量写入单项失败 index=%s kind=%s error=%s",
                            index,
                            kind,
                            exc,
                            exc_info=True,
                        )
                        results.append(
                            {
                                "ok": False,
                                "code": "batch_op_error",
                                "error": clean_text(str(exc), 300),
                            }
                        )
        return results

    async def list_relationships(
        self,
        limit: int = 20,
        entity_id: str = "",
        memory_types: list[str] | tuple[str, ...] | None = None,
        scope: str = "",
        session_id: str = "",
        group_id: str = "",
        *,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_relationships_sync,
            limit,
            entity_id,
            memory_types,
            scope,
            session_id,
            group_id,
            offset,
        )

    def _list_relationships_sync(
        self,
        limit: int,
        entity_id: str,
        memory_types: list[str] | tuple[str, ...] | None,
        scope: str,
        session_id: str,
        group_id: str,
        offset: int,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "1=1"
        if entity_id:
            where += " AND (subject_id=? OR object_id=?)"
            params.extend([entity_id, entity_id])
        if scope:
            where += " AND scope=?"
            params.append(scope)
        if session_id:
            where += " AND session_id=?"
            params.append(session_id)
        if group_id:
            where += " AND group_id=?"
            params.append(group_id)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM relationship_edges
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [max(1, int(limit)), max(0, int(offset))],
            ).fetchall()
        return [dict(row) for row in rows]
