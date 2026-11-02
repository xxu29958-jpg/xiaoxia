from __future__ import annotations

from quart import request

from .core.models import clean_text
from .page_api_support import (
    DEFAULT_THEME_NAME,
    THEME_NAME_TO_KEY,
    UI_CONTRACT_VERSION,
    UI_DANGEROUS_ENDPOINTS,
    UI_ENDPOINT_EXPOSURE,
    UI_MODES,
    UI_VIEW_ENDPOINTS,
    logger,
)


class PluginPageApiProfiles:
    """Profiles; shared runtime resources belong to the facade."""

    async def ui_capabilities(self):
        routes = self._route_specs()
        endpoint_views: dict[str, list[str]] = {}
        for view, endpoints in UI_VIEW_ENDPOINTS.items():
            for endpoint in endpoints:
                endpoint_views.setdefault(endpoint, []).append(view)
        return self._ok(
            {
                "contract_version": UI_CONTRACT_VERSION,
                "modes": list(UI_MODES),
                "views": [
                    {"id": view, "endpoints": list(endpoints)}
                    for view, endpoints in UI_VIEW_ENDPOINTS.items()
                ],
                "endpoints": [
                    {
                        "path": path,
                        "methods": list(methods),
                        "description": description,
                        "exposure": UI_ENDPOINT_EXPOSURE.get(path, {}).get(
                            "exposure", "visible"
                        ),
                        "reason": UI_ENDPOINT_EXPOSURE.get(path, {}).get(
                            "reason", "面板直接功能"
                        ),
                        "views": endpoint_views.get(path, []),
                        "dangerous": path in UI_DANGEROUS_ENDPOINTS,
                    }
                    for path, _handler, methods, description in routes
                ],
            }
        )

    async def ui_preferences(self):
        """拓展页首屏要用的偏好：走哪套界面 + 用哪套配色。

        ``palette`` 必须在这里返回，而不是让面板自己再去读配置：``index.html`` 是在
        跳转**之前**调这个端点的，拿到就能先把 ``data-palette`` 打到 ``<html>`` 上，
        于是配色在第一帧就是对的，不会有「先紫后青」那种闪。
        """
        config = self.plugin.service.config
        style = clean_text(config.get("appearance.ui_style", "旧版"), 20).lower()
        modern = style in {"modern", "new", "新版"}
        theme_name = str(config.get("appearance.theme", DEFAULT_THEME_NAME))
        return self._ok(
            {
                "ui_style": "modern" if modern else "legacy",
                "palette": self._theme_key(theme_name),
                "theme": theme_name,
                "available_palettes": list(THEME_NAME_TO_KEY.keys()),
            }
        )

    async def stats(self):
        stats = await self.plugin.service.store.stats()
        stats.pop("pending_review", None)
        stats["summary_progress"] = await self.plugin.service.store.summary_progress()
        return self._ok({"stats": stats})

    async def profiles(self):
        profile = clean_text(request.args.get("profile", ""), 80)
        query = clean_text(request.args.get("query", ""), 240)
        current_date = clean_text(request.args.get("date", ""), 20)
        current_window = clean_text(request.args.get("window", ""), 40)
        try:
            limit = max(1, min(100, int(request.args.get("limit", "10"))))
        except (TypeError, ValueError):
            limit = 10
        result = await self.plugin.service.read_bot_profile(
            profile,
            query=query,
            limit=limit,
            current_date=current_date,
            current_window=current_window,
            authorized=False,
        )
        return self._ok({"data": result})

    async def user_memory_summary(self):
        user_id = clean_text(request.args.get("user_id", ""), 120)
        session_id = clean_text(request.args.get("session_id", ""), 200)
        try:
            limit = max(1, min(8, int(request.args.get("limit", "6"))))
        except (TypeError, ValueError):
            limit = 6
        reader = getattr(
            getattr(self.plugin, "service", None), "read_user_memory_summary", None
        )
        if not callable(reader):
            result = {
                "contract": "memory.user_memory_summary.v1",
                "ok": False,
                "read_only": True,
                "state": "degraded",
                "degraded": True,
                "pending": True,
                "user_id": user_id,
                "session_id": session_id,
                "counts": {
                    "profile": 0,
                    "preference": 0,
                    "relationship": 0,
                    "private_conversation": 0,
                    "other": 0,
                    "total": 0,
                },
                "summaries": [],
                "workspace": {
                    "kind": "memory_user_workspace",
                    "route_hint": "user_memory",
                    "user_id": user_id,
                },
                "error_code": "bridge_method_unavailable",
            }
        else:
            try:
                result = await reader(user_id, session_id=session_id, limit=limit)
            except Exception:
                result = {
                    "contract": "memory.user_memory_summary.v1",
                    "ok": False,
                    "read_only": True,
                    "state": "degraded",
                    "degraded": True,
                    "pending": True,
                    "user_id": user_id,
                    "session_id": session_id,
                    "counts": {
                        "profile": 0,
                        "preference": 0,
                        "relationship": 0,
                        "private_conversation": 0,
                        "other": 0,
                        "total": 0,
                    },
                    "summaries": [],
                    "workspace": {
                        "kind": "memory_user_workspace",
                        "route_hint": "user_memory",
                        "user_id": user_id,
                    },
                    "error_code": "summary_unavailable",
                }
        return self._ok({"data": result})

    async def portrait_profiles(self):
        try:
            limit = max(1, min(500, int(request.args.get("limit", "100"))))
        except (TypeError, ValueError):
            limit = 100
        portraits = getattr(getattr(self.plugin, "service", None), "portraits", None)
        if portraits is None:
            return self._ok({"items": [], "state": "bridge_unavailable"})
        try:
            items = await portraits.list_governance_profiles(limit=limit)
        except Exception:
            logger.exception("统一画像列表读取失败")
            return self._err("统一画像列表读取失败", 500)
        return self._ok({"items": items, "state": "ready"})

    async def portrait_profile(self):
        person_id = clean_text(request.args.get("person_id", ""), 80)
        if not person_id:
            return self._err("缺少 person_id", 400)
        portraits = getattr(getattr(self.plugin, "service", None), "portraits", None)
        if portraits is None:
            return self._ok(
                {
                    "data": {
                        "ok": False,
                        "code": "bridge_unavailable",
                        "person": {},
                        "facts": [],
                        "suppressions": [],
                    }
                }
            )
        try:
            result = await portraits.governance_detail(person_id)
        except Exception:
            logger.exception("统一画像详情读取失败")
            return self._err("统一画像详情读取失败", 500)
        return self._ok({"data": result})

    async def portrait_govern(self):
        payload = await self._json()
        portraits = getattr(getattr(self.plugin, "service", None), "portraits", None)
        if portraits is None:
            return self._err("统一画像服务不可用", 503)
        try:
            result = await portraits.govern_fact(
                person_id=clean_text(payload.get("person_id"), 80),
                fact_id=clean_text(payload.get("fact_id"), 120),
                action=clean_text(payload.get("action"), 40),
                actor="page_administrator",
                operation_id=clean_text(payload.get("operation_id"), 120),
                expires_at=clean_text(payload.get("expires_at"), 80),
            )
        except Exception:
            logger.exception("统一画像治理失败")
            return self._err("统一画像治理失败", 500)
        return self._ok({"data": result})

    async def portrait_migration(self):
        payload = await self._json()
        mode = clean_text(payload.get("mode"), 40) or "dry_run"
        operation_id = clean_text(payload.get("operation_id"), 120)
        portraits = getattr(getattr(self.plugin, "service", None), "portraits", None)
        if portraits is None:
            return self._err("统一画像服务不可用", 503)
        if not operation_id:
            return self._err("缺少 operation_id", 400)
        try:
            if mode == "dry_run":
                result = await portraits.migrate_legacy(
                    operation_id=operation_id, dry_run=True
                )
            elif mode == "apply":
                result = await portraits.migrate_legacy(
                    operation_id=operation_id, dry_run=False
                )
            elif mode == "rollback":
                result = await portraits.rollback_legacy_migration(
                    operation_id=operation_id
                )
            else:
                return self._err("无效的迁移模式", 400)
        except Exception:
            logger.exception("统一画像迁移操作失败")
            return self._err("统一画像迁移操作失败", 500)
        return self._ok({"data": result})
