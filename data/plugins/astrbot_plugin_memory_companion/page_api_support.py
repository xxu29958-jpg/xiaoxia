from __future__ import annotations

import logging

from .core.capability_probe import CAPABILITY_SNAPSHOT_FIELDS

try:
    from astrbot.api.web import request as astrbot_web_request
except Exception:  # pragma: no cover - optional in isolated page tests.
    astrbot_web_request = None


PLUGIN_NAME = "astrbot_plugin_memory_companion"


PAGE_API_PREFIXES = (f"/{PLUGIN_NAME}/page",)


logger = logging.getLogger("MemoryCompanion.PageAPI")


ENDPOINT_FIELD_CONTRACT: dict[str, tuple[str, ...]] = {
    "/capabilities/bot-personal": CAPABILITY_SNAPSHOT_FIELDS,
    "/companion/personal-memory": (
        "available",
        "plugin_name",
        "reason",
        "daily_plan_enabled",
        "detail_enabled",
        "bridge_available",
        "bridge_state",
        "bridge_reason",
        "selected_date",
        "dates",
        "snapshot",
        "actions",
    ),
    "/conversation-import/qq/capabilities": (
        "available",
        "adapters",
        "limits",
        "source",
    ),
}


COMPANION_PERSONAL_FIELDS = ENDPOINT_FIELD_CONTRACT["/companion/personal-memory"]


QQ_HISTORY_CAPABILITY_FIELDS = ENDPOINT_FIELD_CONTRACT[
    "/conversation-import/qq/capabilities"
]


THEME_NAME_TO_KEY = {
    "黄白游": "huangbaiyou",
    "天缥": "tianpiao",
    "海天霞": "haitianxia",
    "盈盈": "yingying",
    "欧碧": "oubi",
    "青冥": "qingming",
    "紫蒲": "zipu",
    "山岚": "shanlan",
    "窃蓝": "qielan",
    "退红": "tuihong",
    "葱倩": "congqing",
    "月白": "yuebai",
    "墨黪": "mocan",
    "骨缥": "gupiao",
}


THEME_KEYS = set(THEME_NAME_TO_KEY.values())


DEFAULT_THEME_NAME = "月白"


DEFAULT_THEME_KEY = THEME_NAME_TO_KEY[DEFAULT_THEME_NAME]


UI_CONTRACT_VERSION = "memory.page.ui.v2"


UI_MODES = (
    {"id": "standard", "label": "简洁管理"},
    {"id": "cinema", "label": "放映馆界面"},
)


UI_VIEW_ENDPOINTS = {
    "overview": ("/stats", "/buckets"),
    "users": (
        "/memories",
        "/memory",
        "/memory/update",
        "/memory/rebind-owner",
        "/memory/delete",
        "/portrait/profiles",
        "/portrait/profile",
        "/portrait/govern",
        "/maintenance/clear_scope",
    ),
    "groups": (
        "/memories",
        "/memory",
        "/memory/update",
        "/memory/delete",
        "/maintenance/clear_scope",
    ),
    "personal": ("/companion/personal-memory", "/companion/personal-photo-data"),
    "knowledge": (
        "/graph",
        "/relations",
        "/threads",
        "/thread/status",
        "/timeline",
        "/logs",
        "/persona-state",
        "/emotion/traces",
        "/emotion/trace",
    ),
    "microscope": ("/search",),
    "archive": (
        "/core-memory",
        "/core-memory/upsert",
        "/core-memory/delete",
        "/context/config",
        "/config/schema",
        "/config/module/update",
        "/retrieval/config/update",
        "/operations/diagnostics",
        "/operations/preset",
        "/maintenance",
        "/maintenance/audit/preview",
        "/maintenance/audit/status",
        "/maintenance/audit/apply",
        "/maintenance/audit/rollback",
        "/data/export",
        "/data/import/preview",
        "/data/import/run",
        "/conversation-import/targets",
        "/conversation-import/qq/capabilities",
        "/conversation-import/qq/preview",
        "/conversation-import/upload",
        "/conversation-import/start",
        "/conversation-import/status",
        "/conversation-import/pause",
        "/conversation-import/resume",
        "/conversation-import/rebind",
        "/conversation-import/rollback",
        "/maintenance/repair_livingmemory_content",
        "/maintenance/clear_all",
        "/import/livingmemory/preview",
        "/import/livingmemory/run",
        "/acl/matrix",
        "/acl/upsert",
        "/acl/delete",
    ),
}


MEMORY_TYPE_FILTERS = {
    "profile": ("profile", "user_profile", "user_habit"),
    "preference": ("preference", "user_preference"),
    "relationship": (
        "relationship",
        "relationship_claim",
        "relationship_phase_summary",
    ),
    "fact": ("fact", "stable_fact", "user_fact", "manual_memory", "explicit_memory"),
    "event": ("event", "conversation_event", "important_event", "timeline_event"),
    "state": ("state", "current_state", "stable_state", "stable_fact"),
    "promise": ("promise", "promise_memory"),
    "schedule": ("schedule", "schedule_fragment", "daily_digest"),
    "thought": ("thought", "companion_note", "internal_note"),
}


MEMORY_LIFECYCLE_FILTERS = {
    "active": (
        "active",
        "current_window",
        "recent",
        "open_loop",
        "planned_projection",
        "stable_memory",
    ),
    "stable": ("stable", "stable_memory"),
    "fading": ("fading", "recent"),
    "archived": ("archived",),
    "expired": ("expired",),
}


MEMORY_VISIBILITY_FILTERS = {
    "public": ("public", "group_public"),
    "private": ("private", "private_pair"),
    "group_shared": ("group_shared", "group_public"),
    "bot_self": ("bot_self",),
    "restricted": ("restricted", "internal"),
}


UI_ENDPOINT_EXPOSURE = {
    "/ui/capabilities": {
        "exposure": "internal",
        "reason": "前端启动时用于自校验，不作为业务按钮",
    },
    "/memory/visibility": {
        "exposure": "compat",
        "reason": "由统一 memory/update 表单覆盖",
    },
    "/memory/lifecycle": {
        "exposure": "compat",
        "reason": "由统一 memory/update 表单覆盖",
    },
    "/acl": {"exposure": "compat", "reason": "当前权限拓扑使用聚合 matrix 端点"},
    "/acl/policy": {
        "exposure": "advanced",
        "reason": "保留给高级策略调用，普通拓扑按规则管理",
    },
    "/coordination/status": {
        "exposure": "internal",
        "reason": "由个人记忆和统计投影聚合展示",
    },
    "/profiles": {"exposure": "compat", "reason": "旧 Bot profile 查询兼容入口"},
    "/user-memory-summary": {
        "exposure": "compat",
        "reason": "旧用户摘要工作区兼容入口",
    },
    "/portrait/migration": {
        "exposure": "internal",
        "reason": "画像迁移控制，不向普通面板暴露",
    },
    "/capabilities/bot-personal": {"exposure": "internal", "reason": "跨插件能力探测"},
    "/companion/personal-photo": {"exposure": "internal", "reason": "图片代理兼容入口"},
    "/companion/personal-photo-data": {
        "exposure": "visible",
        "reason": "个人相册按需加载的数据端点",
    },
    "/maintenance/sleep": {
        "exposure": "compat",
        "reason": "页面使用统一 maintenance 操作，保留状态/命令兼容",
    },
    # 2.1.0 面板重写删除了一批旧视图入口，下列端点失去前端调用点；
    # 逐条登记保留理由，避免“后端有路由但没有消费方”再次静默发生。
    "/timeline": {
        "exposure": "compat",
        "reason": "旧记忆时间线视图入口，面板改为在 /memories 与 /stats 之上本地聚合，保留兼容读取",
    },
    "/relations": {
        "exposure": "compat",
        "reason": "旧关系视图入口，面板改为按 /memories 的关系记忆分类呈现，保留兼容读取",
    },
    "/graph": {
        "exposure": "compat",
        "reason": "旧知识图谱数据入口，面板改用 /memories 结果本地构图，保留兼容读取",
    },
    "/threads": {
        "exposure": "compat",
        "reason": "旧开放线程列表入口，面板改用 /stats 与 /memories 聚合，保留兼容读取",
    },
    "/thread/status": {
        "exposure": "advanced",
        "reason": "线程开闭状态写入，面板不提供按钮，保留给管理员直接维护未闭环线程",
    },
    "/logs": {
        "exposure": "compat",
        "reason": "注入日志明细，面板只显示 /stats 的条数汇总，保留给排障直连读取",
    },
    "/context/config": {
        "exposure": "compat",
        "reason": "上下文编排配置读取，面板统一走 /config/schema 的 context_orchestration 模块",
    },
    "/retrieval/config/update": {
        "exposure": "compat",
        "reason": "检索配置写入，面板统一走 /config/module/update 的 retrieval 模块",
    },
    "/memory/rebind-owner": {
        "exposure": "compat",
        "reason": "记忆归属改绑，面板统一走 /memory/update 表单，保留兼容修复入口",
    },
    "/core-memory/delete": {
        "exposure": "advanced",
        "reason": "核心记忆删除是危险操作，面板只提供 /core-memory/upsert，保留管理员直连删除",
    },
    "/maintenance/clear_scope": {
        "exposure": "advanced",
        "reason": "按私聊/群聊范围清理记忆是危险操作，面板只暴露 /maintenance/clear_all，保留管理员直连",
    },
    "/emotion/trace": {
        "exposure": "internal",
        "reason": "情绪痕迹诊断只对已绑定 Dashboard 管理员开放，面板以 /persona-state 投影展示",
    },
    "/emotion/traces": {
        "exposure": "internal",
        "reason": "情绪痕迹列表同样只对已绑定 Dashboard 管理员开放，面板不提供按钮",
    },
    "/conversation-import/targets": {
        "exposure": "advanced",
        "reason": "导入目标的私聊候选列表，面板改为手填会话 ID，保留给管理员核对候选窗口",
    },
    "/conversation-import/rebind": {
        "exposure": "advanced",
        "reason": "导入批次重新绑定目标窗口的修复入口，面板只做预览/开始/暂停/继续/回滚",
    },
    "/portrait/profiles": {
        "exposure": "compat",
        "reason": "统一画像档案列表，面板改用 /memories 的画像分类筛选，保留兼容读取",
    },
    "/portrait/profile": {
        "exposure": "compat",
        "reason": "统一画像单档详情，面板改用 /memories 的画像分类筛选，保留兼容读取",
    },
    "/portrait/govern": {
        "exposure": "advanced",
        "reason": "统一画像治理是危险操作，面板不提供按钮，保留管理员直连",
    },
}


UI_DANGEROUS_ENDPOINTS = frozenset(
    {
        "/memory/delete",
        "/core-memory/delete",
        "/portrait/govern",
        "/maintenance/audit/apply",
        "/maintenance/audit/rollback",
        "/data/import/run",
        "/conversation-import/rollback",
        "/maintenance/clear_all",
        "/maintenance/clear_scope",
        "/import/livingmemory/run",
    }
)
