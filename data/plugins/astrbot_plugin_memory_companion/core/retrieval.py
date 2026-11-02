from __future__ import annotations

import threading
from typing import Any

from .models import (
    clean_text,
)
from .retrieval_candidate_fusion import RetrievalEngineCandidateFusion
from .retrieval_candidates import RetrievalEngineCandidates
from .retrieval_deduplication import RetrievalEngineDeduplication
from .retrieval_embeddings import RetrievalEngineEmbeddings
from .retrieval_pipeline import RetrievalEnginePipeline
from .retrieval_profile_policy import RetrievalEngineProfilePolicy
from .retrieval_query_intent import RetrievalEngineQueryIntent
from .retrieval_query_terms import RetrievalEngineQueryTerms
from .retrieval_reranking import RetrievalEngineReranking
from .retrieval_scoring import RetrievalEngineScoring
from .retrieval_visibility import RetrievalEngineVisibility
from .store import MemoryStore
from .visibility import VisibilityPolicy


class RetrievalEngine(
    RetrievalEnginePipeline,
    RetrievalEngineReranking,
    RetrievalEngineDeduplication,
    RetrievalEngineCandidates,
    RetrievalEngineEmbeddings,
    RetrievalEngineQueryTerms,
    RetrievalEngineCandidateFusion,
    RetrievalEngineProfilePolicy,
    RetrievalEngineVisibility,
    RetrievalEngineQueryIntent,
    RetrievalEngineScoring,
):
    DEFAULT_MATERIALIZE_LIMIT = 2000

    _QUERY_AWARE_ROUTE_SOURCES = frozenset({"fts", "keyword", "vector", "time_window"})

    def __init__(
        self,
        store: MemoryStore,
        policy: VisibilityPolicy,
        *,
        retrieval_mode: str = "auto",
        rerank_provider: Any = None,
        rerank_provider_id: str = "",
        rerank_candidate_multiplier: int = 5,
        rerank_candidate_limit: int = 32,
        rerank_timeout_ms: int = 1200,
        latency_budget_ms: int = 0,
        query_vector_cache_ttl_seconds: float = 0.0,
        embedding_provider: Any = None,
        embedding_provider_id: str = "",
        embedding_enabled: bool = False,
        embedding_candidate_limit: int = 1200,
        embedding_top_k: int = 32,
        embedding_score_threshold: float = 0.34,
        embedding_weight: float = 0.55,
        embedding_timeout_ms: int = 5000,
        embedding_max_text_chars: int = 1200,
        current_window_candidate_limit: int = 600,
        keyword_fallback_min_fts_candidates: int = 80,
        knowledge_graph_enabled: bool = True,
        knowledge_graph_expansion_limit: int = 12,
        private_topology_enabled: bool = True,
        group_topology_enabled: bool = True,
        usage_recorder: Any | None = None,
        relax_keyword_min_hits: bool = False,
        proactive_message_score_penalty: float = 1.0,
        dedupe_content_ratio: float = 0.0,
        non_conversation_budget: int = 2,
    ):
        self.store = store
        self.policy = policy
        self.retrieval_mode = clean_text(retrieval_mode or "auto", 40).lower()
        if self.retrieval_mode not in {"auto", "basic", "rerank"}:
            self.retrieval_mode = "auto"
        self.rerank_provider = rerank_provider
        self.rerank_provider_id = clean_text(rerank_provider_id, 160)
        self.rerank_candidate_multiplier = max(1, int(rerank_candidate_multiplier or 5))
        self.rerank_candidate_limit = max(1, int(rerank_candidate_limit or 32))
        self.rerank_timeout_ms = max(0, int(rerank_timeout_ms or 0))
        # A latency budget is spent on optional model calls, never on results.
        # Lowering top_k to go faster removes exactly the context this plugin
        # exists to supply, so the speed has to come from somewhere else.
        self.latency_budget_ms = max(0, int(latency_budget_ms or 0))
        self._latency_started_at: float | None = None
        self.query_vector_cache_ttl_seconds = max(
            0.0, float(query_vector_cache_ttl_seconds or 0.0)
        )
        self._query_vector_cache: dict[str, tuple[float, list[float]]] = {}
        self.embedding_provider = embedding_provider
        self.embedding_provider_id = clean_text(embedding_provider_id, 160)
        self.embedding_enabled = bool(embedding_enabled)
        self.embedding_candidate_limit = max(1, int(embedding_candidate_limit or 1200))
        self.embedding_top_k = max(1, int(embedding_top_k or 32))
        self.embedding_score_threshold = max(
            0.0, min(1.0, float(embedding_score_threshold or 0.0))
        )
        self.embedding_weight = max(0.0, min(2.0, float(embedding_weight or 0.0)))
        self.embedding_timeout_ms = max(0, int(embedding_timeout_ms or 0))
        self.embedding_max_text_chars = max(200, int(embedding_max_text_chars or 1200))
        self.current_window_candidate_limit = max(
            1, int(current_window_candidate_limit or 600)
        )
        self.keyword_fallback_min_fts_candidates = max(
            0,
            int(keyword_fallback_min_fts_candidates or 0),
        )
        self.knowledge_graph_enabled = bool(knowledge_graph_enabled)
        self.knowledge_graph_expansion_limit = max(
            0, int(knowledge_graph_expansion_limit or 0)
        )
        self.private_topology_enabled = bool(private_topology_enabled)
        self.group_topology_enabled = bool(group_topology_enabled)
        self.usage_recorder = usage_recorder if callable(usage_recorder) else None
        # relax_keyword_min_hits=False 时词法复核 min_hits 与历史版本完全一致；
        # 开启后把 temporal_aggregate/exact_phrases/长词 分支的 min_hits 降到 1，
        # 让语义相关但关键词不重叠的候选更容易通过词法复核。
        self.relax_keyword_min_hits = bool(relax_keyword_min_hits)
        # proactive_message_score_penalty：proactive_message（主动消息原文）类记忆的评分乘数。
        # 1.0=不降权（历史行为）；<1.0 降权；0=完全排除。
        self.proactive_message_score_penalty = max(
            0.0, min(1.0, float(proactive_message_score_penalty or 1.0))
        )
        # dedupe_content_ratio：检索结果内容级去重阈值（规范化文本 difflib ratio）。
        # 0.0=关闭（历史行为）；>0 时同主题高相似候选只保留评分最高的一条。
        self.dedupe_content_ratio = max(
            0.0, min(1.0, float(dedupe_content_ratio or 0.0))
        )
        # Bot self-timeline records are useful context, but should not crowd
        # out user/query evidence in ordinary questions. Explicit self-timeline
        # queries remove this cap via capped_slots.
        self.non_conversation_budget = max(
            0, min(12, int(non_conversation_budget or 0))
        )
        self._rank_path_info: dict[str, Any] = {}
        # haystack 缓存：同一 (memory.id, updated_at) 在一次召回内会被
        # _term_document_stats/_score/MMR features 多次重建，按键复用可省去
        # 重复的字段拼接与 lower()。以锁保护以便在线程池中安全访问。
        # 引擎每次召回都会重建，实例内缓存只活一次调用；store 提供共享缓存
        # 时优先使用它，条目由同一条失效广播随其它投影一起清除。
        self._haystack_cache: dict[tuple[str, str], str] = {}
        self._haystack_cache_lock = threading.Lock()
        self._shared_haystack_cache = getattr(store, "haystack_cache", None)
        self.last_path_info: dict[str, Any] = {
            "mode": self.retrieval_mode,
            "path": "basic",
            "provider_id": self.rerank_provider_id,
            "reason": "not_started",
            "embedding_enabled": self.embedding_enabled,
            "embedding_provider_id": self.embedding_provider_id,
            "embedding_reason": "not_started",
            "private_topology_enabled": self.private_topology_enabled,
            "group_topology_enabled": self.group_topology_enabled,
        }
