
// capabilities: one page runtime owns shared state and DOM references.
export function createCapabilities(app) {
async function initialize() {
app.navigation.defineView("companion", {
  title: "陪伴插件联动",
  navLabel: "陪伴插件",
  eyebrow: "Bridge · Companion",
  hint: "主动陪伴插件的桥接状态、能力开关与个人记忆产出",
  async load() {
    const [caps, coord, personal] = await Promise.all([
      app.api.apiTry(() => app.api.apiGet("/capabilities/bot-personal"), null),
      app.api.apiTry(() => app.api.apiGet("/coordination/status"), { status: {} }),
      app.api.apiTry(() => app.api.apiGet("/companion/personal-memory?limit=40"), null),
    ]);
    return { caps, coord: (coord && coord.status) || {}, personal };
  },
  render(data) {
    const coord = data.coord || {};
    const bridge = coord.bridge || {};
    const status = app.diagnostics.companionStatus(data.caps, data.personal);

    const installed = status.installed;
    const offOr = (ok, warn) => (installed ? (ok ? "is-ok" : warn ? "is-warn" : "is-bad") : "is-off");

    const items = [];
    items.push([
      installed ? (status.available ? "is-ok" : "is-warn") : "is-off",
      "插件安装",
      installed ? status.pluginName || "已安装" : "未安装 astrbot_plugin_private_companion",
    ]);
    items.push([
      !installed ? "is-off" : bridge.health === "ready" ? "is-ok" : bridge.health === "degraded" ? "is-warn" : "is-bad",
      "桥接状态",
      !installed ? "需先安装陪伴插件" : app.diagnostics.bridgeHealthText(bridge),
    ]);
    items.push([
      offOr(status.dailyPlanEnabled, true),
      "每日日程",
      installed ? (status.dailyPlanEnabled ? "已启用" : "未启用") : "不可用",
    ]);
    items.push([
      offOr(status.detailEnabled, true),
      "细化增强",
      installed ? (status.detailEnabled ? "已启用" : "未启用") : "不可用",
    ]);
    if (installed && data.personal && data.personal.available) {
      items.push(["is-ok", "可选日期", (data.personal.dates || []).length + " 天有记录"]);
      items.push(["is-ok", "相册", ((data.personal.snapshot || {}).album || []).length + " 张"]);
    }

    const bridgeReason = app.common.compact(bridge.reason_code);
    const p6 = coord.p6 || null;
    const runtime = coord.runtime || {};
    const runtimeLabel = app.diagnostics.runtimeHealthText(runtime);

    return (
      '<div class="grid" style="gap:16px">' +
      '<div class="grid split-2">' +
        app.widgets.card(
          "联动状态",
          !installed ? "未安装陪伴插件" : status.available ? "已连接" : "已安装但未连通",
          '<div class="row-list" style="gap:6px">' +
            items.map((item) => '<div class="link-item ' + item[0] + '"><span class="link-dot"></span><b>' + app.common.esc(item[1]) + "</b><span>" + app.common.esc(item[2]) + "</span></div>").join("") +
            "</div>" +
            (bridgeReason && installed
              ? '<p class="section-note"' + (app.values.REASON_TEXT[bridgeReason] ? ' title="' + app.common.esc(bridgeReason) + '"' : "") + ">"
                + app.common.esc(app.values.REASON_TEXT[bridgeReason] || bridgeReason) + "</p>"
              : !installed ? '<p class="section-note">未安装陪伴插件 astrbot_plugin_private_companion。记忆与注入照常工作，日程、相册与表达权威归属需要它。安装并启用后本页会自动显示真实状态。</p>' : "") +
            (installed
              ? '<div class="pill-row" style="margin-top:12px"><button class="btn is-sm" type="button" data-goto="botlife">查看 Bot 日程与相册</button></div>'
              : "")
        ) +
        app.widgets.card(
          "协调契约",
          "只读投影 · 不在本插件持久化",
          '<dl class="kv">' +
            "<dt>契约版本</dt><dd>" + app.common.esc(app.common.compact(coord.schema_version) || "-") + "</dd>" +
            "<dt>契约指纹</dt><dd>" + app.common.esc(app.common.compact(coord.contract_fingerprint) || "-") + "</dd>" +
            "<dt>兼容等级</dt><dd>" + app.common.esc(runtimeLabel) + "</dd>" +
            "<dt>桥接状态</dt><dd>" + app.common.esc(app.diagnostics.bridgeHealthText(bridge)) + "</dd>" +
            (installed
              ? "<dt>表达权威</dt><dd>private_companion</dd>"
              : "<dt>表达权威</dt><dd>无（未安装陪伴插件）</dd>") +
            "<dt>记忆角色</dt><dd>召回可见性与提及上限</dd>" +
            (p6 ? "<dt>P6 状态</dt><dd>" + app.common.esc(app.common.compact(p6.health) || app.common.compact(p6.state) || "-") + "</dd>" : "") +
            "</dl>"
        ) +
      "</div>" +
      (p6
        ? app.widgets.card("P6 只读状态", "由陪伴插件产出", '<pre style="margin:0;font-size:11.5px;line-height:1.6;color:var(--text-2);white-space:pre-wrap;word-break:break-all">' + app.common.esc(app.microscope.jsonPreview(p6)) + "</pre>")
        : "") +
      "</div>"
    );
  },
});
}
return { initialize };
}
