
// pool: one page runtime owns shared state and DOM references.
export function createPool(app) {
async function loadPool() {
  if (app.values.state.pool) return app.values.state.pool;
  const result = await app.api.apiGet("/memories?limit=800");
  app.values.state.pool = Array.isArray(result.memories) ? result.memories : [];
  return app.values.state.pool;
}

function invalidatePool() {
  app.values.state.pool = null;
}

async function initialize() {
app.navigation.defineView("navigate", {
  title: "记忆导航",
  navLabel: "记忆导航",
  eyebrow: "Library · Navigate",
  hint: "按记忆归属进入：私聊、群聊、用户档案、Bot 个人、核心记忆与外部接口",
  async load() {
    await app.shell.withBusy("正在读取记忆分布…", async () => {});
    const [statsPayload, core, pool, buckets] = await Promise.all([
      app.api.apiGet("/stats"),
      app.api.apiTry(() => app.api.apiGet("/core-memory"), { blocks: [] }),
      app.api.apiTry(loadPool, []),
      app.api.apiTry(() => app.api.apiGet("/buckets?limit=160"), { buckets: [] }),
    ]);
    const statsData = (statsPayload && statsPayload.stats) || {};
    const byScope = statsData.by_scope || {};
    const personalCount = pool.filter((m) => app.common.compact(m.visibility) === "bot_self").length;
    const externalCount = pool.filter((m) => {
      const source = app.common.compact(m.source_plugin);
      return source && source !== "self" && source !== "astrbot_plugin_memory_companion";
    }).length;
    const externalCapped = externalCount >= pool.length && pool.length >= 800;
    const personalCapped = personalCount >= pool.length && pool.length >= 800;
    return {
      counts: {
        private: app.common.int(byScope.private),
        group: app.common.int(byScope.group),
        profile: app.common.int(statsData.identities),
        personal: app.common.int(personalCount),
        core: app.common.int((core.blocks || []).length),
        external: app.common.int(externalCount),
      },
      capped: { personal: personalCapped, external: externalCapped },
      poolSize: pool.length,
      buckets: Array.isArray(buckets.buckets) ? buckets.buckets : [],
      pool,
    };
  },
  render(data) {
    const counts = data.counts || {};
    const max = Math.max(1, ...Object.values(counts).map((v) => app.common.int(v)));

    const scopes = [
      { key: "private", meta: app.values.SCOPE_META.private, jump: "inspect", filter: "scope:private", exact: true },
      { key: "group", meta: app.values.SCOPE_META.group, jump: "inspect", filter: "scope:group", exact: true },
      { key: "profile", meta: app.values.SCOPE_META.profile, jump: "inspect", filter: "profile", exact: true },
      { key: "personal", meta: app.values.SCOPE_META.personal, jump: "botlife", filter: "", exact: false },
      { key: "core", meta: app.values.SCOPE_META.core, jump: "core", filter: "", exact: true },
      { key: "external", meta: app.values.SCOPE_META.external, jump: "external", filter: "", exact: false },
    ];

    const cards = scopes
      .map((item) => {
        const count = app.common.int(counts[item.key]);
        const capped = Boolean(data.capped && data.capped[item.key]);
        const note = item.exact ? "精确统计" : capped ? "≥ " + app.common.fmtInt(count) + "（抽样下限）" : "基于 " + app.common.fmtInt(data.poolSize) + " 条抽样";
        return (
          '<button class="scope-card" type="button" data-tone="' + item.meta.tone + '" data-goto="' + item.jump + '" data-filter="' + app.common.esc(item.filter) + '">' +
          '<div class="scope-top">' + app.common.icon(item.meta.icon, "scope-icon") +
          '<span class="scope-count">' + app.common.fmtInt(count) + " 条</span></div>" +
          '<div class="scope-body"><div class="scope-name">' + app.common.esc(item.meta.label) + "</div>" +
          '<div class="scope-desc">' + app.common.esc(item.meta.desc) + "</div></div>" +
          '<div class="scope-bar"><i style="width:' + ((count / max) * 100).toFixed(1) + '%"></i></div>' +
          '<div class="scope-foot"><span>' + app.common.esc(note) + "</span>" +
          "<span>进入 ›</span></div></button>"
        );
      })
      .join("");

    const recent = (data.buckets || [])
      .slice()
      .sort((a, b) => app.common.int(b.memory_count) - app.common.int(a.memory_count))
      .slice(0, 10)
      .map((bucket) => {
        const scope = app.common.compact(bucket.scope);
        const meta = app.values.SCOPE_META[scope] || { label: scope || "未分类", tone: "" };
        return (
          '<button class="row" type="button" data-goto="inspect" data-scope="' + app.common.esc(scope) + '" data-target="' + app.common.esc(app.common.compact(bucket.target_id)) + '">' +
          '<div class="row-main"><div class="row-title">' + app.common.esc(app.common.compact(bucket.label) || app.common.compact(bucket.target_name) || app.common.compact(bucket.target_id) || "未命名窗口") + "</div>" +
          '<div class="row-sub">' + app.common.esc(app.common.compact(bucket.target_kind) || "window") + " · " + app.common.esc(app.common.compact(bucket.sample_session_id) || "-") + "</div></div>" +
          '<div class="row-meta">' + app.widgets.badge(meta.label, meta.tone) + app.widgets.badge(app.common.fmtInt(bucket.memory_count) + " 条") + "</div></button>"
        );
      })
      .join("");

    const typeBreakdown = {};
    (data.pool || []).forEach((memory) => {
      const key = app.common.compact(memory.memory_type) || "unknown";
      typeBreakdown[key] = (typeBreakdown[key] || 0) + 1;
    });
    const typeRows = Object.entries(typeBreakdown)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map((entry) => app.widgets.meter(app.values.TYPE_LABEL[entry[0]] || entry[0], app.common.fmtInt(entry[1]), Math.max(1, data.pool.length)))
      .join("");

    const visBreakdown = {};
    (data.pool || []).forEach((memory) => {
      const key = app.common.compact(memory.visibility) || "unknown";
      visBreakdown[key] = (visBreakdown[key] || 0) + 1;
    });
    const visRows = Object.entries(visBreakdown)
      .sort((a, b) => b[1] - a[1])
      .map((entry) => app.widgets.meter(app.values.VISIBILITY_LABEL[entry[0]] || entry[0], app.common.fmtInt(entry[1]), Math.max(1, data.pool.length)))
      .join("");

    return (
      '<div class="grid" style="gap:16px">' +
      '<div class="section-label"><h2>记忆归属</h2><span class="section-note">点击任一范围进入对应检视视图</span></div>' +
      '<div class="grid g3">' + cards + "</div>" +
      '<div class="grid split-2">' +
        app.widgets.card("最近活跃范围", (data.buckets || []).length + " 个窗口 · 按记忆量排序", '<div class="row-list">' + (recent || app.widgets.emptyState("暂无范围", "还没有可展示的记忆窗口。")) + "</div>") +
        '<div style="display:grid;gap:16px">' +
          app.widgets.card("记忆类型构成", "基于最近 " + app.common.fmtInt(data.poolSize) + " 条抽样", '<div style="display:grid;gap:9px">' + (typeRows || "—") + "</div>") +
          app.widgets.card("可见性构成", "决定谁能读到这条记忆", '<div style="display:grid;gap:9px">' + (visRows || "—") + "</div>") +
        "</div>" +
      "</div>" +
      "</div>"
    );
  },
});
}
return { loadPool, invalidatePool, initialize };
}
