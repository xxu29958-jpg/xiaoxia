from __future__ import annotations

import asyncio
from typing import Any

from .models import (
    clean_text,
    json_dumps,
    json_loads,
    stable_fingerprint,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value


class MemoryStoreKnowledgeGraph:
    """Knowledge graph; state and transaction ownership remain in the facade."""

    async def upsert_knowledge_node(
        self,
        *,
        node_type: str,
        label: str,
        scope: str = "",
        session_id: str = "",
        group_id: str = "",
        confidence: float = 0.6,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._upsert_knowledge_node_sync,
            node_type,
            label,
            scope,
            session_id,
            group_id,
            confidence,
            metadata or {},
        )

    def _upsert_knowledge_node_sync(
        self,
        node_type: str,
        label: str,
        scope: str,
        session_id: str,
        group_id: str,
        confidence: float,
        metadata: dict[str, Any],
    ) -> str:
        node_type = clean_text(node_type, 40)
        label = clean_text(redact_sensitive_text(label), 160)
        metadata = redact_sensitive_value(metadata)
        scope = clean_text(scope, 40)
        session_id = clean_text(session_id, 200)
        group_id = clean_text(group_id, 120)
        if not node_type or not label:
            return ""
        node_key = stable_fingerprint(node_type, label.lower())
        node_id = (
            "kg_node_" + stable_fingerprint(node_type, node_key, scope, session_id)[:16]
        )
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO knowledge_nodes(
                    id, node_type, node_key, label, scope, session_id, group_id,
                    confidence, metadata, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(node_type, node_key, scope, session_id) DO UPDATE SET
                    label=excluded.label,
                    group_id=CASE WHEN excluded.group_id!='' THEN excluded.group_id ELSE knowledge_nodes.group_id END,
                    confidence=max(knowledge_nodes.confidence, excluded.confidence),
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    node_id,
                    node_type,
                    node_key,
                    label,
                    scope,
                    session_id,
                    group_id,
                    max(0.0, min(1.0, float(confidence or 0.0))),
                    json_dumps(metadata),
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                """
                SELECT id FROM knowledge_nodes
                WHERE node_type=? AND node_key=? AND scope=? AND session_id=?
                """,
                (node_type, node_key, scope, session_id),
            ).fetchone()
            self._conn.commit()
        return str(row["id"] if row else node_id)

    async def upsert_knowledge_edge(
        self,
        *,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        scope: str = "",
        session_id: str = "",
        group_id: str = "",
        source_memory_id: str = "",
        evidence: str = "",
        confidence: float = 0.6,
        review_status: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._upsert_knowledge_edge_sync,
            source_node_id,
            target_node_id,
            relation_type,
            scope,
            session_id,
            group_id,
            source_memory_id,
            evidence,
            confidence,
            review_status,
            metadata or {},
        )

    def _upsert_knowledge_edge_sync(
        self,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        scope: str,
        session_id: str,
        group_id: str,
        source_memory_id: str,
        evidence: str,
        confidence: float,
        review_status: str,
        metadata: dict[str, Any],
    ) -> str:
        source_node_id = clean_text(source_node_id, 120)
        target_node_id = clean_text(target_node_id, 120)
        relation_type = clean_text(relation_type, 60)
        source_memory_id = clean_text(source_memory_id, 120)
        evidence = redact_sensitive_text(evidence)
        metadata = redact_sensitive_value(metadata)
        if not source_node_id or not target_node_id or not relation_type:
            return ""
        edge_id = (
            "kg_edge_"
            + stable_fingerprint(
                source_node_id, target_node_id, relation_type, source_memory_id
            )[:16]
        )
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO knowledge_edges(
                    id, source_node_id, target_node_id, relation_type, scope, session_id,
                    group_id, source_memory_id, evidence, confidence, review_status,
                    metadata, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_node_id, target_node_id, relation_type, source_memory_id) DO UPDATE SET
                    evidence=excluded.evidence,
                    confidence=max(knowledge_edges.confidence, excluded.confidence),
                    review_status=excluded.review_status,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    edge_id,
                    source_node_id,
                    target_node_id,
                    relation_type,
                    clean_text(scope, 40),
                    clean_text(session_id, 200),
                    clean_text(group_id, 120),
                    source_memory_id,
                    clean_text(evidence, 1000),
                    max(0.0, min(1.0, float(confidence or 0.0))),
                    clean_text(review_status, 40),
                    json_dumps(metadata),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return edge_id

    async def list_knowledge_edges(
        self,
        limit: int = 50,
        scope: str = "",
        session_id: str = "",
        group_id: str = "",
        node: str = "",
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_knowledge_edges_sync,
            limit,
            scope,
            session_id,
            group_id,
            node,
        )

    def _list_knowledge_edges_sync(
        self,
        limit: int,
        scope: str,
        session_id: str,
        group_id: str,
        node: str,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "1=1"
        if scope:
            where += " AND e.scope=?"
            params.append(clean_text(scope, 40))
        if session_id:
            where += " AND e.session_id=?"
            params.append(clean_text(session_id, 200))
        if group_id:
            where += " AND e.group_id=?"
            params.append(clean_text(group_id, 120))
        node = clean_text(node, 160)
        if node:
            where += " AND (s.label LIKE ? ESCAPE '\\' OR t.label LIKE ? ESCAPE '\\')"
            like = self._like_pattern(node)
            params.extend([like, like])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    e.*,
                    s.node_type AS source_type,
                    s.label AS source_label,
                    t.node_type AS target_type,
                    t.label AS target_label
                FROM knowledge_edges e
                LEFT JOIN knowledge_nodes s ON s.id=e.source_node_id
                LEFT JOIN knowledge_nodes t ON t.id=e.target_node_id
                WHERE {where}
                ORDER BY e.updated_at DESC
                LIMIT ?
                """,
                params + [max(1, int(limit))],
            ).fetchall()
        return [dict(row) for row in rows]

    async def query_knowledge_paths(
        self,
        terms: list[str],
        *,
        tag: str = "",
        node_type: str = "",
        memory_ids: list[str] | None = None,
        limit: int = 80,
        session_id: str = "",
        scope: str = "",
        group_id: str = "",
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        """Return bounded one-hop graph paths for active memory reconstruction.

        This storage query intentionally does not decide visibility. Callers must
        resolve ``source_memory_id`` values to memories and apply the current ACL
        before exposing any returned evidence.
        """
        return await asyncio.to_thread(
            self._query_knowledge_paths_sync,
            terms,
            tag,
            node_type,
            memory_ids,
            limit,
            session_id,
            scope,
            group_id,
            user_id,
        )

    def _query_knowledge_paths_sync(
        self,
        terms: list[str],
        tag: str,
        node_type: str,
        memory_ids: list[str] | None,
        limit: int,
        session_id: str,
        scope: str,
        group_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        # Keep placeholder counts comfortably below SQLite's common variable
        # limit. Repeated tool calls provide traversal depth at the service layer.
        cleaned_terms: list[str] = []
        raw_terms = terms if isinstance(terms, list) else []
        for raw_term in raw_terms:
            term = clean_text(raw_term, 160).lower()
            if term and term not in cleaned_terms:
                cleaned_terms.append(term)
            if len(cleaned_terms) >= 32:
                break

        cleaned_memory_ids: list[str] = []
        raw_memory_ids = memory_ids if isinstance(memory_ids, list) else []
        for raw_memory_id in raw_memory_ids:
            memory_id = clean_text(raw_memory_id, 120)
            if memory_id and memory_id not in cleaned_memory_ids:
                cleaned_memory_ids.append(memory_id)
            if len(cleaned_memory_ids) >= 256:
                break

        tag = clean_text(tag, 80).lower()
        node_type = clean_text(node_type, 40).lower()
        session_id = clean_text(session_id, 200)
        scope = clean_text(scope, 40).lower()
        group_id = clean_text(group_id, 120)
        user_id = clean_text(user_id, 160)
        if not cleaned_terms and not tag and not node_type and not cleaned_memory_ids:
            return []

        def literal_like(value: str) -> str:
            escaped = (
                value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            return f"%{escaped}%"

        clauses: list[str] = []
        params: list[Any] = []
        if cleaned_terms:
            term_clauses: list[str] = []
            for term in cleaned_terms:
                term_clauses.append(
                    "(lower(s.label) LIKE ? ESCAPE '\\' OR lower(t.label) LIKE ? ESCAPE '\\')"
                )
                pattern = literal_like(term)
                params.extend([pattern, pattern])
            clauses.append("(" + " OR ".join(term_clauses) + ")")
        if tag:
            tag_pattern = literal_like(tag)
            clauses.append(
                """
                (
                    lower(
                        CASE
                            WHEN json_valid(e.metadata)
                            THEN coalesce(CAST(json_extract(e.metadata, '$.associative_tag') AS TEXT), '')
                            ELSE ''
                        END
                    ) LIKE ? ESCAPE '\\'
                    OR (lower(s.node_type)='tag' AND lower(s.label) LIKE ? ESCAPE '\\')
                    OR (lower(t.node_type)='tag' AND lower(t.label) LIKE ? ESCAPE '\\')
                )
                """
            )
            params.extend([tag_pattern, tag_pattern, tag_pattern])
        if node_type:
            clauses.append("(lower(s.node_type)=? OR lower(t.node_type)=?)")
            params.extend([node_type, node_type])
        if cleaned_memory_ids:
            placeholders = ",".join("?" for _ in cleaned_memory_ids)
            clauses.append(f"e.source_memory_id IN ({placeholders})")
            params.extend(cleaned_memory_ids)

        try:
            bounded_limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            bounded_limit = 80

        # Prefer paths that are likely to survive the service-layer visibility
        # check. This keeps a dense, unrelated user's graph from consuming the
        # bounded scan budget before current-session evidence is considered.
        priority_cases: list[str] = []
        priority_params: list[Any] = []
        if session_id:
            priority_cases.append("WHEN e.session_id=? THEN 0")
            priority_params.append(session_id)
        if user_id:
            priority_cases.append("WHEN (m.subject_id=? OR m.object_id=?) THEN 1")
            priority_params.extend([user_id, user_id])
        if group_id:
            priority_cases.append("WHEN e.group_id=? THEN 2")
            priority_params.append(group_id)
        if scope:
            priority_cases.append("WHEN lower(e.scope)=? THEN 3")
            priority_params.append(scope)
        priority_order = (
            "CASE " + " ".join(priority_cases) + " ELSE 4 END, "
            if priority_cases
            else ""
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT
                    e.id AS edge_id,
                    e.source_memory_id,
                    e.source_node_id,
                    s.node_type AS source_type,
                    s.node_key AS source_key,
                    s.label AS source_label,
                    s.metadata AS source_metadata,
                    e.target_node_id,
                    t.node_type AS target_type,
                    t.node_key AS target_key,
                    t.label AS target_label,
                    t.metadata AS target_metadata,
                    e.relation_type,
                    e.evidence,
                    e.confidence,
                    e.review_status,
                    e.scope,
                    e.session_id,
                    e.group_id,
                    e.metadata AS edge_metadata,
                    e.created_at,
                    e.updated_at
                FROM knowledge_edges e
                JOIN knowledge_nodes s ON s.id=e.source_node_id
                JOIN knowledge_nodes t ON t.id=e.target_node_id
                LEFT JOIN memories m ON m.id=e.source_memory_id
                WHERE {" AND ".join(clauses)}
                ORDER BY {priority_order}e.confidence DESC, e.updated_at DESC, e.id ASC
                LIMIT ?
                """,
                params + priority_params + [bounded_limit],
            ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("source_metadata", "target_metadata", "edge_metadata"):
                parsed = json_loads(item.get(key), {})
                item[key] = parsed if isinstance(parsed, dict) else {}
            results.append(item)
        return results

    async def related_knowledge_terms(
        self,
        terms: list[str],
        *,
        scope: str = "",
        session_id: str = "",
        group_id: str = "",
        limit: int = 12,
    ) -> list[str]:
        return await asyncio.to_thread(
            self._related_knowledge_terms_sync,
            terms,
            scope,
            session_id,
            group_id,
            limit,
        )

    def _related_knowledge_terms_sync(
        self,
        terms: list[str],
        scope: str,
        session_id: str,
        group_id: str,
        limit: int,
    ) -> list[str]:
        cleaned_terms = [
            clean_text(term, 80).lower() for term in terms if clean_text(term, 80)
        ]
        if not cleaned_terms:
            return []
        params: list[Any] = []
        scope = clean_text(scope, 40)
        session_id = clean_text(session_id, 200)
        group_id = clean_text(group_id, 120)
        scope_filter = ""
        if scope:
            scope_filter += " AND (n.scope='' OR n.scope=?)"
            params.append(scope)
        if session_id:
            scope_filter += " AND (n.session_id='' OR n.session_id=?)"
            params.append(session_id)
        if group_id:
            scope_filter += " AND (n.group_id='' OR n.group_id=?)"
            params.append(group_id)
        match_clauses: list[str] = []
        match_params: list[Any] = []
        like_terms = cleaned_terms
        if self._knowledge_trgm_enabled:
            # Trigram substring match equals LIKE '%term%' for terms with
            # >=3 alphanumeric characters. Terms containing punctuation keep
            # the exact LIKE path because the trigram tokenizer splits tokens
            # on non-alphanumeric characters.
            long_terms = [
                term for term in cleaned_terms if len(term) >= 3 and term.isalnum()
            ]
            like_terms = [term for term in cleaned_terms if term not in long_terms]
            if long_terms:
                trgm_query = " OR ".join(
                    self._quote_fts_term(term) for term in long_terms
                )
                match_clauses.append(
                    "n.rowid IN (SELECT rowid FROM knowledge_label_trgm WHERE knowledge_label_trgm MATCH ?)"
                )
                match_params.append(trgm_query)
        if like_terms:
            like_sql = " OR ".join(
                ["lower(n.label) LIKE ? ESCAPE '\\'" for _ in like_terms]
            )
            match_clauses.append(f"({like_sql})")
            match_params.extend(self._like_pattern(term) for term in like_terms)
        match_where = " OR ".join(match_clauses) or "1=0"
        with self._lock:
            matched = self._conn.execute(
                f"""
                SELECT n.id, n.label
                FROM knowledge_nodes n
                WHERE ({match_where}) {scope_filter}
                ORDER BY n.updated_at DESC
                LIMIT ?
                """,
                match_params + params + [max(1, int(limit))],
            ).fetchall()
            matched_ids = [str(row["id"]) for row in matched]
            labels = [clean_text(row["label"], 80) for row in matched]
            if not matched_ids:
                return labels[:limit]
            placeholders = ",".join("?" for _ in matched_ids)
            related = self._conn.execute(
                f"""
                SELECT DISTINCT n.label
                FROM knowledge_edges e
                JOIN knowledge_nodes n
                  ON n.id = CASE
                    WHEN e.source_node_id IN ({placeholders}) THEN e.target_node_id
                    ELSE e.source_node_id
                  END
                WHERE e.source_node_id IN ({placeholders})
                   OR e.target_node_id IN ({placeholders})
                ORDER BY e.updated_at DESC
                LIMIT ?
                """,
                matched_ids + matched_ids + matched_ids + [max(1, int(limit))],
            ).fetchall()
        for row in related:
            label = clean_text(row["label"], 80)
            if label and label.lower() not in cleaned_terms and label not in labels:
                labels.append(label)
            if len(labels) >= limit:
                break
        return labels[:limit]
