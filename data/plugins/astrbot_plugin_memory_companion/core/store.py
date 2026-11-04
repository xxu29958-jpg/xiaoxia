from __future__ import annotations

import asyncio
import sqlite3
import threading
import traceback
from collections.abc import Callable
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from .astrbot_compat import logger
from .models import (
    MemoryRecord,
    clean_text,
    utc_now,
)
from .store_acl_policies import MemoryStoreAclPolicies
from .store_candidate_queries import MemoryStoreCandidateQueries
from .store_conversation_threads import MemoryStoreConversationThreads
from .store_embeddings import MemoryStoreEmbeddings
from .store_emotion_delivery import MemoryStoreEmotionDelivery
from .store_emotion_records import MemoryStoreEmotionRecords
from .store_import_batches import MemoryStoreImportBatches
from .store_import_rebind import MemoryStoreImportRebind
from .store_import_repair import MemoryStoreImportRepair
from .store_knowledge_graph import MemoryStoreKnowledgeGraph
from .store_maintenance import MemoryStoreMaintenance
from .store_portrait_facts import MemoryStorePortraitFacts
from .store_portrait_governance import MemoryStorePortraitGovernance
from .store_portrait_learning import MemoryStorePortraitLearning
from .store_portrait_projection import MemoryStorePortraitProjection
from .store_profile_assertions import MemoryStoreProfileAssertions
from .store_profile_evidence import MemoryStoreProfileEvidence
from .store_profile_repair_apply import MemoryStoreProfileRepairApply
from .store_profile_repair_rollback import MemoryStoreProfileRepairRollback
from .store_profile_repair_snapshots import MemoryStoreProfileRepairSnapshots
from .store_record_insert import MemoryStoreRecordInsert
from .store_record_queries import MemoryStoreRecordQueries
from .store_record_state import MemoryStoreRecordState
from .store_record_updates import MemoryStoreRecordUpdates
from .store_relationships import MemoryStoreRelationships
from .store_schema import MemoryStoreSchema
from .store_schema_repairs import MemoryStoreSchemaRepairs
from .store_scope_erasure import MemoryStoreScopeErasure
from .store_search_indexes import MemoryStoreSearchIndexes
from .store_source_queries import MemoryStoreSourceQueries
from .store_support import _ACL_UNSET as _ACL_UNSET
from .store_support import _EMBEDDING_VECTOR_FMT as _EMBEDDING_VECTOR_FMT
from .store_support import SharedTextCache as SharedTextCache
from .store_support import _memory_time_sql as _memory_time_sql
from .store_support import (
    _normalize_embedding_vector_values as _normalize_embedding_vector_values,
)
from .store_support import _pack_embedding_vector as _pack_embedding_vector
from .store_support import _timeline_order_key as _timeline_order_key
from .store_support import _timeline_time_sql as _timeline_time_sql
from .store_support import _unpack_embedding_vector as _unpack_embedding_vector
from .store_timeline_capture import MemoryStoreTimelineCapture
from .summary_batches import SummaryBatchStore


class MemoryStore(
    MemoryStoreAclPolicies,
    MemoryStoreSchema,
    MemoryStoreSchemaRepairs,
    MemoryStoreSearchIndexes,
    MemoryStorePortraitProjection,
    MemoryStorePortraitFacts,
    MemoryStorePortraitGovernance,
    MemoryStorePortraitLearning,
    MemoryStoreScopeErasure,
    MemoryStoreProfileEvidence,
    MemoryStoreProfileAssertions,
    MemoryStoreProfileRepairSnapshots,
    MemoryStoreProfileRepairApply,
    MemoryStoreProfileRepairRollback,
    MemoryStoreRecordInsert,
    MemoryStoreRelationships,
    MemoryStoreKnowledgeGraph,
    MemoryStoreTimelineCapture,
    MemoryStoreImportBatches,
    MemoryStoreImportRebind,
    MemoryStoreImportRepair,
    MemoryStoreSourceQueries,
    MemoryStoreConversationThreads,
    MemoryStoreCandidateQueries,
    MemoryStoreRecordQueries,
    MemoryStoreRecordUpdates,
    MemoryStoreRecordState,
    MemoryStoreMaintenance,
    MemoryStoreEmbeddings,
    MemoryStoreEmotionRecords,
    MemoryStoreEmotionDelivery,
    SummaryBatchStore,
):
    """Own shared runtime resources; domain classes never create separate state."""

    EMBEDDING_CANDIDATE_CACHE_MAX = 64

    MAINTENANCE_REPAIR_BATCH_SIZE = 200

    MAINTENANCE_FINGERPRINT_REPAIR_VERSION = "v1"

    MEMORY_ATOM_BACKFILL_VERSION = "v1"

    SCHEMA_VERSION = "memory-atom-v2-core-state-v1"

    MAINTENANCE_REPAIR_BATCH_SIZE = 200

    MAINTENANCE_FINGERPRINT_REPAIR_VERSION = "v1"

    MEMORY_ATOM_BACKFILL_VERSION = "v1"

    SENSITIVE_REDACTION_VERSION = "storage-redaction-v1"

    PROFILE_MEMORY_TYPES = frozenset({"user_profile", "user_preference", "user_habit"})

    PROFILE_RULE_EXTRACTORS = frozenset({"rule_v1", "rule_v2"})

    PROFILE_RULE_PORTRAIT_VERSIONS = frozenset({"req036.rule.v1", "req036.rule.v2"})

    @classmethod
    def _recallable_memory_sql(cls, alias: str = "") -> str:
        normalized_alias = clean_text(alias, 40)
        prefix = f"{normalized_alias}." if normalized_alias else ""
        return (
            f"{prefix}source_plugin != 'bot_personal_bridge' AND "
            f"lower({prefix}content) NOT LIKE 'bot personal archive reference [%]'"
        )

    RECALLABLE_MEMORY_VIEW = "recallable_memories"

    def _initialize_recallable_memory_view(self) -> None:
        """Expose the recall filter as a view so no query can omit it.

        ``_recallable_memory_sql`` returns a bare predicate that every caller
        has to remember to splice into its own ``WHERE`` clause, which is why
        list-style queries silently drifted away from recall queries.  The
        view carries the same rule at the data entry point instead, and is
        recreated from the code predicate so the two can never disagree.
        """
        self._conn.execute(f"DROP VIEW IF EXISTS {self.RECALLABLE_MEMORY_VIEW}")
        self._conn.execute(
            f"CREATE VIEW {self.RECALLABLE_MEMORY_VIEW} AS "
            f"SELECT * FROM memories WHERE {self._recallable_memory_sql()}"
        )

    PROFILE_SINGLE_VALUE_DIMENSIONS = frozenset(
        {
            "preferred_address",
            "name",
            "birthday",
            "birth_date",
            "occupation",
            "profession",
            "education",
            "major",
            "zodiac",
            "zodiac_or_blood_type",
            "blood_type",
        }
    )

    def __init__(self, db_path: Path, *, read_only: bool = False):
        self.db_path = Path(db_path)
        self._read_only = bool(read_only)
        if self._read_only:
            if not self.db_path.is_file():
                raise FileNotFoundError(
                    f"memory database does not exist: {self.db_path}"
                )
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._open_connection()
        self._lock = threading.RLock()
        # 专用只读连接（WAL 下与主连接写入真正并行）：候选加载等纯读操作
        # 不再排队等主连接写锁，对应 optimization_plan.md §6.1。
        self._read_conn: sqlite3.Connection | None = None
        self._read_lock = threading.RLock()
        self._operation_condition = threading.Condition()
        self._active_tracked_operations = 0
        self._operation_waiters = 0
        self._operation_owner_stack = ""
        self._record_operation_stacks = False
        self._closing = False
        self._closed = False
        self._fts_enabled = False
        self._knowledge_trgm_enabled = False
        self._source_fts_enabled = False
        self._savepoint_counter = 0
        self._embedding_candidate_cache_revision = ""
        self._embedding_candidate_cache: dict[
            tuple[str, bool, int],
            list[tuple[MemoryRecord, list[float], str]],
        ] = {}
        self._vector_search_lock = threading.RLock()
        self._vector_search_cache: dict[tuple, Any] = {}
        self._acl_feature_override_cache: dict[
            tuple[str, str], tuple[bool | None, bool | None]
        ] = {}
        # `memory_acl_policies` is administrator configuration: one row per
        # window, no hot writes.  The in-process projection above is therefore
        # authoritative for the synchronous hot path, refreshed by the write
        # path after its transaction commits and reconciled in the background.
        self._acl_projection_pending: dict[
            tuple[str, str], tuple[bool | None, bool | None]
        ] = {}
        self._acl_projection_lock = threading.Lock()
        self._acl_projection_revision = ""
        self._cache_invalidation_lock = threading.Lock()
        self._cache_invalidation_callbacks: dict[str, Callable[[], None]] = {}
        self.haystack_cache = SharedTextCache(4096)
        self._last_wal_health: dict[str, Any] = {}
        self._last_database_error: dict[str, Any] = {}
        self._database_recovery_attempts = 0
        self._database_recovery_successes = 0
        self.last_schema_backup_path = ""

    def _open_connection(self) -> sqlite3.Connection:
        database = str(self.db_path)
        uri = False
        if self._read_only:
            database = f"{self.db_path.resolve().as_uri()}?mode=ro"
            uri = True
        connection = sqlite3.connect(
            database,
            timeout=3.0,
            check_same_thread=False,
            uri=uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=3000")
        if not self._read_only:
            connection.execute("PRAGMA wal_autocheckpoint=500")
        return connection

    def _ensure_read_connection(self) -> sqlite3.Connection:
        """Lazily open a dedicated read-only connection.

        Under WAL this reader runs concurrently with the main connection's
        writes, so pure-read paths (candidate loading) stop queueing behind
        the main write lock. Read-only stores simply reuse the main
        connection, which is already opened with mode=ro.
        """
        if self._read_only:
            return self._conn
        conn = self._read_conn
        if conn is None:
            with self._read_lock:
                conn = self._read_conn
                if conn is None:
                    conn = sqlite3.connect(
                        f"{self.db_path.resolve().as_uri()}?mode=ro",
                        timeout=3.0,
                        check_same_thread=False,
                        uri=True,
                    )
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA busy_timeout=3000")
                    self._read_conn = conn
        return conn

    def _read_connection_for_bundle(self) -> tuple[sqlite3.Connection, threading.RLock]:
        if self._read_only:
            return self._conn, self._lock
        return self._ensure_read_connection(), self._read_lock

    @staticmethod
    def _is_database_path_error(error: BaseException) -> bool:
        if not isinstance(error, sqlite3.OperationalError):
            return False
        message = str(error).strip().lower()
        return (
            "unable to open database file" in message
            or "cannot open database" in message
        )

    def _recover_connection_sync(self) -> None:
        """Replace a broken connection without ever creating an empty database."""
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("memory database is closed")
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.db_path.is_file():
                raise sqlite3.OperationalError(
                    f"memory database file is missing: {self.db_path}; restart or restore the data directory"
                )
            replacement = self._open_connection()
            try:
                replacement.execute("PRAGMA journal_mode=WAL")
                replacement.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            except BaseException:
                replacement.close()
                raise
            previous = self._conn
            self._conn = replacement
            try:
                previous.close()
            except sqlite3.Error:
                pass

    def _run_with_database_recovery_sync(self, operation: Any, *args: Any) -> Any:
        try:
            return operation(*args)
        except sqlite3.OperationalError as error:
            if not self._is_database_path_error(error):
                raise
            self._database_recovery_attempts += 1
            error_details = {
                "recorded_at": utc_now(),
                "message": clean_text(error, 500),
                "sqlite_errorcode": getattr(error, "sqlite_errorcode", None),
                "sqlite_errorname": clean_text(
                    getattr(error, "sqlite_errorname", ""), 80
                ),
                **self._database_file_snapshot(),
            }
            self._last_database_error = dict(error_details)
            try:
                health = self._wal_health_sync(checkpoint=False)
            except Exception as health_error:
                health = {
                    "health_error": clean_text(health_error, 500),
                    **self._database_file_snapshot(),
                }
            self._last_wal_health = {
                **health,
                "last_database_error": dict(error_details),
                "database_recovery_attempts": self._database_recovery_attempts,
                "database_recovery_successes": self._database_recovery_successes,
            }
            with self._lock:
                if self._conn.in_transaction:
                    self._conn.rollback()
            self._recover_connection_sync()
            self._database_recovery_successes += 1
            return operation(*args)

    async def _run_recoverable_database_operation(
        self, operation: Any, *args: Any
    ) -> Any:
        return await asyncio.to_thread(
            self._guard_operation_sync,
            self._run_with_database_recovery_sync,
            operation,
            *args,
        )

    def _guard_operation_sync(self, operation: Any, *args: Any) -> Any:
        """Run a SQLite operation while participating in the close barrier."""
        with self._operation_condition:
            if self._closing or self._closed:
                raise sqlite3.OperationalError("database is closing")
            self._active_tracked_operations += 1
            self._operation_owner_stack = (
                "".join(traceback.format_stack(limit=8))
                if self._record_operation_stacks
                else ""
            )
        try:
            return operation(*args)
        finally:
            with self._operation_condition:
                self._active_tracked_operations -= 1
                self._operation_owner_stack = ""
                if self._operation_waiters:
                    self._operation_condition.notify_all()

    async def _run_tracked_operation(
        self,
        operation: Any,
        *args: Any,
        closed_result: Any = None,
    ) -> Any:
        """Keep a queued executor write alive until store shutdown can flush it."""

        with self._operation_condition:
            if self._closing or self._closed:
                return closed_result
            self._active_tracked_operations += 1

        def run() -> Any:
            try:
                return operation(*args)
            finally:
                with self._operation_condition:
                    self._active_tracked_operations -= 1
                    self._operation_condition.notify_all()

        try:
            future = asyncio.get_running_loop().run_in_executor(None, run)
        except BaseException:
            with self._operation_condition:
                self._active_tracked_operations -= 1
                self._operation_condition.notify_all()
            raise
        return await asyncio.shield(future)

    def _database_file_snapshot(self) -> dict[str, Any]:
        def size(path: Path) -> int:
            try:
                return int(path.stat().st_size)
            except OSError:
                return -1

        wal_path = Path(str(self.db_path) + "-wal")
        shm_path = Path(str(self.db_path) + "-shm")
        return {
            "db_exists": self.db_path.is_file(),
            "db_bytes": size(self.db_path),
            "wal_exists": wal_path.is_file(),
            "wal_bytes": size(wal_path),
            "shm_exists": shm_path.is_file(),
            "shm_bytes": size(shm_path),
        }

    async def wal_health(self, *, checkpoint: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._guard_operation_sync, self._wal_health_sync, checkpoint
        )

    def _wal_health_sync(self, checkpoint: bool = False) -> dict[str, Any]:
        result = self._database_file_snapshot()
        result.update(
            {
                "checkpoint_attempted": False,
                "checkpoint_busy": None,
                "checkpoint_log_frames": None,
                "checkpointed_frames": None,
            }
        )
        with self._lock:
            result["in_transaction"] = bool(self._conn.in_transaction)
            result["busy_timeout_ms"] = int(
                self._conn.execute("PRAGMA busy_timeout").fetchone()[0] or 0
            )
            result["wal_autocheckpoint_pages"] = int(
                self._conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] or 0
            )
            if checkpoint and not self._conn.in_transaction:
                row = self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                result["checkpoint_attempted"] = True
                if row is not None and len(row) >= 3:
                    result["checkpoint_busy"] = int(row[0] or 0)
                    result["checkpoint_log_frames"] = int(row[1] or 0)
                    result["checkpointed_frames"] = int(row[2] or 0)
        result.update(
            {
                f"after_{key}": value
                for key, value in self._database_file_snapshot().items()
            }
        )
        result["database_recovery_attempts"] = self._database_recovery_attempts
        result["database_recovery_successes"] = self._database_recovery_successes
        if self._last_database_error:
            result["last_database_error"] = dict(self._last_database_error)
        self._last_wal_health = dict(result)
        return result

    async def wal_checkpoint_truncate(
        self, *, min_wal_bytes: int = 8 * 1024 * 1024
    ) -> dict[str, Any]:
        """Best-effort TRUNCATE checkpoint to bound the WAL file size.

        A long-running WAL reader (the dedicated candidate-load connection) can
        keep the passive auto-checkpoint from ever truncating the ``-wal`` file,
        so it grows to its high-water mark and stays there — a 146MB WAL was
        observed in production, which slows every subsequent read. This runs a
        ``wal_checkpoint(TRUNCATE)`` on the main connection only when the WAL is
        above ``min_wal_bytes`` and no write transaction is active; a busy
        result is not an error.
        """
        return await asyncio.to_thread(
            self._guard_operation_sync,
            self._wal_checkpoint_truncate_sync,
            min_wal_bytes,
        )

    def _wal_checkpoint_truncate_sync(self, min_wal_bytes: int) -> dict[str, Any]:
        result = self._database_file_snapshot()
        result.update(
            {
                "checkpoint_attempted": False,
                "checkpoint_mode": "truncate",
                "checkpoint_busy": None,
                "checkpoint_log_frames": None,
                "checkpointed_frames": None,
            }
        )
        if self._read_only:
            result["skipped"] = "read_only"
            return result
        if result.get("wal_bytes", -1) < max(0, int(min_wal_bytes)):
            result["skipped"] = "below_threshold"
            return result
        with self._lock:
            if self._conn.in_transaction:
                result["skipped"] = "in_transaction"
                return result
            try:
                row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                result["checkpoint_attempted"] = True
                if row is not None and len(row) >= 3:
                    result["checkpoint_busy"] = int(row[0] or 0)
                    result["checkpoint_log_frames"] = int(row[1] or 0)
                    result["checkpointed_frames"] = int(row[2] or 0)
            except sqlite3.Error as exc:
                result["checkpoint_error"] = clean_text(exc, 200)
        result.update(
            {
                f"after_{key}": value
                for key, value in self._database_file_snapshot().items()
            }
        )
        return result

    @contextmanager
    def _transaction_sync(self):
        """Run a write unit atomically; callers must hold ``self._lock``.

        Projection updates staged by write paths are published only after the
        outermost commit succeeds, so a rolled back unit (including a single
        failed item isolated by a SAVEPOINT) can never leak an uncommitted
        value into the in-process projections.
        """
        if self._conn.in_transaction:
            self._savepoint_counter += 1
            savepoint = f"memory_companion_{self._savepoint_counter}"
            with self._acl_projection_lock:
                staged_before = dict(self._acl_projection_pending)
            self._conn.execute(f"SAVEPOINT {savepoint}")
            try:
                yield
            except BaseException:
                self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                self._restore_acl_projection_pending_sync(staged_before)
                raise
            else:
                self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            self._discard_acl_projection_pending_sync()
            raise
        else:
            self._conn.commit()
            self._flush_acl_projection_pending_sync()

    def register_invalidation(self, name: str, callback: Callable[[], None]) -> None:
        """Register a cache that must be dropped whenever stored data changes.

        Caches register themselves instead of each one remembering to compare
        a revision: anything registered here is cleared by the same broadcast
        that refreshes the store-owned projections, so a new cache cannot be
        forgotten by the invalidation path.
        """
        key = clean_text(name, 80)
        if not key or not callable(callback):
            return
        with self._cache_invalidation_lock:
            self._cache_invalidation_callbacks[key] = callback

    def invalidate_registered_caches(self, *, reason: str = "") -> list[str]:
        """Broadcast an invalidation to every registered cache."""
        with self._cache_invalidation_lock:
            callbacks = list(self._cache_invalidation_callbacks.items())
        cleared: list[str] = []
        for name, callback in callbacks:
            try:
                callback()
            except Exception as exc:
                logger.warning(
                    "[MemoryCompanion] 缓存失效回调失败，已跳过: name=%s reason=%s error=%s",
                    name,
                    reason,
                    exc,
                    exc_info=True,
                )
                continue
            cleared.append(name)
        return cleared

    def shutdown_state(self) -> dict[str, Any]:
        """Return a lightweight snapshot for plugin shutdown diagnostics."""
        with self._operation_condition:
            tracked_ops = int(self._active_tracked_operations)
            owner_stack = self._operation_owner_stack
        return {
            "main_conn_closed": bool(self._closed),
            "read_conn_closed": self._read_conn is None,
            "tracked_ops": tracked_ops,
            "operation_owner_stack": owner_stack,
        }

    def close(self) -> None:
        with self._operation_condition:
            if self._closed:
                return
            self._closing = True
            if self._active_tracked_operations:
                self._operation_waiters += 1
                try:
                    while self._active_tracked_operations:
                        self._operation_condition.wait()
                finally:
                    self._operation_waiters -= 1
        with self._lock:
            if self._closed:
                return
            self._conn.commit()
            self._conn.close()
            self._closed = True
            self._vector_search_cache.clear()
        with self._read_lock:
            if self._read_conn is not None:
                try:
                    self._read_conn.close()
                except sqlite3.Error:
                    pass
                self._read_conn = None

    def backup(self, suffix: str = "") -> Path:
        stamp = utc_now().replace(":", "").replace("-", "").replace("+", "_")
        target = self.db_path.with_name(
            f"{self.db_path.stem}.backup.{stamp}{suffix}.db"
        )
        with self._lock:
            self._conn.commit()
            with closing(sqlite3.connect(str(target))) as target_conn:
                self._conn.backup(target_conn)
        return target

    async def backup_async(self, suffix: str = "") -> Path:
        """Copy the whole database without blocking the event loop.

        ``sqlite3.Connection.backup`` rewrites every page while holding the
        store write lock.  On a production-sized database that stalls the
        event loop for the entire copy, so asynchronous callers must use this
        wrapper; the synchronous variant stays for callers that already run
        inside a worker thread.
        """
        return await asyncio.to_thread(self.backup, suffix)
