
// diagnostics: one page runtime owns shared state and DOM references.
export function createDiagnostics(app) {
function bridgeReasonText(code) {
  const key = app.common.compact(code);
  if (!key) return "";
  return app.values.REASON_TEXT[key] || key;
}

function healthText(value) {
  const key = app.common.compact(value);
  return app.values.HEALTH_TEXT[key] || key || "未知";
}

function bridgeHealthText(bridge) {
  if (!bridge || typeof bridge !== "object") return "未知";
  return healthText(bridge.health);
}

function runtimeHealthText(runtime) {
  if (!runtime || typeof runtime !== "object") return "未知";
  const reason = bridgeReasonText(runtime.reason_code);
  return reason ? healthText(runtime.health) + " · " + reason : healthText(runtime.health);
}

function companionStatus(capsPayload, personalPayload) {
  const caps = capsPayload && typeof capsPayload === "object" ? capsPayload : {};
  const personal = personalPayload && typeof personalPayload === "object" ? personalPayload : {};
  const installed = caps.companion_installed === true;
  const linked = personal.available === true;
  return {
    installed: installed,
    available: installed && linked,
    pluginName: app.common.compact(personal.plugin_name) || (installed ? app.common.compact(caps.companion_plugin_name) : ""),
    dailyPlanEnabled: installed && linked && personal.daily_plan_enabled === true,
    detailEnabled: installed && linked && personal.detail_enabled === true,
    reason: !installed
      ? "未安装陪伴插件 astrbot_plugin_private_companion"
      : app.common.compact(personal.reason) || bridgeReasonText(personal.bridge_reason) || (linked ? "" : "陪伴插件已安装，桥接尚未就绪"),
  };
}

async function initialize() {
app.values.REASON_TEXT = {
  bridge_status_unavailable: "联动状态读取失败",
  bridge_config_unavailable: "无法读取插件配置",
  bridge_config_unreadable: "配置读取出错",
  bridge_config_invalid: "配置格式不正确",
  bridge_disabled: "联动已在配置中关闭",
  companion_api_unavailable: "未检测到陪伴插件 astrbot_plugin_private_companion",
  companion_p6_producer_unavailable: "陪伴插件未提供状态接口",
  companion_p6_producer_unreadable: "陪伴插件状态接口读取失败",
  companion_p6_producer_stale: "陪伴插件状态已过期",
  companion_p6_unverifiable: "陪伴插件状态无法核实",
  companion_bridge_available: "联动正常",
  manifest_registry_unavailable: "扩展清单不可用",
  coordination_input_unverifiable: "部分联动项无法核实",
  coordination_degraded: "部分联动项降级",
  coordination_ready: "联动正常",
  runtime_compatible: "契约完全兼容",
  runtime_degraded: "契约降级运行",
  runtime_unsupported: "契约版本不受支持",
  runtime_status_unavailable: "运行时状态不可读",
};
app.values.HEALTH_TEXT = { ready: "正常", degraded: "降级", unverifiable: "无法核实" };
}
return { bridgeReasonText, healthText, bridgeHealthText, runtimeHealthText, companionStatus, initialize };
}
