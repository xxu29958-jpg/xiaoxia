from __future__ import annotations

import asyncio
import secrets
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .astrbot_compat import (
    logger,
)
from .audit import MemoryAuditManager
from .chat_import import HistoricalChatImporter
from .classifier import MemoryClassifier
from .config import ConfigView
from .context_orchestrator import RetrievalIntentBuilder
from .identity import (
    IdentityResolver,
)
from .importance import ImportanceEvaluator
from .injection import (
    InjectionComposer,
)
from .migration_livingmemory import LivingMemoryMigrator
from .models import (
    SessionContext,
    clean_text,
)
from .operations import (
    PortableMemoryArchive,
)
from .portrait_service import PortraitService
from .provenance_store import ProvenanceLedger
from .qq_history import QQHistoryReader
from .reply_chain import ReplyChainResolver
from .scoped_store import ScopedStore
from .service_actor_filtering import MemoryCompanionServiceActorFiltering
from .service_background_runtime import MemoryCompanionServiceBackgroundRuntime
from .service_bot_profile import MemoryCompanionServiceBotProfile
from .service_bridge_context import MemoryCompanionServiceBridgeContext
from .service_companion_dedupe import MemoryCompanionServiceCompanionDedupe
from .service_companion_notes import MemoryCompanionServiceCompanionNotes
from .service_companion_projection import MemoryCompanionServiceCompanionProjection
from .service_context_anchors import MemoryCompanionServiceContextAnchors
from .service_core_preferences import MemoryCompanionServiceCorePreferences
from .service_embedding_runtime import MemoryCompanionServiceEmbeddingRuntime
from .service_expression_policy import (
    MemoryCompanionServiceExpressionPolicy,
)
from .service_external_capture import MemoryCompanionServiceExternalCapture
from .service_history_import import MemoryCompanionServiceHistoryImport
from .service_injection_context import MemoryCompanionServiceInjectionContext
from .service_injection_publication import MemoryCompanionServiceInjectionPublication
from .service_maintenance import MemoryCompanionServiceMaintenance
from .service_maintenance_dispatch import MemoryCompanionServiceMaintenanceDispatch
from .service_memory_tools import MemoryCompanionServiceMemoryTools
from .service_navigation import MemoryCompanionServiceNavigation
from .service_operations import MemoryCompanionServiceOperations
from .service_provider_selection import MemoryCompanionServiceProviderSelection
from .service_recall_routing import MemoryCompanionServiceRecallRouting
from .service_recent_context import MemoryCompanionServiceRecentContext
from .service_relationship_phase import MemoryCompanionServiceRelationshipPhase
from .service_reply_context import MemoryCompanionServiceReplyContext
from .service_request_capture import MemoryCompanionServiceRequestCapture
from .service_request_injection import MemoryCompanionServiceRequestInjection
from .service_retrieval_runtime import MemoryCompanionServiceRetrievalRuntime
from .service_scope_policy import MemoryCompanionServiceScopePolicy
from .service_semantic_indexing import MemoryCompanionServiceSemanticIndexing
from .service_semantic_lifecycle import MemoryCompanionServiceSemanticLifecycle
from .service_summary_evidence import MemoryCompanionServiceSummaryEvidence
from .service_summary_generation import MemoryCompanionServiceSummaryGeneration
from .service_support import _CORE_MEMORY_TOOL_CONTRACT as _CORE_MEMORY_TOOL_CONTRACT
from .service_support import (
    _CORE_MEMORY_TOOL_CONTRACT_FOOTER as _CORE_MEMORY_TOOL_CONTRACT_FOOTER,
)
from .service_support import (
    _CORE_MEMORY_TOOL_CONTRACT_HEADER as _CORE_MEMORY_TOOL_CONTRACT_HEADER,
)
from .service_support import _RECALL_EVIDENCE_GUIDANCE as _RECALL_EVIDENCE_GUIDANCE
from .service_support import _RECALL_GUIDANCE_FOOTER as _RECALL_GUIDANCE_FOOTER
from .service_support import _RECALL_GUIDANCE_HEADER as _RECALL_GUIDANCE_HEADER
from .service_support import (
    _RECENT_FACT_ASSERTION_MARKERS as _RECENT_FACT_ASSERTION_MARKERS,
)
from .service_support import _RECENT_FACT_PATTERNS as _RECENT_FACT_PATTERNS
from .service_support import _RECONSTRUCTION_ACTIONS as _RECONSTRUCTION_ACTIONS
from .service_support import _RECONSTRUCTION_CONTRACT as _RECONSTRUCTION_CONTRACT
from .service_support import (
    _RECONSTRUCTION_CONTRACT_FOOTER as _RECONSTRUCTION_CONTRACT_FOOTER,
)
from .service_support import (
    _RECONSTRUCTION_CONTRACT_HEADER as _RECONSTRUCTION_CONTRACT_HEADER,
)
from .service_support import (
    _RECONSTRUCTION_DYNAMIC_FOOTER as _RECONSTRUCTION_DYNAMIC_FOOTER,
)
from .service_support import (
    _RECONSTRUCTION_DYNAMIC_HEADER as _RECONSTRUCTION_DYNAMIC_HEADER,
)
from .service_support import _REMEMBER_TOOL_CONTRACT as _REMEMBER_TOOL_CONTRACT
from .service_support import (
    _REMEMBER_TOOL_CONTRACT_FOOTER as _REMEMBER_TOOL_CONTRACT_FOOTER,
)
from .service_support import (
    _REMEMBER_TOOL_CONTRACT_HEADER as _REMEMBER_TOOL_CONTRACT_HEADER,
)
from .service_support import _RULE_PROFILE_MEMORY_TYPES as _RULE_PROFILE_MEMORY_TYPES
from .service_support import (
    _USER_MEMORY_CONVERSATION_TYPES as _USER_MEMORY_CONVERSATION_TYPES,
)
from .service_support import (
    _USER_MEMORY_PREFERENCE_TYPES as _USER_MEMORY_PREFERENCE_TYPES,
)
from .service_support import _USER_MEMORY_PROFILE_TYPES as _USER_MEMORY_PROFILE_TYPES
from .service_support import (
    _USER_MEMORY_RELATIONSHIP_TYPES as _USER_MEMORY_RELATIONSHIP_TYPES,
)
from .service_support import LOCAL_TZ as LOCAL_TZ
from .service_support import USER_MEMORY_SUMMARY_VERSION as USER_MEMORY_SUMMARY_VERSION
from .service_support import MemoryRouteDecision as MemoryRouteDecision
from .service_support import _HookStageTimer as _HookStageTimer
from .service_token_accounting import MemoryCompanionServiceTokenAccounting
from .service_user_profile import MemoryCompanionServiceUserProfile
from .store import MemoryStore
from .summarizer import MemorySummarizer


class MemoryCompanionService(
    MemoryCompanionServiceScopePolicy,
    MemoryCompanionServiceHistoryImport,
    MemoryCompanionServiceRequestCapture,
    MemoryCompanionServiceMaintenanceDispatch,
    MemoryCompanionServiceCompanionProjection,
    MemoryCompanionServiceExternalCapture,
    MemoryCompanionServiceBotProfile,
    MemoryCompanionServiceUserProfile,
    MemoryCompanionServiceBridgeContext,
    MemoryCompanionServiceRetrievalRuntime,
    MemoryCompanionServiceProviderSelection,
    MemoryCompanionServiceTokenAccounting,
    MemoryCompanionServiceBackgroundRuntime,
    MemoryCompanionServiceSemanticLifecycle,
    MemoryCompanionServiceSemanticIndexing,
    MemoryCompanionServiceEmbeddingRuntime,
    MemoryCompanionServiceSummaryGeneration,
    MemoryCompanionServiceSummaryEvidence,
    MemoryCompanionServiceCorePreferences,
    MemoryCompanionServiceMemoryTools,
    MemoryCompanionServiceNavigation,
    MemoryCompanionServiceCompanionNotes,
    MemoryCompanionServiceOperations,
    MemoryCompanionServiceMaintenance,
    MemoryCompanionServiceInjectionContext,
    MemoryCompanionServiceRequestInjection,
    MemoryCompanionServiceReplyContext,
    MemoryCompanionServiceInjectionPublication,
    MemoryCompanionServiceRecentContext,
    MemoryCompanionServiceRecallRouting,
    MemoryCompanionServiceContextAnchors,
    MemoryCompanionServiceActorFiltering,
    MemoryCompanionServiceExpressionPolicy,
    MemoryCompanionServiceCompanionDedupe,
    MemoryCompanionServiceRelationshipPhase,
):
    """Own shared runtime resources; domain classes never create separate state."""

    _SCOPE_CONTROL_FEATURES = frozenset({"capture", "recall", "topology"})

    _ACL_RECONCILE_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        *,
        context: Any,
        config: Any,
        plugin_root: Path,
        data_dir: Path,
        defer_database_initialization: bool = False,
    ):
        self.context = context
        self.config = ConfigView(config)
        self.plugin_root = Path(plugin_root)
        self.data_dir = Path(data_dir)

        # 启动宽限期：插件加载后的前 N 秒内暂缓创建任何后台任务，
        # 避开 AstrBot 核心加载完插件后执行的 SQLAlchemy 连接池迁移窗口，
        # 防止插件后台线程的高频 DB 并发撞上核心 deque 迭代（RuntimeError: deque mutated during iteration）。
        self._service_created_at = time.monotonic()
        self._background_grace_seconds = max(
            0,
            self.config.int("startup.background_grace_seconds", 60),
        )

        self.store = MemoryStore(self.data_dir / "memory_companion.db")
        self.scoped_store = ScopedStore(
            self.data_dir / "req041_scoped.db",
            initialize=False,
        )
        self._database_init_lock = threading.Lock()
        self._database_initialized = False
        if not defer_database_initialization:
            try:
                self._initialize_database_sync()
            except BaseException:
                self.store.close()
                raise
        self.portraits = PortraitService(self.store, self.config)

        self.identity = IdentityResolver(
            self._resolve_default_bot_id, self._resolve_runtime_persona_id
        )
        self.reply_chain = ReplyChainResolver()
        self.intent_builder = RetrievalIntentBuilder()
        self.classifier = MemoryClassifier(
            capture_min_chars=self.config.int("memory_capture.capture_min_chars", 2)
        )
        self.injection = InjectionComposer()
        self.summarizer = MemorySummarizer(
            max_input_chars=self.config.int("memory_summary.max_input_chars", 6000),
            max_summary_chars=self.config.int("memory_summary.max_summary_chars", 1200),
            provider_timeout_seconds=self.config.int(
                "memory_summary.provider_timeout_seconds", 300
            ),
        )
        self.importance = ImportanceEvaluator(
            mention_policy_relax=self.config.bool(
                "memory_capture.relax_mention_policy", False
            )
        )
        self._summary_locks: dict[str, asyncio.Lock] = {}
        self._summary_lock_ts: dict[str, float] = {}
        self._summary_workers: dict[str, asyncio.Task[Any]] = {}
        self._summary_pending: set[str] = set()
        self._summary_pending_contexts: dict[str, SessionContext] = {}
        self._summary_pending_reasons: dict[str, str] = {}
        self._summary_lock_last_cleanup: float = time.monotonic()
        self._SUMMARY_LOCK_TTL: float = 600.0  # 10 minutes
        self._decay_lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._portrait_dispatch_task: asyncio.Task[Any] | None = None
        self._maintenance_dispatch_task: asyncio.Task[Any] | None = None
        self._maintenance_dispatch_status: dict[str, Any] = {
            "state": "idle",
            "last_error": "",
            "last_completed_at": "",
        }
        self._wal_checkpoint_task: asyncio.Task[Any] | None = None
        self._wal_checkpoint_status: dict[str, Any] = {
            "state": "idle",
            "last_error": "",
            "last_completed_at": "",
            "last_result": {},
        }
        self._acl_reconcile_task: asyncio.Task[Any] | None = None
        self._acl_reconcile_status: dict[str, Any] = {
            "state": "idle",
            "last_error": "",
            "last_completed_at": "",
            "last_revision": "",
            "last_changed": False,
        }
        self._portrait_dispatch_status: dict[str, Any] = {
            "state": "idle",
            "last_run_day": "",
            "last_result": "",
            "last_completed_at": "",
        }
        self._closing = False
        self.capture = None
        if self._database_initialized:
            from .capture_runtime import CaptureRuntime

            self.capture = CaptureRuntime(self)
        self._closed = False
        self._embedding_backfill_inflight: set[str] = set()
        self._embedding_memory_inflight: set[str] = set()
        self._embedding_backfill_last_run: dict[str, float] = {}
        self._embedding_backfill_offsets: dict[str, int] = {}
        self._embedding_background_semaphore = asyncio.Semaphore(
            max(1, self.config.int("retrieval.embedding_background_concurrency", 2))
        )
        # 前台来源语义查询等待槽位时，后台建库主动让步，避免历史回填长期占满
        # 共享 embedding 并发额度。
        self._source_semantic_query_waiters = 0
        self._source_semantic_inflight: set[str] = set()
        self._source_semantic_pending_sources: dict[str, set[str]] = {}
        self._source_semantic_tasks: dict[str, asyncio.Task[Any]] = {}
        self._source_semantic_context_keys: dict[str, str] = {}
        self._source_semantic_status: dict[str, Any] = {
            "state": "disabled",
            "last_error": "",
            "last_run_at": "",
            "last_generation": "",
        }
        # 全局摘要调用并发上限（#10）：每会话 worker 并行调 provider 会压垮
        # 模型网关，默认串行化所有后台阶段性总结的 provider 调用。
        self._summary_call_semaphore = asyncio.Semaphore(
            max(1, self.config.int("memory_summary.max_concurrent_calls", 1))
        )
        self._embedding_provider_warned: set[str] = set()
        self._last_retrieval_path_info: dict[str, Any] = {}
        self._hook_stage_timer: _HookStageTimer | None = None
        self._retrieval_result_cache: dict[str, dict[str, Any]] = {}
        self._retrieval_result_cache_stats: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
        }
        self._RETRIEVAL_RESULT_CACHE_TTL: float = 45.0
        self._RETRIEVAL_RESULT_CACHE_MAX: int = 128
        # 注入结果 TTL 缓存（§3.5）：同一会话短时间连续提问的注入包变化很小，
        # 命中缓存可毫秒级注入，避免重复检索与编排。
        self._injection_cache: dict[str, tuple[float, str, str]] = {}
        # 注入缓存是派生投影：数据 revision 变化后必须整体失效。注册到 store
        # 的失效广播里，而不是让每个缓存各自记得去比对 revision。
        self.store.register_invalidation("injection", self._injection_cache.clear)
        self._reconstruction_states: dict[str, dict[str, Any]] = {}
        self._query_generation = secrets.token_hex(8)
        self._reconstruction_lock = asyncio.Lock()
        self._reconstruction_last_cleanup: float = 0.0
        self._RECONSTRUCTION_STATE_TTL: float = 600.0
        self._RECONSTRUCTION_STATE_MAX: int = 512
        self.migrator = LivingMemoryMigrator(
            self.store, self.plugin_root, self.data_dir
        )
        self.portable_archive = PortableMemoryArchive(self.store, self.data_dir)
        self.memory_audit = MemoryAuditManager(self, self.data_dir / "memory_audits")
        self.chat_importer = HistoricalChatImporter(self)
        self.qq_history_reader = QQHistoryReader(self)
        self.sleep_state_path = self.data_dir / "memory_companion_sleep_state.json"
        self.token_usage_path = self.data_dir / "memory_companion_token_usage.json"
        self._token_usage_last_save_at: float = 0.0
        self._token_usage: dict[str, Any] = self._load_token_usage()
        self._emotional_event_queue: dict[str, list[dict[str, Any]]] = {}
        self._EMOTIONAL_EVENT_MAX_PER_SESSION = 10
        self._EMOTIONAL_EVENT_TTL = 3600.0
        self._emotional_event_last_cleanup: float = 0.0
        self._relationship_phase_state: dict[str, dict[str, Any]] = {}
        self._RELATIONSHIP_PHASE_FILE = (
            self.data_dir / "memory_companion_relationship_phase.json"
        )
        self._RELATIONSHIP_PHASE_STATE_TTL = 180 * 86400.0
        self._RELATIONSHIP_PHASE_STATE_MAX = 4096
        self._relationship_phase_last_cleanup: float = 0.0
        self._load_relationship_phase_state()
        self.provenance_ledger = ProvenanceLedger(
            self.data_dir / "provenance_ledger.json"
        )
        self._last_p5_gate_status: dict[str, Any] = {
            "state": "disabled",
            "enabled": False,
        }

    async def _resolve_runtime_persona_id(self, event: Any) -> str:
        """Use the host's effective conversation persona for memory capture and recall.

        Args:
            event: The native message event whose conversation is being processed.

        Returns:
            The selected persona, or an empty value when no persona is selected.
        """
        manager = getattr(self.context, "persona_manager", None)
        if manager is None:
            return ""
        origin = event.unified_msg_origin
        conversations = self.context.conversation_manager
        conversation_id = await conversations.get_curr_conversation_id(origin)
        conversation = (
            await conversations.get_conversation(origin, conversation_id)
            if conversation_id
            else None
        )
        persona_id, _, _, _ = await manager.resolve_selected_persona(
            umo=origin,
            conversation_persona_id=conversation.persona_id if conversation else None,
            platform_name=event.get_platform_name(),
        )
        return "" if persona_id == "[%None]" else clean_text(persona_id, 96)

    def _resolve_default_bot_id(self, _event: Any = None) -> str:
        """Return a Bot ID only when the runtime exposes one unambiguous value."""
        ids: set[str] = set()

        def add(value: Any) -> None:
            value = clean_text(value, 120)
            if value and value.casefold() not in {"self", "bot", "bot_self", "unknown"}:
                ids.add(value)

        context = self.context
        for source in (
            context,
            getattr(context, "platform_manager", None),
            getattr(_event, "message_obj", None),
            _event,
        ):
            for attr in ("bot_user_id", "bot_self_id", "self_id", "client_self_id"):
                add(getattr(source, attr, ""))

        manager = getattr(context, "platform_manager", None)
        for instance in list(getattr(manager, "platform_insts", []) or []):
            for attr in ("self_id", "bot_self_id", "bot_user_id", "client_self_id"):
                add(getattr(instance, attr, ""))
            bot = getattr(instance, "bot", None)
            for attr in ("self_id", "bot_self_id", "bot_user_id"):
                add(getattr(bot, attr, ""))

        getter = getattr(context, "get_all_stars", None)
        if callable(getter):
            try:
                stars = getter() or []
            except Exception:
                stars = []
            if not isinstance(stars, Iterable):
                stars = []
            for star in stars:
                for attr in ("bot_user_id", "bot_self_id", "self_id"):
                    add(getattr(star, attr, ""))
                star_config = getattr(star, "config", None)
                if isinstance(star_config, dict):
                    for key in ("bot_user_id", "bot_self_id", "self_id"):
                        add(star_config.get(key))

        return next(iter(ids)) if len(ids) == 1 else ""

    _PHASES = ["acquaintance", "familiar", "close", "intimate", "deeply_bonded"]

    _PHASE_THRESHOLDS = [0.0, 0.20, 0.45, 0.65, 0.85]

    _PHASE_MOMENTUM_MAX = 1.0

    _PHASE_MOMENTUM_MIN = -0.3

    _RELATIONSHIP_TOUCH_HISTORY_LIMIT = 256

    _ADDRESS_TERMS = {
        "formal": ["你好", "请问", "麻烦", "您好"],
        "casual": ["嘿", "哈喽", "hi", "hello", "喂"],
        "intimate": [
            "亲爱的",
            "宝贝",
            "宝宝",
            "老公",
            "老婆",
            "哥哥",
            "姐姐",
            "笨蛋",
            "傻瓜",
            "猪猪",
        ],
        "playful": ["小子", "丫头", "笨蛋", "大笨蛋", "小可爱"],
    }

    _BOT_ADDRESS_SUGGESTIONS = {
        "acquaintance": {
            "tone": "礼貌自然",
            "hint": "你们还不太熟，用自然的语气称呼对方名字或“你”就好，不要太亲昵也不要太疏远。",
        },
        "familiar": {
            "tone": "轻松友好",
            "hint": "你们已经比较熟了，可以用更轻松的方式称呼对方，偶尔用昵称或缩写名字。",
        },
        "close": {
            "tone": "温暖亲近",
            "hint": "你们关系很近了，可以用温暖的方式称呼对方，比如“你呀”、“笨蛋”这种带着善意的词，自然地用名字缩写或昵称。",
        },
        "intimate": {
            "tone": "亲密柔软",
            "hint": "你们关系很亲密，可以用柔软的方式称呼对方，像“宝贝”、“亲爱的”这种词在自然的时候可以用，但不要刻意。",
        },
        "deeply_bonded": {
            "tone": "默契无间",
            "hint": "你们之间已经不需要刻意称呼了，用只有你们才懂的称呼或昵称，语气里带着只有彼此才理解的默契。",
        },
    }

    def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        for task in list(self._summary_workers.values()):
            task.cancel()
        self._summary_workers.clear()
        self._summary_pending.clear()
        self._summary_pending_contexts.clear()
        self._summary_pending_reasons.clear()
        self._save_token_usage(force=True)
        try:
            self._close_store_sync()
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 关闭记忆库连接失败: %s", exc, exc_info=True
            )
        finally:
            self._closed = True

    def _close_store_sync(self) -> None:
        with self._database_init_lock:
            self.store.close()

    def shutdown_evidence(self) -> dict[str, Any]:
        """Return structured resource state for the shutdown completion log."""
        try:
            store_state = self.store.shutdown_state()
        except Exception:
            # Keep plugin termination successful even when an older store object
            # does not expose the optional diagnostic method.
            store_state = {
                "main_conn_closed": bool(getattr(self.store, "_closed", False)),
                "read_conn_closed": getattr(self.store, "_read_conn", None) is None,
                "tracked_ops": int(
                    getattr(self.store, "_active_tracked_operations", 0) or 0
                ),
                "error": "store_shutdown_state_unavailable",
            }
        return {
            "background_tasks": len(self._background_tasks),
            "summary_workers": len(self._summary_workers),
            "summary_pending": len(self._summary_pending),
            "store": store_state,
        }

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closing = True
        current = asyncio.current_task()
        tasks = [
            task
            for task in self._background_tasks
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        # Summary workers are tracked separately from _background_tasks.
        # Cancel and await them before closing SQLite connections.
        summary_tasks = [
            task
            for task in self._summary_workers.values()
            if task is not current and not task.done()
        ]
        for task in summary_tasks:
            task.cancel()
        if summary_tasks:
            await asyncio.gather(*summary_tasks, return_exceptions=True)
        self._summary_workers.clear()
        self._summary_pending.clear()
        self._summary_pending_contexts.clear()
        self._summary_pending_reasons.clear()
        self._save_token_usage(force=True)
        try:
            await asyncio.to_thread(self._close_store_sync)
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 关闭记忆库连接失败: %s", exc, exc_info=True
            )
        finally:
            self._closed = True

    def _initialize_database_sync(self) -> bool:
        with self._database_init_lock:
            if getattr(self, "_closing", False) or getattr(self, "_closed", False):
                return False
            if self._database_initialized:
                return True
            try:
                self.store.initialize()
            except Exception as exc:
                raise RuntimeError(
                    f"记忆主库初始化失败 [primary_store.initialize]: {type(exc).__name__}: {exc}"
                ) from exc
            try:
                self.scoped_store.initialize()
            except Exception as exc:
                raise RuntimeError(
                    f"作用域记忆库初始化失败 [scoped_store.initialize]: {type(exc).__name__}: {exc}"
                ) from exc
            try:
                normalized = self.store.normalize_legacy_manual_visibility()
                if normalized:
                    logger.info(
                        "[MemoryCompanion] 已收回早期过宽的手动记忆可见性: count=%s",
                        normalized,
                    )
                internal_normalized = self.store.normalize_internal_bot_self_scopes()
                if internal_normalized:
                    logger.info(
                        "[MemoryCompanion] 已将内部 Bot 梦境移出私聊用户范围: count=%s",
                        internal_normalized,
                    )
            except Exception as exc:
                raise RuntimeError(
                    f"旧记忆规范化失败 [legacy_memory.normalize]: {type(exc).__name__}: {exc}"
                ) from exc
            self._database_initialized = True
            return True

    async def initialize_database(self) -> bool:
        """Initialize or upgrade databases without blocking AstrBot's loop."""
        if self._closing or self._closed:
            return False
        if not self._database_initialized:
            initialized = await asyncio.to_thread(self._initialize_database_sync)
            if not initialized:
                return False
        if self._closing or self._closed:
            return False
        if self.capture is None:
            from .capture_runtime import CaptureRuntime

            self.capture = CaptureRuntime(self)
        return True
