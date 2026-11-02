from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)
from .sensitive_data import redact_sensitive_text, redact_sensitive_value


class MemoryStoreSchemaRepairs:
    """store schema repairs; the facade owns all shared resources."""

    def _cleanup_placeholder_memory_indexes_sync(self) -> int:
        """Remove historical placeholder entries from recall-only indexes.

        The memory rows themselves are intentionally retained because the
        Bot Personal bridge reads their structured metadata.  Deleting stale
        FTS/vector entries is sufficient to keep ordinary recall clean and is
        safe to repeat during startup.
        """
        rows = self._conn.execute(
            f"SELECT id FROM memories WHERE NOT ({self._recallable_memory_sql()})"
        ).fetchall()
        memory_ids = [
            clean_text(row["id"], 120) for row in rows if clean_text(row["id"], 120)
        ]
        if not memory_ids:
            return 0
        deleted = 0
        for index in range(0, len(memory_ids), 500):
            chunk = memory_ids[index : index + 500]
            marks = ",".join("?" for _ in chunk)
            deleted += int(
                self._conn.execute(
                    f"DELETE FROM memory_embeddings WHERE memory_id IN ({marks})",
                    chunk,
                ).rowcount
                or 0
            )
            if self._fts_enabled:
                self._conn.execute(
                    f"DELETE FROM memory_fts WHERE memory_id IN ({marks})",
                    chunk,
                )
        return deleted

    def _backup_before_schema_upgrade_sync(self) -> str:
        """Create a consistent, private rollback copy before mutating an existing schema."""

        tables = {
            clean_text(row["name"], 160)
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        if not tables:
            return ""
        schema_columns = self._table_column_names_sync("schema_metadata")
        if schema_columns == {"key", "value", "updated_at"}:
            row = self._conn.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()
            if row is not None and clean_text(row["value"], 80) == self.SCHEMA_VERSION:
                return ""

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.db_path.with_name(
            f"{self.db_path.stem}.before-{self.SCHEMA_VERSION}.{timestamp}{self.db_path.suffix or '.db'}"
        )
        with closing(sqlite3.connect(str(backup_path))) as backup_conn:
            self._conn.backup(backup_conn)
        backup_path.chmod(0o600)
        return str(backup_path)

    def _table_column_names_sync(self, table: str) -> set[str]:
        return {
            clean_text(row["name"], 160)
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _repair_internal_control_tables_sync(self) -> None:
        """Canonicalize disposable control tables left by interrupted/foreign upgrades."""

        schema_columns = self._table_column_names_sync("schema_metadata")
        if schema_columns != {"key", "value", "updated_at"}:
            remembered_version = ""
            if {"key", "value"}.issubset(schema_columns):
                row = self._conn.execute(
                    "SELECT value FROM schema_metadata WHERE key='schema_version'"
                ).fetchone()
                if row is not None:
                    remembered_version = clean_text(row["value"], 80)
            self._conn.execute("DROP TABLE schema_metadata")
            self._conn.execute(
                """
                CREATE TABLE schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            if remembered_version:
                self._conn.execute(
                    "INSERT INTO schema_metadata(key,value,updated_at) VALUES('schema_version',?,?)",
                    (remembered_version, utc_now()),
                )

        revision_columns = self._table_column_names_sync("retrieval_revision")
        if revision_columns and revision_columns != {"singleton", "revision"}:
            remembered_revision = 0
            if {"singleton", "revision"}.issubset(revision_columns):
                row = self._conn.execute(
                    "SELECT revision FROM retrieval_revision WHERE singleton=1"
                ).fetchone()
                if row is not None:
                    try:
                        remembered_revision = max(0, int(row["revision"] or 0))
                    except (TypeError, ValueError):
                        remembered_revision = 0
            self._conn.execute("DROP TABLE retrieval_revision")
            self._conn.execute(
                """
                CREATE TABLE retrieval_revision (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    revision INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._conn.execute(
                "INSERT INTO retrieval_revision(singleton,revision) VALUES(1,?)",
                (remembered_revision,),
            )

    def _redact_existing_sensitive_rows_sync(self) -> None:
        """Idempotently scrub legacy rows before recall, embedding, or export."""
        marker = self._conn.execute(
            "SELECT value FROM schema_metadata WHERE key='sensitive_redaction_version'"
        ).fetchone()
        if (
            marker is not None
            and clean_text(marker["value"], 80) == self.SENSITIVE_REDACTION_VERSION
        ):
            return
        rekeyed = False
        for row in self._conn.execute(
            "SELECT id, content, evidence, metadata FROM memories"
        ).fetchall():
            content = redact_sensitive_text(row["content"])
            evidence = redact_sensitive_text(row["evidence"])
            metadata = redact_sensitive_value(json_loads(row["metadata"], {}))
            encoded_metadata = json_dumps(metadata)
            if (
                content != row["content"]
                or evidence != row["evidence"]
                or encoded_metadata != row["metadata"]
            ):
                content_changed = content != row["content"]
                self._conn.execute(
                    """UPDATE memories
                       SET content=?, evidence=?, metadata=?,
                           canonical_key=CASE WHEN ? THEN '' ELSE canonical_key END,
                           content_fingerprint=CASE WHEN ? THEN '' ELSE content_fingerprint END
                       WHERE id=?""",
                    (
                        content,
                        evidence,
                        encoded_metadata,
                        content_changed,
                        content_changed,
                        row["id"],
                    ),
                )
                if content_changed:
                    self._conn.execute(
                        "DELETE FROM memory_embeddings WHERE memory_id=?",
                        (row["id"],),
                    )
                    refreshed = self._conn.execute(
                        "SELECT * FROM memories WHERE id=?",
                        (row["id"],),
                    ).fetchone()
                    self._upsert_memory_fts_row(refreshed)
                rekeyed = rekeyed or content_changed
        for row in self._conn.execute(
            "SELECT id, content, metadata FROM timeline"
        ).fetchall():
            content = redact_sensitive_text(row["content"])
            metadata = redact_sensitive_value(json_loads(row["metadata"], {}))
            encoded_metadata = json_dumps(metadata)
            if content != row["content"] or encoded_metadata != row["metadata"]:
                self._conn.execute(
                    "UPDATE timeline SET content=?, metadata=? WHERE id=?",
                    (content, encoded_metadata, row["id"]),
                )
        self._redact_legacy_auxiliary_tables_sync()
        if rekeyed:
            self._backfill_memory_atom_v2_sync()
        self._conn.execute(
            """INSERT INTO schema_metadata(key,value,updated_at) VALUES('sensitive_redaction_version',?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (self.SENSITIVE_REDACTION_VERSION, utc_now()),
        )

    def _remove_legacy_redaction_tracking_triggers_sync(self) -> None:
        """Remove legacy triggers that made every safe write rescan the full store.

        Store write APIs redact text and metadata before persistence. The old
        triggers could not distinguish those safe writes from legacy data, so
        one new chat message invalidated the marker and forced an O(N) scan on
        the next startup.
        """
        self._conn.executescript(
            """
            DROP TRIGGER IF EXISTS trg_redaction_memories_insert;
            DROP TRIGGER IF EXISTS trg_redaction_memories_update;
            DROP TRIGGER IF EXISTS trg_redaction_timeline_insert;
            DROP TRIGGER IF EXISTS trg_redaction_timeline_update;
            """
        )

    def _redact_legacy_auxiliary_tables_sync(self) -> None:
        """Scrub older secondary projections that can surface memory text."""
        secret_node_ids: list[str] = []
        for row in self._conn.execute(
            "SELECT id,label FROM knowledge_nodes"
        ).fetchall():
            if redact_sensitive_text(row["label"]) != row["label"]:
                secret_node_ids.append(row["id"])
        if secret_node_ids:
            placeholders = ",".join("?" for _ in secret_node_ids)
            self._conn.execute(
                f"DELETE FROM knowledge_edges WHERE source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders})",
                [*secret_node_ids, *secret_node_ids],
            )
            self._conn.execute(
                f"DELETE FROM knowledge_nodes WHERE id IN ({placeholders})",
                secret_node_ids,
            )

        secret_fact_ids: list[str] = []
        for row in self._conn.execute(
            "SELECT id,claim_summary FROM portrait_facts"
        ).fetchall():
            if redact_sensitive_text(row["claim_summary"]) != row["claim_summary"]:
                secret_fact_ids.append(row["id"])
        if secret_fact_ids:
            placeholders = ",".join("?" for _ in secret_fact_ids)
            self._conn.execute(
                f"DELETE FROM portrait_learning_queue WHERE fact_id IN ({placeholders})",
                secret_fact_ids,
            )
            self._conn.execute(
                f"DELETE FROM portrait_facts WHERE id IN ({placeholders})",
                secret_fact_ids,
            )

        table_columns = {
            "identities": ((), ("profile",)),
            "summary_failures": (("last_error",), ("metadata",)),
            "chat_import_batches": (("error",), ("speaker_map", "options", "stats")),
            "chat_import_segments": (("transcript", "error"), ("result",)),
            "relationship_edges": (("evidence",), ("metadata",)),
            "knowledge_nodes": (("label",), ("metadata",)),
            "knowledge_edges": (("evidence",), ("metadata",)),
            "cross_window_threads": (("topic", "content"), ("metadata",)),
            "injection_logs": (("query",), ("blocked_reasons",)),
            "portrait_people": ((), ("capability_summary",)),
            "portrait_facts": (("claim_summary",), ("context_refs",)),
            "portrait_suppressions": (("reason",), ()),
            "portrait_operations": ((), ("snapshot",)),
        }
        for table, (text_columns, json_columns) in table_columns.items():
            columns = [*text_columns, *json_columns]
            if not columns:
                continue
            selected = ",".join(columns)
            rows = self._conn.execute(
                f"SELECT rowid AS _redact_rowid,{selected} FROM {table}"
            ).fetchall()
            for row in rows:
                values: list[str] = []
                changed = False
                for column in text_columns:
                    raw = str(row[column] or "")
                    safe = redact_sensitive_text(raw)
                    values.append(safe)
                    changed = changed or safe != raw
                for column in json_columns:
                    raw = str(row[column] or "")
                    decoded = json_loads(raw, {})
                    safe = json_dumps(redact_sensitive_value(decoded))
                    values.append(safe)
                    changed = changed or safe != raw
                if changed:
                    assignments = ",".join(f"{column}=?" for column in columns)
                    self._conn.execute(
                        f"UPDATE {table} SET {assignments} WHERE rowid=?",
                        [*values, row["_redact_rowid"]],
                    )

    def normalize_legacy_manual_visibility(self) -> int:
        """收回早期版本中过宽的手动记忆默认可见性。"""
        with self._lock:
            with self._transaction_sync():
                return self._normalize_legacy_manual_visibility_sync()

    def normalize_internal_bot_self_scopes(self) -> int:
        """Move legacy internal Bot memories out of user-facing private scopes."""
        with self._lock:
            with self._transaction_sync():
                return self._normalize_internal_bot_self_scopes_sync()

    def _normalize_legacy_manual_visibility_sync(self) -> int:
        private_cur = self._conn.execute(
            """
            UPDATE memories
            SET visibility='private_pair', updated_at=?
            WHERE memory_type='manual_memory' AND visibility='shareable' AND scope='private'
            """,
            (utc_now(),),
        )
        group_cur = self._conn.execute(
            """
            UPDATE memories
            SET visibility='group_public', updated_at=?
            WHERE memory_type='manual_memory' AND visibility='shareable' AND scope='group'
            """,
            (utc_now(),),
        )
        unknown_cur = self._conn.execute(
            """
            UPDATE memories
            SET visibility='internal', updated_at=?
            WHERE memory_type='manual_memory' AND visibility='shareable' AND scope NOT IN ('private', 'group')
            """,
            (utc_now(),),
        )
        return (
            int(private_cur.rowcount or 0)
            + int(group_cur.rowcount or 0)
            + int(unknown_cur.rowcount or 0)
        )

    def _normalize_internal_bot_self_scopes_sync(self) -> int:
        rows = self._conn.execute(
            """
            SELECT * FROM memories
            WHERE scope='private'
              AND visibility='bot_self'
              AND source_plugin='private_companion'
              AND (
                    session_id='private_companion:dream'
                    OR id LIKE 'private_companion_dream_%'
                    OR tags LIKE '%"dream_fragment"%'
              )
            """,
        ).fetchall()
        now = utc_now()
        for row in rows:
            record = MemoryRecord.from_row(row)
            record.scope = "unknown"
            record.group_id = ""
            record.content_fingerprint = ""
            record.ensure_defaults()
            self._conn.execute(
                """
                UPDATE memories
                SET scope='unknown', group_id='', content_fingerprint=?, updated_at=?
                WHERE id=?
                """,
                (record.content_fingerprint, now, record.id),
            )
        return len(rows)

    def _ensure_memory_columns_sync(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        salience_added = "salience" not in existing
        columns_added = False
        additions = {
            "content_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "merged_count": "INTEGER NOT NULL DEFAULT 1",
            "supersedes_id": "TEXT NOT NULL DEFAULT ''",
            "owner_bot_id": "TEXT NOT NULL DEFAULT ''",
            "validity_status": (
                "TEXT NOT NULL DEFAULT 'active' "
                "CHECK(validity_status IN ('active','superseded','expired','archived','deleted','quarantined'))"
            ),
            "valid_from": "TEXT NOT NULL DEFAULT ''",
            "valid_to": "TEXT NOT NULL DEFAULT ''",
            # SQLite cannot reliably add a NOT NULL column with a fractional
            # default to a populated legacy table.  Use an integer default for
            # the ALTER TABLE path; _backfill_memory_atom_v2_sync restores the
            # legacy metadata/importance-derived value immediately afterwards.
            "salience": "REAL NOT NULL DEFAULT 0 CHECK(salience >= 0 AND salience <= 1)",
            "durability": (
                "TEXT NOT NULL DEFAULT 'normal' "
                "CHECK(durability IN ('ephemeral','short','normal','durable','pinned'))"
            ),
            "sensitivity": (
                "TEXT NOT NULL DEFAULT 'private' "
                "CHECK(sensitivity IN ('public','internal','private','restricted'))"
            ),
            "reinforcement_score": (
                "REAL NOT NULL DEFAULT 0 CHECK(reinforcement_score >= 0 AND reinforcement_score <= 1)"
            ),
            "injection_count": "INTEGER NOT NULL DEFAULT 0 CHECK(injection_count >= 0)",
            "last_injected_at": "TEXT NOT NULL DEFAULT ''",
            "canonical_key": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {ddl}")
                columns_added = True

        marker = self._conn.execute(
            "SELECT value FROM schema_metadata WHERE key='memory_atom_v2_backfill_version'"
        ).fetchone()
        needs_backfill = (
            columns_added
            or marker is None
            or clean_text(marker["value"], 40) != self.MEMORY_ATOM_BACKFILL_VERSION
        )
        if not needs_backfill:
            return

        self._backfill_memory_atom_v2_sync(restore_legacy_salience=salience_added)
        self._conn.execute(
            """
            INSERT INTO schema_metadata(key,value,updated_at)
            VALUES('memory_atom_v2_backfill_version',?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (self.MEMORY_ATOM_BACKFILL_VERSION, utc_now()),
        )

    def _backfill_memory_atom_v2_sync(
        self, *, restore_legacy_salience: bool = False
    ) -> int:
        """Populate v2 atom columns without rewriting legacy content or timestamps."""

        rows = self._conn.execute("SELECT * FROM memories").fetchall()
        updated = 0
        columns = (
            "owner_bot_id",
            "validity_status",
            "valid_from",
            "valid_to",
            "salience",
            "durability",
            "sensitivity",
            "reinforcement_score",
            "injection_count",
            "last_injected_at",
            "canonical_key",
            "content_fingerprint",
        )
        for row in rows:
            record = MemoryRecord.from_row(row)
            if restore_legacy_salience and not row["salience"]:
                metadata = json_loads(row["metadata"], {})
                if isinstance(metadata, dict):
                    # The migration default is deliberately zero for SQLite
                    # compatibility; recover the legacy semantic default or
                    # explicit metadata value before writing the atom fields.
                    record.salience = metadata.get("salience", record.importance)
            values = record.to_db()
            current = tuple(row[name] for name in columns)
            desired = tuple(values[name] for name in columns)
            if current == desired:
                continue
            self._conn.execute(
                """
                UPDATE memories
                SET owner_bot_id=?, validity_status=?, valid_from=?, valid_to=?,
                    salience=?, durability=?, sensitivity=?, reinforcement_score=?,
                    injection_count=?, last_injected_at=?, canonical_key=?,
                    content_fingerprint=?
                WHERE id=?
                """,
                [*desired, record.id],
            )
            updated += 1
        return updated

    def _ensure_timeline_columns_sync(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(timeline)").fetchall()
        }
        additions = {
            "summarized_at": "TEXT NOT NULL DEFAULT ''",
            "message_id": "TEXT NOT NULL DEFAULT ''",
            "dedupe_key": "TEXT NOT NULL DEFAULT ''",
            "retention_class": "TEXT NOT NULL DEFAULT 'normal'",
            "import_batch_id": "TEXT NOT NULL DEFAULT ''",
            "source_sequence": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, ddl in additions.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE timeline ADD COLUMN {name} {ddl}")

    def _ensure_acl_columns_sync(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(memory_acl_rules)"
            ).fetchall()
        }
        if "effect" not in existing:
            self._conn.execute(
                "ALTER TABLE memory_acl_rules ADD COLUMN effect TEXT NOT NULL DEFAULT 'allow'"
            )
        policy_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(memory_acl_policies)"
            ).fetchall()
        }
        if "capture_enabled" not in policy_columns:
            self._conn.execute(
                "ALTER TABLE memory_acl_policies ADD COLUMN capture_enabled INTEGER"
            )
        if "recall_enabled" not in policy_columns:
            self._conn.execute(
                "ALTER TABLE memory_acl_policies ADD COLUMN recall_enabled INTEGER"
            )

    def _ensure_portrait_columns_sync(self) -> None:
        existing = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(portrait_evidence)"
            ).fetchall()
        }
        if "statement_fingerprint" not in existing:
            self._conn.execute(
                "ALTER TABLE portrait_evidence ADD COLUMN statement_fingerprint TEXT NOT NULL DEFAULT ''"
            )
