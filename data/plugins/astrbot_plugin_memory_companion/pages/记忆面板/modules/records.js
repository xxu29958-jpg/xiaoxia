
// records: one page runtime owns shared state and DOM references.
export function createRecords(app) {
function memoryTone(memory) {
  const scope = app.common.compact(memory.scope);
  if (scope === "private") return "private";
  if (scope === "group") return "group";
  const type = app.common.compact(memory.memory_type);
  if (type === "relationship") return "relation";
  if (type === "preference" || type === "profile") return "relation";
  if (type === "fact" || type === "state") return "fact";
  if (app.common.compact(memory.source_plugin) && app.common.compact(memory.source_plugin) !== "self") return "external";
  return "";
}

function memoryTitle(memory) {
  const summary = app.common.compact(memory.canonical_summary);
  if (summary) return summary;
  const content = app.common.compact(memory.content);
  if (!content) return "（空内容）";
  const firstLine = content.split(/\r?\n/)[0].trim();
  return firstLine || content.slice(0, 60);
}

function memoryRow(memory, index) {
  const tone = memoryTone(memory);
  const title = memoryTitle(memory);
  const typeLabel = app.values.TYPE_LABEL[app.common.compact(memory.memory_type)] || app.common.compact(memory.memory_type) || "记忆";
  const scopeLabel = app.values.SCOPE_META[app.common.compact(memory.scope)] ? app.values.SCOPE_META[app.common.compact(memory.scope)].label : app.common.compact(memory.scope) || "未分类";
  const parts = [typeLabel, scopeLabel];
  if (app.common.compact(memory.subject && memory.subject.name)) parts.push(app.common.compact(memory.subject.name));
  if (app.common.compact(memory.updated_at_local)) parts.push(app.common.compact(memory.updated_at_local));
  return (
    '<button class="row" type="button" data-memory="' + app.common.esc(memory.id) + '">' +
    '<div class="row-main"><div class="row-title">' + app.common.esc(app.common.clip(title, 96)) + "</div>" +
    '<div class="row-sub">' + app.common.esc(parts.join(" · ")) + "</div></div>" +
    '<div class="row-meta">' +
    (tone ? app.widgets.badge(scopeLabel, tone) : "") +
    app.widgets.badge("重要 " + app.common.num(app.common.compact(memory.importance, 0)), "accent") +
    "</div></button>"
  );
}

function memoryDetailHtml(memory) {
  const type = app.common.compact(memory.memory_type);
  const scope = app.common.compact(memory.scope);
  const tone = memoryTone(memory);
  const weights = memory.persona_weights && typeof memory.persona_weights === "object" ? memory.persona_weights : {};
  const weightKeys = Object.keys(weights).filter((key) => Number.isFinite(Number(weights[key])));
  const tags = Array.isArray(memory.tags) ? memory.tags : [];
  const topics = Array.isArray(memory.topics) ? memory.topics : [];
  const facts = Array.isArray(memory.key_facts) ? memory.key_facts : [];
  const range = memory.time_range && typeof memory.time_range === "object" ? memory.time_range : {};

  const section = (title, body) =>
    body ? '<div class="drawer-section"><h4>' + app.common.esc(title) + "</h4>" + body + "</div>" : "";

  const kv = (pairs) =>
    '<dl class="kv">' +
    pairs
      .filter((pair) => app.common.compact(pair[1]) !== "")
      .map((pair) => "<dt>" + app.common.esc(pair[0]) + "</dt><dd>" + app.common.esc(pair[1]) + "</dd>")
      .join("") +
    "</dl>";

  const rows = [
    section(
      "内容",
      '<div class="drawer-content">' + app.common.esc(app.common.compact(memory.content, "（空）")) + "</div>"
    ),
    section(
      "摘要",
      app.common.compact(memory.canonical_summary)
        ? '<div class="drawer-content">' + app.common.esc(memory.canonical_summary) + "</div>"
        : ""
    ),
    facts.length
      ? section(
          "关键事实",
          '<div class="row-list">' +
            facts.map((fact) => '<div class="row" style="cursor:default"><div class="row-main"><div class="row-title" style="white-space:normal">' + app.common.esc(fact) + "</div></div></div>").join("") +
            "</div>"
        )
      : "",
    section("证据", app.common.compact(memory.evidence_preview) ? '<div class="drawer-content">' + app.common.esc(memory.evidence_preview) + "</div>" : ""),
    section(
      "分类与来源",
      kv([
        ["记忆 ID", app.common.compact(memory.id)],
        ["类型", (app.values.TYPE_LABEL[type] || type || "-") + (type ? "（" + type + "）" : "")],
        ["范围", (app.values.SCOPE_META[scope] ? app.values.SCOPE_META[scope].label : scope || "-")],
        ["会话", app.common.compact(memory.session_id)],
        ["群 ID", app.common.compact(memory.group_id)],
        ["来源插件", app.common.compact(memory.source_plugin) || "本插件"],
        ["导入批次", app.common.compact(memory.import_batch_id)],
        ["主体", app.common.compact(memory.subject && memory.subject.name) + (app.common.compact(memory.subject && memory.subject.role) ? "（" + app.common.compact(memory.subject.role) + "）" : "")],
        ["客体", app.common.compact(memory.object && memory.object.name)],
        ["人格 ID", app.common.compact(memory.persona_id)],
        ["归属 Bot", app.common.compact(memory.owner_bot_id)],
      ])
    ),
    section(
      "权重",
      kv([
        ["重要性", app.common.num(memory.importance)],
        ["置信度", app.common.num(memory.confidence)],
        ["显著性", app.common.num(memory.salience)],
        ["强化分", app.common.num(memory.reinforcement_score)],
        ["注入次数", String(app.common.int(memory.injection_count))],
        ["最近注入", app.common.fmtTime(memory.last_injected_at)],
      ]) +
        (weightKeys.length
          ? '<div class="tag-row" style="margin-top:9px">' +
            weightKeys
              .slice(0, 12)
              .map((key) => app.widgets.badge((app.values.WEIGHT_LABEL[key] || key) + " " + app.common.num(weights[key]), "accent"))
              .join("") +
            "</div>"
          : "")
    ),
    section(
      "生命周期",
      kv([
        ["可见性", (app.values.VISIBILITY_LABEL[app.common.compact(memory.visibility)] || app.common.compact(memory.visibility) || "-")],
        ["可说性", app.common.compact(memory.sayability)],
        ["现实层级", (app.values.REALITY_LABEL[app.common.compact(memory.reality_level)] || app.common.compact(memory.reality_level) || "-")],
        ["生命周期", (app.values.LIFECYCLE_LABEL[app.common.compact(memory.lifecycle)] || app.common.compact(memory.lifecycle) || "-")],
        ["时效状态", (app.values.VALIDITY_LABEL[app.common.compact(memory.validity_status)] || app.common.compact(memory.validity_status) || "-")],
        ["有效区间", (app.common.compact(memory.valid_from) || "…") + " → " + (app.common.compact(memory.valid_to) || "…")],
        ["持久度", (app.values.DURABILITY_LABEL[app.common.compact(memory.durability)] || app.common.compact(memory.durability) || "-")],
        ["敏感度", (app.values.SENSITIVITY_LABEL[app.common.compact(memory.sensitivity)] || app.common.compact(memory.sensitivity) || "-")],
        ["记忆理由", app.common.compact(memory.memory_reason)],
        ["提及策略", app.common.compact(memory.mention_policy)],
        ["可提及分", memory.mentionability_score === null || memory.mentionability_score === undefined ? "" : app.common.num(memory.mentionability_score)],
        ["关系阶段", app.common.compact(memory.relationship_phase)],
        ["衰减模式", app.common.compact(memory.decay_mode)],
        ["时间范围", app.common.compact(range.start_at_local) ? app.common.compact(range.start_at_local) + " → " + app.common.compact(range.end_at_local) : ""],
      ])
    ),
    section(
      "时间",
      kv([
        ["创建", app.common.fmtTime(memory.created_at) + "（" + app.common.compact(memory.created_at_local) + "）"],
        ["更新", app.common.fmtTime(memory.updated_at) + "（" + app.common.compact(memory.updated_at_local) + "）"],
        ["发生", app.common.fmtTime(memory.occurred_at) + "（" + app.common.compact(memory.occurred_at_local) + "）"],
      ])
    ),
    section(
      "标签与主题",
      (tags.length ? '<div class="tag-row">' + tags.map((tag) => app.widgets.badge(tag, "fact")).join("") + "</div>" : "") +
        (topics.length ? '<div class="tag-row" style="margin-top:6px">' + topics.map((tag) => app.widgets.badge(tag, "gold")).join("") + "</div>" : "")
    ),
    section(
      "去重与合并",
      kv([
        ["规范键", app.common.compact(memory.canonical_key)],
        ["合并条数", String(app.common.int(memory.merged_count))],
        ["内容指纹", app.common.compact(memory.content_fingerprint)],
        ["复核状态", app.common.compact(memory.review_status)],
      ])
    ),
  ].join("");

  const actions =
    '<div class="drawer-section"><h4>操作</h4>' +
    '<div class="card is-tight" style="background:var(--surface-2)">' +
    '<div class="config-form">' +
    '<button class="btn is-sm assembly-open-btn" type="button" id="openAssemblyViewerBtn">打开三维结构</button>' +
    '<div class="field"><span>内容</span><textarea id="editContent" rows="5">' + app.common.esc(app.common.compact(memory.content)) + "</textarea></div>" +
    '<div class="field is-inline"><span>重要性</span><input id="editImportance" type="number" step="0.01" min="0" max="1" value="' + app.common.esc(app.common.num(memory.importance, 2) === "-" ? "0.5" : app.common.num(memory.importance, 2)) + '" /></div>' +
    '<div class="field is-inline"><span>置信度</span><input id="editConfidence" type="number" step="0.01" min="0" max="1" value="' + app.common.esc(app.common.num(memory.confidence, 2) === "-" ? "0.5" : app.common.num(memory.confidence, 2)) + '" /></div>' +
    '<div class="field is-inline"><span>显著性</span><input id="editSalience" type="number" step="0.01" min="0" max="1" value="' + app.common.esc(app.common.num(memory.salience, 2) === "-" ? "0.5" : app.common.num(memory.salience, 2)) + '" /></div>' +
    '<div class="field is-inline"><span>可见性</span><select id="editVisibility">' +
      Object.keys(app.values.VISIBILITY_LABEL).map((key) => '<option value="' + key + '"' + (app.common.compact(memory.visibility) === key ? " selected" : "") + ">" + app.values.VISIBILITY_LABEL[key] + "</option>").join("") +
    "</select></div>" +
    '<div class="field is-inline"><span>生命周期</span><select id="editLifecycle">' +
      Object.keys(app.values.LIFECYCLE_LABEL).map((key) => '<option value="' + key + '"' + (app.common.compact(memory.lifecycle) === key ? " selected" : "") + ">" + app.values.LIFECYCLE_LABEL[key] + "</option>").join("") +
    "</select></div>" +
    '<div class="pill-row" style="justify-content:flex-end;margin-top:4px">' +
      '<button class="btn is-sm is-danger" type="button" id="deleteMemoryBtn">删除</button>' +
      '<button class="btn is-sm is-primary" type="button" id="saveMemoryBtn">保存修改</button>' +
    "</div></div></div></div>";

  return rows + actions;
}

function bindMemoryActions(memoryId) {
  const viewerBtn = app.values.$("#openAssemblyViewerBtn");
  if (viewerBtn) viewerBtn.addEventListener("click", () => app.viewer.openAssemblyViewer(memoryId));
  const saveBtn = app.values.$("#saveMemoryBtn");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const payload = {
        id: memoryId,
        content: app.values.$("#editContent").value,
        importance: Number(app.values.$("#editImportance").value),
        confidence: Number(app.values.$("#editConfidence").value),
        salience: Number(app.values.$("#editSalience").value),
        visibility: app.values.$("#editVisibility").value,
        lifecycle: app.values.$("#editLifecycle").value,
      };
      await app.shell.withBusy("正在保存…", async () => {
        await app.api.apiPost("/memory/update", payload);
        app.pool.invalidatePool();
        app.shell.toast("记忆已更新", "ok");
        app.shell.closeDrawer();
        app.navigation.refresh();
      });
    });
  }
  const delBtn = app.values.$("#deleteMemoryBtn");
  if (delBtn) {
    delBtn.addEventListener("click", async () => {
      const confirmed = await app.shell.showInlineConfirmation("删除记忆", "确定删除这条记忆？该操作不可撤销。", "删除");
      if (!confirmed) return;
      await app.shell.withBusy("正在删除…", async () => {
        await app.api.apiPost("/memory/delete", { id: memoryId });
        app.pool.invalidatePool();
        app.shell.toast("记忆已删除", "ok");
        app.shell.closeDrawer();
        app.navigation.refresh();
      });
    });
  }
}

async function initialize() {
app.values.SCOPE_META = {
  private: { label: "私聊", tone: "private", icon: "chat", desc: "一对一会话中沉淀的记忆" },
  group: { label: "群聊", tone: "group", icon: "users", desc: "群窗口内的公共与成员记忆" },
  profile: { label: "用户档案", tone: "relation", icon: "user", desc: "画像 / 偏好 / 关系的统一档案" },
  personal: { label: "Bot 个人记忆", tone: "personal", icon: "sun", desc: "Bot 自身日程、相册与主观记忆" },
  core: { label: "核心记忆", tone: "gold", icon: "star", desc: "强制注入的稳定事实与规则" },
  external: { label: "外部接口", tone: "external", icon: "plug", desc: "由其他插件经 bridge 写入" },
};
app.values.TYPE_LABEL = {
  profile: "画像",
  user_profile: "画像",
  user_habit: "习惯",
  preference: "偏好",
  user_preference: "偏好",
  relationship: "关系",
  relationship_claim: "关系",
  relationship_phase_summary: "关系阶段",
  fact: "事实",
  stable_fact: "稳定事实",
  user_fact: "用户事实",
  manual_memory: "手动记忆",
  explicit_memory: "明确记忆",
  event: "事件",
  conversation_event: "会话事件",
  important_event: "重要事件",
  timeline_event: "时间线事件",
  state: "稳定状态",
  current_state: "当前状态",
  stable_state: "稳定状态",
  plan: "计划",
  promise: "承诺",
  emotion: "情绪",
  schedule: "日程",
  schedule_fragment: "日程片段",
  daily_digest: "每日摘要",
  action: "行为",
  image_action: "图像行为",
  thought: "想法",
  companion_note: "陪伴想法",
  internal_note: "内部想法",
  summary: "摘要",
  note: "备注",
  rule: "规则",
  boundary: "边界",
};
app.values.VISIBILITY_LABEL = {
  public: "公开",
  private: "私有",
  group_shared: "群内共享",
  bot_self: "仅 Bot 自身",
  restricted: "受限",
};
app.values.LIFECYCLE_LABEL = {
  active: "活跃",
  stable: "稳定",
  fading: "淡出",
  archived: "归档",
  expired: "过期",
};
app.values.VALIDITY_LABEL = {
  current: "当前有效",
  historical: "历史有效",
  unknown: "待确认",
  superseded: "已被取代",
};
app.values.DURABILITY_LABEL = { permanent: "永久", durable: "长期", transient: "短期" };
app.values.SENSITIVITY_LABEL = { low: "低", normal: "普通", high: "高", critical: "敏感" };
app.values.REALITY_LABEL = { real: "真实", fictional: "虚构", uncertain: "不确定" };
app.values.WEIGHT_LABEL = {
  persona_importance: "人格重要性",
  relationship_weight: "关系",
  emotional_weight: "情绪",
  promise_weight: "承诺",
  open_loop_weight: "未完成",
  creative_weight: "创作",
  preference_weight: "偏好",
  self_continuity_weight: "自我连续",
  freshness_weight: "新鲜度",
  scar_weight: "伤痕",
  emotional_debt_weight: "情绪债",
  intimacy_weight: "亲密度",
  vulnerability_weight: "脆弱",
};
}
return { memoryTone, memoryTitle, memoryRow, memoryDetailHtml, bindMemoryActions, initialize };
}
