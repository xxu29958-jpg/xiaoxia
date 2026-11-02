from __future__ import annotations

from typing import Any

from .companion_page_bridge import (
    CompanionPageBridge,
)
from .page_api_companion_page import PluginPageApiCompanionPage
from .page_api_configuration import PluginPageApiConfiguration
from .page_api_diagnostics import PluginPageApiDiagnostics
from .page_api_imports import PluginPageApiImports
from .page_api_legacy_projection import PluginPageApiLegacyProjection
from .page_api_memory_management import PluginPageApiMemoryManagement
from .page_api_operations import PluginPageApiOperations
from .page_api_personal_records import PluginPageApiPersonalRecords
from .page_api_profiles import PluginPageApiProfiles
from .page_api_providers import PluginPageApiProviders
from .page_api_queries import PluginPageApiQueries
from .page_api_response_contract import PluginPageApiResponseContract
from .page_api_support import COMPANION_PERSONAL_FIELDS as COMPANION_PERSONAL_FIELDS
from .page_api_support import DEFAULT_THEME_KEY as DEFAULT_THEME_KEY
from .page_api_support import DEFAULT_THEME_NAME as DEFAULT_THEME_NAME
from .page_api_support import ENDPOINT_FIELD_CONTRACT as ENDPOINT_FIELD_CONTRACT
from .page_api_support import MEMORY_LIFECYCLE_FILTERS as MEMORY_LIFECYCLE_FILTERS
from .page_api_support import MEMORY_TYPE_FILTERS as MEMORY_TYPE_FILTERS
from .page_api_support import MEMORY_VISIBILITY_FILTERS as MEMORY_VISIBILITY_FILTERS
from .page_api_support import PAGE_API_PREFIXES as PAGE_API_PREFIXES
from .page_api_support import PLUGIN_NAME as PLUGIN_NAME
from .page_api_support import (
    QQ_HISTORY_CAPABILITY_FIELDS as QQ_HISTORY_CAPABILITY_FIELDS,
)
from .page_api_support import THEME_KEYS as THEME_KEYS
from .page_api_support import THEME_NAME_TO_KEY as THEME_NAME_TO_KEY
from .page_api_support import UI_CONTRACT_VERSION as UI_CONTRACT_VERSION
from .page_api_support import UI_DANGEROUS_ENDPOINTS as UI_DANGEROUS_ENDPOINTS
from .page_api_support import UI_ENDPOINT_EXPOSURE as UI_ENDPOINT_EXPOSURE
from .page_api_support import UI_MODES as UI_MODES
from .page_api_support import UI_VIEW_ENDPOINTS as UI_VIEW_ENDPOINTS
from .page_api_support import logger as logger
from .page_api_validation import PluginPageApiValidation


class PluginPageApi(
    PluginPageApiProfiles,
    PluginPageApiDiagnostics,
    PluginPageApiImports,
    PluginPageApiMemoryManagement,
    PluginPageApiQueries,
    PluginPageApiConfiguration,
    PluginPageApiCompanionPage,
    PluginPageApiLegacyProjection,
    PluginPageApiPersonalRecords,
    PluginPageApiOperations,
    PluginPageApiResponseContract,
    PluginPageApiProviders,
    PluginPageApiValidation,
):
    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._companion_page_bridge = CompanionPageBridge(plugin)
        self._emotion_page_admin_capability = None
        bridge = getattr(plugin, "memory_companion", None)
        binder = getattr(bridge, "bind_emotion_page_api", None)
        if callable(binder):
            try:
                self._emotion_page_admin_capability = binder(self)
            except Exception:
                self._emotion_page_admin_capability = None

    def _route_specs(self) -> list[tuple[str, Any, list[str], str]]:
        return [
            (
                "/ui/capabilities",
                self.ui_capabilities,
                ["GET"],
                "MemoryCompanion UI capability contract",
            ),
            (
                "/ui-preferences",
                self.ui_preferences,
                ["GET"],
                "MemoryCompanion UI preferences",
            ),
            ("/stats", self.stats, ["GET"], "MemoryCompanion Page stats"),
            ("/buckets", self.buckets, ["GET"], "MemoryCompanion Page buckets"),
            ("/memories", self.memories, ["GET"], "MemoryCompanion Page memories"),
            (
                "/memory",
                self.memory_detail,
                ["GET"],
                "MemoryCompanion Page memory detail",
            ),
            (
                "/memory/update",
                self.memory_update,
                ["POST"],
                "MemoryCompanion Page memory update",
            ),
            (
                "/memory/rebind-owner",
                self.memory_rebind_owner,
                ["POST"],
                "MemoryCompanion Page memory owner rebind",
            ),
            (
                "/memory/delete",
                self.memory_delete,
                ["POST"],
                "MemoryCompanion Page memory delete",
            ),
            (
                "/memory/visibility",
                self.memory_visibility,
                ["POST"],
                "MemoryCompanion Page memory visibility",
            ),
            (
                "/memory/lifecycle",
                self.memory_lifecycle,
                ["POST"],
                "MemoryCompanion Page memory lifecycle",
            ),
            (
                "/core-memory",
                self.core_memory_list,
                ["GET"],
                "MemoryCompanion core memory blocks",
            ),
            (
                "/core-memory/upsert",
                self.core_memory_upsert,
                ["POST"],
                "MemoryCompanion core memory upsert",
            ),
            (
                "/core-memory/delete",
                self.core_memory_delete,
                ["POST"],
                "MemoryCompanion core memory delete",
            ),
            ("/acl", self.acl, ["GET"], "MemoryCompanion Page memory ACL rules"),
            (
                "/acl/upsert",
                self.acl_upsert,
                ["POST"],
                "MemoryCompanion Page memory ACL upsert",
            ),
            (
                "/acl/policy",
                self.acl_policy,
                ["POST"],
                "MemoryCompanion Page memory ACL policy",
            ),
            (
                "/acl/delete",
                self.acl_delete,
                ["POST"],
                "MemoryCompanion Page memory ACL delete",
            ),
            ("/search", self.search, ["POST"], "MemoryCompanion Page search"),
            ("/timeline", self.timeline, ["GET"], "MemoryCompanion Page timeline"),
            ("/relations", self.relations, ["GET"], "MemoryCompanion Page relations"),
            ("/graph", self.graph, ["GET"], "MemoryCompanion Page knowledge graph"),
            ("/threads", self.threads, ["GET"], "MemoryCompanion Page threads"),
            (
                "/thread/status",
                self.thread_status,
                ["POST"],
                "MemoryCompanion Page thread status",
            ),
            ("/logs", self.logs, ["GET"], "MemoryCompanion Page injection logs"),
            (
                "/context/config",
                self.context_config,
                ["GET"],
                "MemoryCompanion Page context config",
            ),
            (
                "/config/schema",
                self.config_schema,
                ["GET"],
                "MemoryCompanion Page config schema",
            ),
            (
                "/config/module/update",
                self.config_module_update,
                ["POST"],
                "MemoryCompanion Page config module update",
            ),
            (
                "/retrieval/config/update",
                self.retrieval_config_update,
                ["POST"],
                "MemoryCompanion Page retrieval config update",
            ),
            (
                "/operations/diagnostics",
                self.operations_diagnostics,
                ["GET"],
                "MemoryCompanion operations diagnostics",
            ),
            (
                "/emotion/trace",
                self.emotion_trace,
                ["GET"],
                "MemoryCompanion redacted emotion trace",
            ),
            (
                "/emotion/traces",
                self.emotion_traces,
                ["GET"],
                "MemoryCompanion redacted emotion trace list",
            ),
            (
                "/coordination/status",
                self.coordination_status,
                ["GET"],
                "MemoryCompanion safe coordination status",
            ),
            (
                "/operations/preset",
                self.operations_preset,
                ["GET", "POST"],
                "MemoryCompanion operations preset",
            ),
            (
                "/data/export",
                self.data_export,
                ["POST"],
                "MemoryCompanion portable data export",
            ),
            (
                "/data/import/preview",
                self.data_import_preview,
                ["GET"],
                "MemoryCompanion portable data preview",
            ),
            (
                "/data/import/run",
                self.data_import_run,
                ["POST"],
                "MemoryCompanion portable data import",
            ),
            (
                "/conversation-import/qq/capabilities",
                self.conversation_import_qq_capabilities,
                ["GET"],
                "MemoryCompanion QQ history capabilities",
            ),
            (
                "/conversation-import/qq/preview",
                self.conversation_import_qq_preview,
                ["POST"],
                "MemoryCompanion QQ history preview",
            ),
            (
                "/conversation-import/upload",
                self.conversation_import_upload,
                ["POST"],
                "MemoryCompanion historical chat upload",
            ),
            (
                "/conversation-import/start",
                self.conversation_import_start,
                ["POST"],
                "MemoryCompanion historical chat start",
            ),
            (
                "/conversation-import/status",
                self.conversation_import_status,
                ["GET"],
                "MemoryCompanion historical chat status",
            ),
            (
                "/conversation-import/targets",
                self.conversation_import_targets,
                ["GET"],
                "MemoryCompanion historical chat private targets",
            ),
            (
                "/conversation-import/pause",
                self.conversation_import_pause,
                ["POST"],
                "MemoryCompanion historical chat pause",
            ),
            (
                "/conversation-import/resume",
                self.conversation_import_resume,
                ["POST"],
                "MemoryCompanion historical chat resume",
            ),
            (
                "/conversation-import/rebind",
                self.conversation_import_rebind,
                ["POST"],
                "MemoryCompanion historical chat rebind",
            ),
            (
                "/conversation-import/rollback",
                self.conversation_import_rollback,
                ["POST"],
                "MemoryCompanion historical chat rollback",
            ),
            ("/profiles", self.profiles, ["GET"], "MemoryCompanion Bot Profiles"),
            (
                "/user-memory-summary",
                self.user_memory_summary,
                ["GET"],
                "MemoryCompanion User Memory workspace summary",
            ),
            (
                "/portrait/profiles",
                self.portrait_profiles,
                ["GET"],
                "MemoryCompanion unified portrait profiles",
            ),
            (
                "/portrait/profile",
                self.portrait_profile,
                ["GET"],
                "MemoryCompanion unified portrait governance detail",
            ),
            (
                "/portrait/govern",
                self.portrait_govern,
                ["POST"],
                "MemoryCompanion unified portrait governance",
            ),
            (
                "/portrait/migration",
                self.portrait_migration,
                ["POST"],
                "MemoryCompanion portrait migration control",
            ),
            (
                "/capabilities/bot-personal",
                self.bot_personal_capabilities,
                ["GET"],
                "MemoryCompanion Bot Personal capability",
            ),
            (
                "/companion/personal-memory",
                self.companion_personal_memory,
                ["GET"],
                "MemoryCompanion Page companion personal memory",
            ),
            (
                "/companion/personal-photo",
                self.companion_personal_photo,
                ["GET"],
                "MemoryCompanion Page companion personal photo",
            ),
            (
                "/companion/personal-photo-data",
                self.companion_personal_photo_data,
                ["GET"],
                "MemoryCompanion Page companion personal photo data",
            ),
            (
                "/maintenance",
                self.maintenance,
                ["POST"],
                "MemoryCompanion Page maintenance",
            ),
            (
                "/maintenance/sleep",
                self.sleep_maintenance,
                ["GET", "POST"],
                "MemoryCompanion Page sleep maintenance",
            ),
            (
                "/maintenance/audit/preview",
                self.audit_preview,
                ["POST"],
                "MemoryCompanion memory audit preview",
            ),
            (
                "/maintenance/audit/status",
                self.audit_status,
                ["GET"],
                "MemoryCompanion memory audit status",
            ),
            (
                "/maintenance/audit/apply",
                self.audit_apply,
                ["POST"],
                "MemoryCompanion memory audit apply",
            ),
            (
                "/maintenance/audit/rollback",
                self.audit_rollback,
                ["POST"],
                "MemoryCompanion memory audit rollback",
            ),
            (
                "/maintenance/repair_livingmemory_content",
                self.repair_livingmemory_content,
                ["POST"],
                "MemoryCompanion Page repair LivingMemory content",
            ),
            (
                "/maintenance/clear_all",
                self.clear_all,
                ["POST"],
                "MemoryCompanion Page clear all memory data",
            ),
            (
                "/maintenance/clear_scope",
                self.clear_scope,
                ["POST"],
                "MemoryCompanion Page clear scoped memory data",
            ),
            (
                "/maintenance/release_summary_batch",
                self.release_summary_batch,
                ["POST"],
                "MemoryCompanion Page release quarantined summary batch",
            ),
            (
                "/import/livingmemory/preview",
                self.import_preview,
                ["GET"],
                "MemoryCompanion Page import preview",
            ),
            (
                "/import/livingmemory/run",
                self.import_run,
                ["POST"],
                "MemoryCompanion Page import run",
            ),
            (
                "/persona-state",
                self.persona_state,
                ["GET"],
                "MemoryCompanion Page persona state",
            ),
            (
                "/acl/matrix",
                self.acl_matrix,
                ["GET"],
                "MemoryCompanion Page ACL matrix",
            ),
        ]

    def register_routes(self) -> None:
        register = self.plugin.context.register_web_api
        for prefix in PAGE_API_PREFIXES:
            for route, handler, methods, desc in self._route_specs():
                register(f"{prefix}{route}", handler, methods, desc)
