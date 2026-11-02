from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    utc_now,
)
from .store_support import (
    _timeline_time_sql,
)


class MemoryStoreSchema:
    """Schema; state and transaction ownership remain in the facade."""

    def initialize(self) -> None:
        if self._read_only:
            with self._lock:
                memories = self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
                ).fetchone()
                if memories is None:
                    raise sqlite3.DatabaseError(
                        "memory database schema is missing the memories table"
                    )
                self._fts_enabled = bool(
                    self._conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_fts'"
                    ).fetchone()
                )
                self._source_fts_enabled = bool(
                    self._conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='timeline_source_fts'"
                    ).fetchone()
                )
            return
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self.last_schema_backup_path = self._backup_before_schema_upgrade_sync()
            self._conn.executescript(
                (Path(__file__).parent / "sql" / "memory_schema.sql").read_text(
                    encoding="utf-8"
                )
            )
            self._repair_internal_control_tables_sync()
            self._ensure_memory_columns_sync()
            self._ensure_timeline_columns_sync()
            self._ensure_acl_columns_sync()
            self._ensure_portrait_columns_sync()
            self._ensure_memory_fts_sync()
            self._ensure_knowledge_trgm_sync()
            self._remove_legacy_redaction_tracking_triggers_sync()
            self._ensure_retrieval_revision_sync()
            self._ensure_source_query_indexes_sync()
            from .source_watches import initialize as initialize_life_source_watches

            initialize_life_source_watches(self._conn)
            from .source_capture import initialize as initialize_capture

            initialize_capture(self._conn)
            from .source_semantic import initialize as initialize_source_semantic

            initialize_source_semantic(self._conn)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_correction_receipts (
                    scope_ref TEXT NOT NULL, correction_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, receipt TEXT NOT NULL,
                    PRIMARY KEY(scope_ref, correction_id)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_fingerprint ON memories(content_fingerprint)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_atom_domain "
                "ON memories(owner_bot_id, platform, scope, validity_status)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_validity_time "
                "ON memories(validity_status, valid_from, valid_to)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_canonical_key "
                "ON memories(canonical_key)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_candidate "
                "ON memories(importance DESC, occurred_at DESC, id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_summary ON timeline(session_id, summarized_at, occurred_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_scope_recent ON timeline(scope, occurred_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_subject_recent "
                "ON timeline(scope, session_id, subject_id, occurred_at DESC, created_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_object_recent "
                "ON timeline(scope, session_id, object_id, occurred_at DESC, created_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_retention ON timeline(occurred_at, created_at) WHERE summarized_at!=''"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_import_batch ON timeline(import_batch_id, source_sequence)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_injection_logs_created ON injection_logs(created_at)"
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_timeline_dedupe_key ON timeline(dedupe_key) WHERE dedupe_key!=''"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_summary_failures_updated ON summary_failures(updated_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_import_batches_state ON chat_import_batches(state, updated_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_import_segments_state ON chat_import_segments(batch_id, status, segment_index)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_import_batch ON memories(import_batch_id, memory_type)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_acl_owner ON memory_acl_rules(owner_scope, owner_id, enabled)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_acl_reader ON memory_acl_rules(reader_scope, reader_id, enabled)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_acl_policy_window ON memory_acl_policies(window_scope, window_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_provider ON memory_embeddings(provider_id, updated_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotion_events_trace ON emotion_events(trace_id, revision)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotion_events_session ON emotion_events(bot_id, scope, session_id, occurred_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotion_events_delivery_domain "
                "ON emotion_events(bot_id, scope, platform, occurred_at DESC, event_id DESC, revision DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotion_events_dedupe ON emotion_events(dedupe_key, event_type)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotion_event_deliveries_pending "
                "ON emotion_event_deliveries(consumer_id, acked_at, last_delivered_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_portrait_evidence_person ON portrait_evidence(person_id, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_portrait_facts_person ON portrait_facts(person_id, status, sensitivity, updated_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_portrait_scope_capabilities_person "
                "ON portrait_scope_capabilities(person_id, updated_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_portrait_suppressions_person ON portrait_suppressions(person_id, status)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_portrait_learning_queue_person ON portrait_learning_queue(person_id, state, updated_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_profile_domain "
                "ON memories(platform, subject_kind, subject_id, object_kind, "
                "object_id, scope, group_id, visibility, memory_type, lifecycle)"
            )
            # c1: 画像治理/修复按 fact_id 逐条查询，原无索引导致逐 fact 全表扫描
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_portrait_learning_queue_fact "
                "ON portrait_learning_queue(fact_id, state)"
            )
            # c2: 回滚/删除按 source_memory_id 批量 IN 查询，原无索引导致全表扫描
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationship_edges_source_memory "
                "ON relationship_edges(source_memory_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_edges_source_memory "
                "ON knowledge_edges(source_memory_id)"
            )
            # c3: 仅按 session_id 过滤 emotion 事件时现有索引（前缀 bot_id）无法命中
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotion_events_session "
                "ON emotion_events(session_id, occurred_at DESC)"
            )
            # c4: timeline 仅按 session_id（无 scope）过滤时无索引命中；
            #     memories 时间窗口查询的 created_at/updated_at 无索引
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_session_recent "
                "ON timeline(session_id, occurred_at DESC, created_at DESC)"
            )
            event_time_sql = _timeline_time_sql()
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timeline_session_event_time "
                f"ON timeline(session_id, scope, {event_time_sql}, created_at, id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories(updated_at)"
            )
            # c5: emotion 投递查询使用 LOWER(scope) 导致 scope 列索引失效，
            #     用表达式索引精确匹配 LOWER(scope) 谓词
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emotion_events_delivery_scope "
                "ON emotion_events(bot_id, LOWER(scope), platform)"
            )
            self._redact_existing_sensitive_rows_sync()
            self._cleanup_placeholder_memory_indexes_sync()
            self._conn.execute(
                """INSERT INTO schema_metadata(key,value,updated_at) VALUES('schema_version',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (self.SCHEMA_VERSION, utc_now()),
            )
            self._initialize_summary_batches()
            self._initialize_recallable_memory_view()
            self._conn.commit()
            self._migrate_summary_batch_retry_state_sync()
            self.rebuild_acl_projection_sync()
            self._acl_projection_revision = self._memory_revision_sync()
