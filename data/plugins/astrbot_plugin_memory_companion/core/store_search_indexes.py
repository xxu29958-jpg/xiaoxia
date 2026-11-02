from __future__ import annotations

import asyncio
import re
import sqlite3
from typing import Any

from .models import (
    MemoryRecord,
    clean_text,
    is_memory_placeholder,
    json_dumps,
    json_loads,
    utc_now,
)


class MemoryStoreSearchIndexes:
    """Search indexes; state and transaction ownership remain in the facade."""

    def _ensure_retrieval_revision_sync(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS retrieval_revision (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                revision INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO retrieval_revision(singleton, revision) VALUES(1, 0);

            CREATE TRIGGER IF NOT EXISTS trg_memories_retrieval_revision_insert
            AFTER INSERT ON memories
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_memories_retrieval_revision_delete
            AFTER DELETE ON memories
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            DROP TRIGGER IF EXISTS trg_memories_retrieval_revision_update;
            CREATE TRIGGER IF NOT EXISTS trg_memories_retrieval_revision_update
            AFTER UPDATE OF
                memory_type, subject_kind, subject_id, subject_name, subject_role,
                object_kind, object_id, object_name, object_role, scope, session_id,
                platform, message_id, group_id, visibility, sayability, reality_level,
                lifecycle, content, evidence, confidence, importance, review_status,
                tags, metadata, created_at, updated_at, occurred_at, source_plugin,
                import_batch_id, content_fingerprint, merged_count, supersedes_id,
                owner_bot_id, validity_status, valid_from, valid_to, salience,
                durability, sensitivity, canonical_key
            ON memories
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_acl_rules_retrieval_revision_insert
            AFTER INSERT ON memory_acl_rules
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_acl_rules_retrieval_revision_update
            AFTER UPDATE ON memory_acl_rules
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_acl_rules_retrieval_revision_delete
            AFTER DELETE ON memory_acl_rules
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_acl_policies_retrieval_revision_insert
            AFTER INSERT ON memory_acl_policies
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_acl_policies_retrieval_revision_update
            AFTER UPDATE ON memory_acl_policies
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_acl_policies_retrieval_revision_delete
            AFTER DELETE ON memory_acl_policies
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_embeddings_retrieval_revision_insert
            AFTER INSERT ON memory_embeddings
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_embeddings_retrieval_revision_update
            AFTER UPDATE ON memory_embeddings
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_embeddings_retrieval_revision_delete
            AFTER DELETE ON memory_embeddings
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_knowledge_nodes_retrieval_revision_insert
            AFTER INSERT ON knowledge_nodes
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_knowledge_nodes_retrieval_revision_update
            AFTER UPDATE ON knowledge_nodes
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_knowledge_nodes_retrieval_revision_delete
            AFTER DELETE ON knowledge_nodes
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_knowledge_edges_retrieval_revision_insert
            AFTER INSERT ON knowledge_edges
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_knowledge_edges_retrieval_revision_update
            AFTER UPDATE ON knowledge_edges
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            CREATE TRIGGER IF NOT EXISTS trg_knowledge_edges_retrieval_revision_delete
            AFTER DELETE ON knowledge_edges
            BEGIN
                UPDATE retrieval_revision SET revision = revision + 1 WHERE singleton = 1;
            END;
            """
        )

    def _ensure_source_query_indexes_sync(self) -> None:
        """Incremental, rebuildable raw-message index and cursor revision."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_query_revision (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO source_query_revision VALUES(1,0);
            CREATE TRIGGER IF NOT EXISTS trg_source_query_revision_ai AFTER INSERT ON timeline
            BEGIN UPDATE source_query_revision SET revision=revision+1 WHERE singleton=1; END;
            CREATE TRIGGER IF NOT EXISTS trg_source_query_revision_ad AFTER DELETE ON timeline
            BEGIN UPDATE source_query_revision SET revision=revision+1 WHERE singleton=1; END;
            CREATE TRIGGER IF NOT EXISTS trg_source_query_revision_au
            AFTER UPDATE OF id,scope,session_id,subject_id,object_id,event_type,content,metadata,occurred_at,created_at ON timeline
            BEGIN UPDATE source_query_revision SET revision=revision+1 WHERE singleton=1; END;
            CREATE INDEX IF NOT EXISTS idx_timeline_source_page
            ON timeline(scope,session_id,julianday(occurred_at) DESC,created_at DESC,id DESC);
        """)
        initialized = self._conn.execute(
            "SELECT value FROM schema_metadata WHERE key='timeline_source_fts_version'"
        ).fetchone()
        index_exists = (
            self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='timeline_source_fts'"
            ).fetchone()
            is not None
        )
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS timeline_source_fts
                USING fts5(content, content='timeline', content_rowid='rowid', tokenize='trigram')
            """)
            self._conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS trg_timeline_source_fts_ai AFTER INSERT ON timeline
                BEGIN INSERT INTO timeline_source_fts(rowid,content) VALUES(new.rowid,new.content); END;
                CREATE TRIGGER IF NOT EXISTS trg_timeline_source_fts_ad AFTER DELETE ON timeline
                BEGIN INSERT INTO timeline_source_fts(timeline_source_fts,rowid,content)
                    VALUES('delete',old.rowid,old.content); END;
                CREATE TRIGGER IF NOT EXISTS trg_timeline_source_fts_au AFTER UPDATE OF content ON timeline
                BEGIN
                    INSERT INTO timeline_source_fts(timeline_source_fts,rowid,content)
                        VALUES('delete',old.rowid,old.content);
                    INSERT INTO timeline_source_fts(rowid,content) VALUES(new.rowid,new.content);
                END;
            """)
            if (
                not index_exists
                or initialized is None
                or initialized[0] != "trigram-v1"
            ):
                self._conn.execute(
                    "INSERT INTO timeline_source_fts(timeline_source_fts) VALUES('rebuild')"
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_metadata(key,value,updated_at) VALUES('timeline_source_fts_version','trigram-v1',?)",
                    (utc_now(),),
                )
            self._source_fts_enabled = True
        except sqlite3.OperationalError as exc:
            if "no such tokenizer" not in str(exc) and "no such module" not in str(exc):
                raise
            self._source_fts_enabled = False

    def _ensure_memory_fts_sync(self) -> None:
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(search_text, memory_id UNINDEXED, tokenize='unicode61')
                """
            )
            self._fts_enabled = True
            # Placeholder rows remain available to the dedicated Bot Personal
            # bridge, but are intentionally absent from the recall index.
            memory_count = int(
                self._conn.execute(
                    f"SELECT COUNT(*) FROM memories WHERE {self._recallable_memory_sql()}"
                ).fetchone()[0]
                or 0
            )
            fts_count = int(
                self._conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0] or 0
            )
            if memory_count != fts_count:
                self._rebuild_memory_fts_sync()
        except sqlite3.Error:
            self._fts_enabled = False

    def _ensure_knowledge_trgm_sync(self) -> None:
        """Maintain a trigram index over knowledge node labels via triggers.

        LIKE '%term%' cannot use a b-tree index, so substring recall over
        ~100k labels needs FTS5 trigram. Triggers keep the index in sync for
        every write path (upsert, cascade delete, clear); a count mismatch at
        startup triggers a full rebuild.
        """
        try:
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_label_trgm
                USING fts5(label, node_id UNINDEXED, tokenize='trigram')
                """
            )
            self._conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS trg_knowledge_label_trgm_ai
                AFTER INSERT ON knowledge_nodes
                BEGIN
                    INSERT INTO knowledge_label_trgm(rowid, node_id, label)
                    VALUES(new.rowid, new.id, new.label);
                END;
                CREATE TRIGGER IF NOT EXISTS trg_knowledge_label_trgm_ad
                AFTER DELETE ON knowledge_nodes
                BEGIN
                    DELETE FROM knowledge_label_trgm WHERE rowid=old.rowid;
                END;
                CREATE TRIGGER IF NOT EXISTS trg_knowledge_label_trgm_au
                AFTER UPDATE OF label ON knowledge_nodes
                BEGIN
                    DELETE FROM knowledge_label_trgm WHERE rowid=old.rowid;
                    INSERT INTO knowledge_label_trgm(rowid, node_id, label)
                    VALUES(new.rowid, new.id, new.label);
                END;
                """
            )
            node_count = int(
                self._conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
                or 0
            )
            trgm_count = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM knowledge_label_trgm"
                ).fetchone()[0]
                or 0
            )
            if node_count != trgm_count:
                self._conn.execute("DELETE FROM knowledge_label_trgm")
                self._conn.execute(
                    """
                    INSERT INTO knowledge_label_trgm(rowid, node_id, label)
                    SELECT rowid, id, label FROM knowledge_nodes
                    """
                )
            self._knowledge_trgm_enabled = True
        except sqlite3.Error:
            self._knowledge_trgm_enabled = False

    def _rebuild_memory_fts_sync(self) -> int:
        if not self._fts_enabled:
            return 0
        self._conn.execute("DELETE FROM memory_fts")
        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE {self._recallable_memory_sql()}"
        ).fetchall()
        count = 0
        for row in rows:
            self._upsert_memory_fts_row(row)
            count += 1
        return count

    async def rebuild_memory_indexes(self) -> dict[str, Any]:
        """Ensure local lexical indexes exist after an external database import."""
        return await asyncio.to_thread(self._rebuild_memory_indexes_sync)

    def _rebuild_memory_indexes_sync(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_memory_fts_sync()
            rebuilt = self._rebuild_memory_fts_sync() if self._fts_enabled else 0
            self._conn.commit()
            return {"fts_enabled": self._fts_enabled, "fts_rebuilt": rebuilt}

    def _upsert_memory_fts_row(self, row: sqlite3.Row | None) -> None:
        if not self._fts_enabled or row is None:
            return
        memory_id = clean_text(row["id"], 120)
        if not memory_id:
            return
        # Do not index storage-only Bot Personal archive references.  This
        # also removes stale entries when an existing row changes source.
        if is_memory_placeholder(MemoryRecord.from_row_light(row)):
            self._conn.execute("DELETE FROM memory_fts WHERE memory_id=?", (memory_id,))
            return
        search_text = self._memory_fts_text(row)
        self._conn.execute("DELETE FROM memory_fts WHERE memory_id=?", (memory_id,))
        if search_text:
            self._conn.execute(
                "INSERT INTO memory_fts(memory_id, search_text) VALUES(?, ?)",
                (memory_id, search_text),
            )

    def _delete_memory_fts_row(self, memory_id: str) -> None:
        if not self._fts_enabled:
            return
        memory_id = clean_text(memory_id, 120)
        if memory_id:
            self._conn.execute("DELETE FROM memory_fts WHERE memory_id=?", (memory_id,))

    def _memory_fts_text(self, row: sqlite3.Row) -> str:
        if is_memory_placeholder(MemoryRecord.from_row_light(row)):
            return ""
        metadata = json_loads(row["metadata"], {})
        if not isinstance(metadata, dict):
            metadata = {}
        tags = json_loads(row["tags"], [])
        if not isinstance(tags, list):
            tags = []
        metadata_parts: list[str] = []
        for key in (
            "canonical_summary",
            "persona_summary",
            "memory_reason",
            "title",
            "topic",
            "fact_key",
        ):
            value = metadata.get(key)
            if isinstance(value, dict):
                value = json_dumps(value)
            if value:
                metadata_parts.append(clean_text(value, 800))
        for key in (
            "key_facts",
            "routine_check_notes",
            "topics",
            "participants",
            "aliases",
            "query_anchors",
        ):
            value = metadata.get(key)
            if isinstance(value, list):
                metadata_parts.extend(
                    clean_text(item, 160) for item in value if clean_text(item, 160)
                )
        parts = [
            row["memory_type"],
            row["subject_id"],
            row["subject_name"],
            row["object_id"],
            row["object_name"],
            row["session_id"],
            row["group_id"],
            row["content"],
            row["evidence"],
            " ".join(clean_text(tag, 80) for tag in tags if clean_text(tag, 80)),
            " ".join(metadata_parts),
        ]
        text = clean_text(" ".join(part for part in parts if part), 8000)
        bigrams = self._cjk_bigrams(text)
        return clean_text(f"{text} {' '.join(bigrams)}", 12000)

    @staticmethod
    def _cjk_bigrams(text: str) -> list[str]:
        compact = re.sub(r"\s+", "", clean_text(text, 8000))
        chunks = re.findall(r"[\u4e00-\u9fff]{2,}", compact)
        result: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            for index in range(0, len(chunk) - 1):
                gram = chunk[index : index + 2]
                if gram not in seen:
                    seen.add(gram)
                    result.append(gram)
                if len(result) >= 512:
                    return result
        return result
