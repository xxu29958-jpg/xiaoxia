from __future__ import annotations

from typing import Any

from quart import request

from .companion_page_bridge import (
    CompanionPageBridgeError,
)
from .core.coordination_status import build_coordination_status, project_p6_status
from .core.models import clean_text
from .page_api_support import astrbot_web_request


class PluginPageApiDiagnostics:
    """Diagnostics; shared runtime resources belong to the facade."""

    async def bot_personal_capabilities(self):
        getter = getattr(self.plugin, "bot_personal_capability_status", None)
        result = (
            getter()
            if callable(getter)
            else {
                "available": False,
                "state": "degraded",
                "degraded": True,
                "warnings": ["capability_status_unavailable"],
            }
        )
        return self._ok({"data": result if isinstance(result, dict) else {}})

    async def operations_diagnostics(self):
        try:
            return self._ok(
                {"diagnostics": await self.plugin.service.operational_report()}
            )
        except Exception as exc:
            return self._err(f"运维诊断失败: {exc}", 500)

    async def emotion_trace(self):
        context = self._trusted_emotion_admin_context()
        if context is None:
            return self._err("admin_required", 403)
        trace_id = clean_text(request.args.get("trace_id", ""), 96)
        if not trace_id:
            return self._err("缺少 trace_id", 400)
        bridge = getattr(self.plugin, "memory_companion", None)
        getter = getattr(bridge, "get_emotion_trace_diagnostic", None)
        if not callable(getter):
            return self._ok(
                {
                    "data": {
                        "state": "degraded",
                        "read_only": True,
                        "items": [],
                        "error_code": "bridge_method_unavailable",
                    }
                }
            )
        return self._ok({"data": await getter(trace_id, context, limit=100)})

    async def emotion_traces(self):
        context = self._trusted_emotion_admin_context()
        if context is None:
            return self._err("admin_required", 403)
        bridge = getattr(self.plugin, "memory_companion", None)
        getter = getattr(bridge, "get_emotion_trace_summary", None)
        if not callable(getter):
            return self._ok(
                {
                    "data": {
                        "state": "degraded",
                        "read_only": True,
                        "items": [],
                        "error_code": "bridge_method_unavailable",
                    }
                }
            )
        return self._ok(
            {
                "data": await getter(
                    context,
                    cursor=clean_text(request.args.get("cursor", ""), 20),
                    limit=max(
                        1, min(100, self._int(request.args.get("limit", "20"), 20))
                    ),
                )
            }
        )

    def _trusted_emotion_admin_context(self) -> Any | None:
        """Construct diagnostic authority only from AstrBot's bound dashboard user."""

        if not self._is_bound_dashboard_admin():
            return None
        bridge = getattr(self.plugin, "memory_companion", None)
        creator = getattr(bridge, "create_emotion_admin_context", None)
        if not callable(creator) or self._emotion_page_admin_capability is None:
            return None
        try:
            return creator(
                self._emotion_page_admin_capability,
                bot_id=clean_text(request.args.get("bot_id", ""), 160),
                scope=clean_text(request.args.get("scope", ""), 24),
                session_id=clean_text(request.args.get("session_id", ""), 220),
            )
        except Exception:
            return None

    def _is_bound_dashboard_admin(self) -> bool:
        """Accept only the framework-injected dashboard user, never request claims."""

        bound_request = astrbot_web_request
        if bound_request is None:
            return False
        try:
            username = clean_text(bound_request.username, 160)
        except Exception:
            return False
        context = getattr(self.plugin, "context", None)
        config_getter = getattr(context, "get_config", None)
        if not callable(config_getter):
            return False
        try:
            config = config_getter()
        except Exception:
            return False
        dashboard = config.get("dashboard") if isinstance(config, dict) else None
        expected_username = clean_text(
            dashboard.get("username") if isinstance(dashboard, dict) else "",
            160,
        )
        return bool(username and expected_username and username == expected_username)

    async def coordination_status(self):
        """Expose a fixed, read-only coordination projection."""
        try:
            service = getattr(self.plugin, "service", None)
            p6_raw, bridge = self._companion_p6_status(service)
            status = build_coordination_status(
                config=getattr(service, "config", None),
                runtime={"compatibility_level": self._compatibility_level(bridge)},
                bridge=bridge,
                p6_raw=p6_raw,
            )
        except Exception:
            status = build_coordination_status(
                config=None,
                runtime=None,
                bridge={
                    "health": "unverifiable",
                    "reason_code": "bridge_status_unavailable",
                },
                p6_raw=None,
            )
        return self._ok({"status": status})

    @staticmethod
    def _compatibility_level(bridge: Any) -> str:
        """兼容等级必须跟着桥接的实际状态走。

        以前这里写死 ``{"compatibility_level": "full"}``，于是不管陪伴插件装没装，
        面板「协调契约 · 兼容等级」恒显「正常 / 契约完全兼容」——和旁边那行
        「桥接状态：无法核实」自相矛盾，也是在替用户宣布一个没验证过的结论。
        桥接真的通了才算完全兼容，其余一律降级。
        """
        health = bridge.get("health") if isinstance(bridge, dict) else ""
        return "full" if health == "ready" else "degraded"

    def _companion_p6_status(self, service: Any) -> tuple[Any, dict[str, str]]:
        config = getattr(service, "config", None)
        getter = getattr(config, "bool", None)
        if not callable(getter):
            return None, {
                "health": "unverifiable",
                "reason_code": "bridge_config_unavailable",
            }
        try:
            enabled = getter("private_companion_bridge.enabled", True)
        except Exception:
            return None, {
                "health": "unverifiable",
                "reason_code": "bridge_config_unreadable",
            }
        if type(enabled) is not bool:
            return None, {
                "health": "unverifiable",
                "reason_code": "bridge_config_invalid",
            }
        if not enabled:
            return None, {"health": "degraded", "reason_code": "bridge_disabled"}
        try:
            p6_raw = self._companion_page_bridge.read_p6_status()
        except CompanionPageBridgeError as exc:
            reason = exc.code
            if reason == "companion_p6_producer_stale":
                reason = "companion_p6_producer_unreadable"
            elif not reason.startswith("companion_p6_"):
                reason = "companion_api_unavailable"
            return None, {"health": "unverifiable", "reason_code": reason}
        p6 = project_p6_status(p6_raw)
        if p6["health"] == "ready":
            return p6_raw, {
                "health": "ready",
                "reason_code": "companion_bridge_available",
            }
        return p6_raw, {
            "health": "degraded",
            "reason_code": "companion_p6_unverifiable",
        }

    async def operations_preset(self):
        if request.method == "GET":
            return self._ok({"preset": self.plugin.service.operation_preset_status()})
        payload = await self._json()
        try:
            result = self.plugin.service.apply_operation_preset(
                clean_text(payload.get("preset"), 40)
            )
            return self._ok({"preset": result})
        except ValueError as exc:
            return self._err(str(exc), 400)
        except Exception as exc:
            return self._err(f"应用预设失败: {exc}", 500)

    async def data_export(self):
        try:
            return self._ok({"data": await self.plugin.service.export_portable_data()})
        except Exception as exc:
            return self._err(f"导出失败: {exc}", 500)

    async def data_import_preview(self):
        path = clean_text(request.args.get("path", ""), 2000)
        if not path:
            return self._err("path is required", 400)
        try:
            return self._ok({"data": self.plugin.service.preview_portable_data(path)})
        except (OSError, ValueError) as exc:
            return self._err(str(exc), 400)

    async def data_import_run(self):
        payload = await self._json()
        path = clean_text(payload.get("path"), 2000)
        if not path:
            return self._err("path is required", 400)
        try:
            return self._ok(
                {"data": await self.plugin.service.import_portable_data(path)}
            )
        except (OSError, ValueError) as exc:
            return self._err(str(exc), 400)
        except Exception as exc:
            return self._err(f"导入失败: {exc}", 500)
