
// galaxyPanel: one page runtime owns shared state and DOM references.
export function createGalaxyPanel(app) {
function toneOfColor(color) {
  const map = {
    "#f472b6": "relation",
    "#60a5fa": "fact",
    "#2dd4bf": "group",
    "#fb923c": "personal",
    "#fcd34d": "gold",
    "#7dd3fc": "external",
    "#a99bff": "private",
  };
  return map[color] || "accent";
}

function starPanelHtml(galaxy) {
  const center = galaxy.center;
  const weights = center.persona_weights && typeof center.persona_weights === "object" ? center.persona_weights : {};
  const weightKeys = Object.keys(weights).filter((key) => Number.isFinite(Number(weights[key])));
  const tags = Array.isArray(center.tags) ? center.tags : [];

  const paramRows = [
    ["记忆 ID", app.common.shortId(center.id)],
    ["类型", app.galaxy.typeLabel(center) + "（" + app.common.compact(center.memory_type) + "）"],
    ["范围", (app.values.SCOPE_META[app.common.compact(center.scope)] ? app.values.SCOPE_META[app.common.compact(center.scope)].label : app.common.compact(center.scope)) || "-"],
    ["主体", app.common.compact(center.subject && center.subject.name) || "-"],
    ["重要性", app.common.num(center.importance)],
    ["置信度", app.common.num(center.confidence)],
    ["显著性", app.common.num(center.salience)],
    ["强化分", app.common.num(center.reinforcement_score)],
    ["注入次数", String(app.common.int(center.injection_count))],
    ["最近注入", app.common.compact(center.last_injected_at) ? app.common.fmtTime(center.last_injected_at) : "从未"],
    ["可见性", app.values.VISIBILITY_LABEL[app.common.compact(center.visibility)] || app.common.compact(center.visibility) || "-"],
    ["生命周期", app.values.LIFECYCLE_LABEL[app.common.compact(center.lifecycle)] || app.common.compact(center.lifecycle) || "-"],
    ["时效", app.values.VALIDITY_LABEL[app.common.compact(center.validity_status)] || app.common.compact(center.validity_status) || "-"],
    ["持久度", app.values.DURABILITY_LABEL[app.common.compact(center.durability)] || app.common.compact(center.durability) || "-"],
    ["敏感度", app.values.SENSITIVITY_LABEL[app.common.compact(center.sensitivity)] || app.common.compact(center.sensitivity) || "-"],
    ["现实层级", app.values.REALITY_LABEL[app.common.compact(center.reality_level)] || app.common.compact(center.reality_level) || "-"],
    ["可提及分", center.mentionability_score === null || center.mentionability_score === undefined ? "-" : app.common.num(center.mentionability_score)],
    ["关系阶段", app.common.compact(center.relationship_phase) || "-"],
    ["来源插件", app.common.compact(center.source_plugin) || "本插件"],
    ["导入批次", app.common.compact(center.import_batch_id) || "-"],
    ["会话", app.common.compact(center.session_id) || "-"],
    ["发生时间", app.common.compact(center.occurred_at_local) || app.common.fmtTime(center.occurred_at)],
    ["更新时间", app.common.compact(center.updated_at_local) || app.common.fmtTime(center.updated_at)],
  ]
    .map((row) => '<div class="param-row"><span>' + app.common.esc(row[0]) + "</span><div>" + app.common.esc(row[1]) + "</div></div>")
    .join("");

  const orbitRows = app.values.ORBIT_TIERS.map((tier, index) => {
    const nodes = galaxy.nodes.filter((node) => node.tier === index);
    return (
      '<div style="margin-bottom:4px"><div class="orbit-row" style="cursor:default;background:transparent;padding:4px 2px">' +
      '<i style="background:' + tier.color + '"></i><b style="color:var(--text-2)">' + app.common.esc(tier.label) + "</b>" +
      "<span>" + nodes.length + " 颗卫星</span></div>" +
      (nodes.length
        ? '<div style="display:grid;gap:3px;padding-left:14px">' +
          nodes
            .slice(0, 6)
            .map(
              (node) =>
                '<button class="orbit-row" type="button" data-star-open="' + app.common.esc(node.id) + '">' +
                '<i style="background:' + node.color + '"></i>' +
                "<span>" + app.common.esc(app.common.clip(app.records.memoryTitle(node.memory), 22)) + "</span>" +
                "<em>" + app.common.num(node.score) + "</em></button>"
            )
            .join("") +
          "</div>"
        : "") +
      "</div>"
    );
  }).join("");

  return (
    '<section class="star-core">' +
      '<span class="core-kicker">当前恒星 · 关联 ' + galaxy.nodes.length + " 条</span>" +
      '<h3 class="core-title">' + app.common.esc(app.common.clip(app.records.memoryTitle(center), 46)) + "</h3>" +
      '<p class="core-content">' + app.common.esc(app.common.clip(app.common.compact(center.content), 220)) + "</p>" +
      '<div class="pill-row" style="margin-top:11px;position:relative">' +
        app.widgets.badge(app.galaxy.typeLabel(center), toneOfColor(app.galaxy.satelliteColor(center))) +
        app.widgets.badge("重要 " + app.common.num(center.importance), "accent") +
        '<button class="btn is-sm is-ghost" type="button" data-star-open="' + app.common.esc(center.id) + '">完整参数</button>' +
      "</div>" +
    "</section>" +
    app.widgets.card(
      "选中记忆 · 参数明细",
      weightKeys.length ? "含 " + weightKeys.length + " 项人格权重" : "基础属性",
      '<div class="star-params">' + paramRows + "</div>" +
        (weightKeys.length
          ? '<div class="tag-row" style="margin-top:10px">' + weightKeys.map((key) => app.widgets.badge((app.values.WEIGHT_LABEL[key] || key) + " " + app.common.num(weights[key]), "accent")).join("") + "</div>"
          : "") +
        (tags.length ? '<div class="tag-row" style="margin-top:8px">' + tags.map((tag) => app.widgets.badge(tag, "fact")).join("") + "</div>" : "")
    ) +
    app.widgets.card("星轨构成", "点击任一行查看该记忆", orbitRows || app.widgets.emptyState("暂无关联记忆", "这条记忆与其他记忆没有足够的属性重合。"))
  );
}

async function initialize() {
app.values.microState = { query: "", scope: "all", userId: "", groupId: "", topK: 8 };
}
return { toneOfColor, starPanelHtml, initialize };
}
