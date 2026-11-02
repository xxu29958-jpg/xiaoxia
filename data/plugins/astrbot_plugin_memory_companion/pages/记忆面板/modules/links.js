
// links: one page runtime owns shared state and DOM references.
export function createLinks(app) {
async function initialize() {
app.navigation.defineView("synergy", {
  title: "互动协同",
  navLabel: "互动协同",
  eyebrow: "Insight · Synergy",
  hint: "表达权威归属、记忆触动趋势与情绪连续性 —— 只读，由陪伴插件主导",
  async load() {
    const [persona, coord] = await Promise.all([
      app.api.apiTry(() => app.api.apiGet("/persona-state"), null),
      app.api.apiTry(() => app.api.apiGet("/coordination/status"), { status: {} }),
    ]);
    return { persona, coord: (coord && coord.status) || {} };
  },
  render(data) {
    const persona = data.persona;
    if (!persona) {
      return app.widgets.card("互动协同不可用", "读取失败", app.widgets.emptyState("无法读取互动协同数据", "请确认插件运行正常后重试。"));
    }
    const expr = persona.expression_coordination || {};
    const trends = Array.isArray(persona.memory_touch_trends) ? persona.memory_touch_trends : [];
    const events = Array.isArray(persona.memory_touch_events) ? persona.memory_touch_events : [];
    const cross = persona.cross_window_emotional_state || {};
    const legacyLabels = persona.legacy_context_labels || {};
    const todLabels = persona.time_of_day_labels || {};

    const authority =
      '<dl class="kv">' +
      "<dt>契约</dt><dd>" + app.common.esc(app.common.compact(expr.contract) || "-") + "</dd>" +
      "<dt>模式</dt><dd>" + app.common.esc(app.common.compact(expr.mode) || "-") + "</dd>" +
      "<dt>表达权威</dt><dd>" + app.widgets.badge(app.common.compact(expr.expression_authority) || "-", "accent") + "</dd>" +
      "<dt>记忆角色</dt><dd>" + app.common.esc(app.common.compact(expr.memory_role) || "-") + "</dd>" +
      "<dt>是否持久化</dt><dd>" + (expr.persistent ? "是" : "否（请求级只读）") + "</dd>" +
      "</dl>";

    const trendRows = trends.length
      ? trends
          .slice(0, 10)
          .map((item) => {
            const tone = item.trend_band === "rising" ? "ok" : item.trend_band === "cooling" ? "warn" : "";
            const bandLabel = item.trend_band === "rising" ? "升温" : item.trend_band === "cooling" ? "降温" : "平稳";
            return (
              '<button class="row" type="button" style="cursor:default">' +
              '<div class="row-main"><div class="row-title">' + app.common.esc(app.common.clip(app.common.compact(item.session_label), 46)) + "</div>" +
              '<div class="row-sub">' + app.common.esc(legacyLabels[app.common.compact(item.legacy_context)] || app.common.compact(item.legacy_context) || "未定阶段") +
              " · 触动 " + app.common.int(item.touch_count) + " 次</div></div>" +
              '<div class="row-meta">' + app.widgets.badge(bandLabel, tone) + app.widgets.badge(app.common.fmtTime(item.updated_at)) + "</div></button>"
            );
          })
          .join("")
      : app.widgets.emptyState("暂无触动趋势", "还没有记录到记忆被表达层触动的趋势。");

    const eventRows = events.length
      ? events
          .slice(0, 10)
          .map(
            (item) =>
              '<div class="row" style="cursor:default;align-items:flex-start"><div class="row-main">' +
              '<div class="row-title">' + app.common.esc(app.common.compact(item.event_type) || "情绪事件") + " · " + app.common.esc(app.common.compact(item.mood_hint) || "—") + "</div>" +
              '<div class="row-sub">' + app.common.esc(app.common.clip(app.common.compact(item.content_preview), 56)) + (app.common.compact(item.energy_delta) ? " · 能量 " + app.common.num(item.energy_delta) : "") + "</div>" +
              "</div>" + app.widgets.badge(app.common.shortId(item.session_id)) + "</div>"
          )
          .join("")
      : app.widgets.emptyState("暂无情绪事件", "近期没有记录到情绪事件。");

    const crossCards = [
      app.widgets.kpi("跨窗口总量", app.common.fmtInt(cross.total), "", "accent"),
      app.widgets.kpi("伤痕", app.common.fmtInt(cross.scar_count), "scar", "relation"),
      app.widgets.kpi("温暖", app.common.fmtInt(cross.warm_count), "warm", "personal"),
      app.widgets.kpi("脆弱", app.common.fmtInt(cross.vulnerable_count), "vulnerable", "fact"),
    ].join("");

    const coordStatus = data.coord || {};
    const bridge = coordStatus.bridge || {};
    const links = [
      bridge.health === "ready" ? "is-ok" : bridge.health === "degraded" ? "is-warn" : "is-bad",
      "陪伴桥接",
      app.common.compact(bridge.reason_code) || app.common.compact(bridge.health) || "未知",
    ];

    return (
      '<div class="grid" style="gap:16px">' +
      '<div class="section-label"><h2>表达协同</h2><span class="section-note">当前时段：' + app.common.esc(todLabels[app.common.compact(persona.time_of_day)] || app.common.compact(persona.time_of_day) || "未知") + "</span></div>" +
      '<div class="grid split-2">' +
        app.widgets.card("表达权威", "谁决定怎么说话", authority) +
        '<div style="display:grid;gap:16px">' +
          app.widgets.card("记忆触动趋势", trends.length + " 个窗口", '<div class="row-list">' + trendRows + "</div>") +
          app.widgets.card("联动状态", "", '<div class="row-list"><div class="link-item ' + links[0] + '"><span class="link-dot"></span><b>' + app.common.esc(links[1]) + "</b><span>" + app.common.esc(links[2]) + "</span></div></div>") +
        "</div>" +
      "</div>" +
      '<div class="kpi-row">' + crossCards + "</div>" +
      app.widgets.card("近期情绪事件", events.length + " 条 · 只读投影", '<div class="row-list">' + eventRows + "</div>") +
      "</div>"
    );
  },
});
}
return { initialize };
}
