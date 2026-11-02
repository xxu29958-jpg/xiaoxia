
// search: one page runtime owns shared state and DOM references.
export function createSearch(app) {
async function initialize() {
app.navigation.defineView("inspect", {
  title: "记忆检视",
  navLabel: "记忆检视",
  eyebrow: "Library · Inspect",
  hint: "按范围、类型、可见性和生命周期筛选，逐条核对 46 项记忆参数",
  async load(options) {
    const opts = options || {};
    if (opts.scope) app.values.state.filters.scope = opts.scope;
    if (opts.target) app.values.state.filters.target = opts.target;
    const filters = app.values.state.filters;
    const params = ["limit=120"];
    if (filters.scope) params.push("scope=" + encodeURIComponent(filters.scope));
    if (filters.visibility) params.push("visibility=" + encodeURIComponent(filters.visibility));
    if (filters.lifecycle) params.push("lifecycle=" + encodeURIComponent(filters.lifecycle));
    if (filters.memoryType) params.push("memory_type=" + encodeURIComponent(filters.memoryType));
    if (filters.q) params.push("q=" + encodeURIComponent(filters.q));
    if (filters.target) {
      if (filters.scope === "group") params.push("group_id=" + encodeURIComponent(filters.target));
      else params.push("entity_id=" + encodeURIComponent(filters.target));
    }
    const result = await app.api.apiGet("/memories?" + params.join("&"));
    let memories = Array.isArray(result.memories) ? result.memories : [];
    if (filters.scope === "profile") {
      memories = memories.filter((m) => [
        "profile", "user_profile", "user_habit",
        "preference", "user_preference",
        "relationship", "relationship_claim", "relationship_phase_summary",
      ].includes(app.common.compact(m.memory_type)));
    }
    return { memories, filters };
  },
  render(data) {
    const filters = data.filters || {};
    const memories = data.memories || [];

    const scopePills = ["", "private", "group", "profile", "external"]
      .map((key) => {
        const label = key === "" ? "全部" : key === "external" ? "外部接口" : (app.values.SCOPE_META[key] ? app.values.SCOPE_META[key].label : key);
        return '<button class="pill' + ((filters.scope || "") === key ? " is-active" : "") + '" type="button" data-filter-key="scope" data-filter-value="' + app.common.esc(key) + '">' + app.common.esc(label) + "</button>";
      })
      .join("");

    const typePills = ["", "profile", "preference", "relationship", "fact", "event", "state", "promise", "schedule", "thought"]
      .map((key) => {
        const label = key === "" ? "全部类型" : app.values.TYPE_LABEL[key] || key;
        return '<button class="pill' + ((filters.memoryType || "") === key ? " is-active" : "") + '" type="button" data-filter-key="memoryType" data-filter-value="' + app.common.esc(key) + '">' + app.common.esc(label) + "</button>";
      })
      .join("");

    const visPills = ["", "public", "private", "group_shared", "bot_self", "restricted"]
      .map((key) => {
        const label = key === "" ? "全部可见性" : app.values.VISIBILITY_LABEL[key] || key;
        return '<button class="pill' + ((filters.visibility || "") === key ? " is-active" : "") + '" type="button" data-filter-key="visibility" data-filter-value="' + app.common.esc(key) + '">' + app.common.esc(label) + "</button>";
      })
      .join("");

    const lifePills = ["", "active", "stable", "fading", "archived", "expired"]
      .map((key) => {
        const label = key === "" ? "全部生命周期" : app.values.LIFECYCLE_LABEL[key] || key;
        return '<button class="pill' + ((filters.lifecycle || "") === key ? " is-active" : "") + '" type="button" data-filter-key="lifecycle" data-filter-value="' + app.common.esc(key) + '">' + app.common.esc(label) + "</button>";
      })
      .join("");

    const activeChips = [];
    if (filters.q) activeChips.push("关键词：" + filters.q);
    if (filters.target) activeChips.push("目标：" + filters.target);

    const rows = memories.length
      ? memories.map((memory, index) => app.records.memoryRow(memory, index)).join("")
      : app.widgets.emptyState("没有匹配的记忆", "试着放宽筛选条件，或清空关键词后重新加载。");

    return (
      '<div class="grid" style="gap:14px">' +
      '<section class="card is-tight">' +
        '<div style="display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center">' +
          '<div class="pill-row">' + scopePills + "</div>" +
        "</div>" +
        '<div style="display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin-top:10px">' +
          '<div class="pill-row">' + typePills + "</div>" +
        "</div>" +
        '<div style="display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin-top:10px">' +
          '<div class="pill-row">' + visPills + "</div>" +
          '<div class="pill-row">' + lifePills + "</div>" +
        "</div>" +
        '<div style="display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap">' +
          '<input id="inspectQuery" type="search" placeholder="按内容或证据过滤" value="' + app.common.esc(filters.q || "") + '" style="flex:1;min-width:200px;height:32px;padding:0 12px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--text);font-size:12.5px;outline:none" />' +
          '<button class="btn is-sm" type="button" id="applyQueryBtn">应用</button>' +
          '<button class="btn is-sm is-ghost" type="button" id="clearFiltersBtn">清空筛选</button>' +
          '<span style="margin-left:auto;font-size:11.5px;color:var(--text-3)">' + app.common.fmtInt(memories.length) + " 条" + (activeChips.length ? " · " + app.common.esc(activeChips.join(" · ")) : "") + "</span>" +
        "</div>" +
      "</section>" +
      '<div class="row-list" id="inspectList">' + rows + "</div>" +
      "</div>"
    );
  },
  mount(node) {
    app.values.$$("[data-filter-key]", node).forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.filterKey;
        const value = button.dataset.filterValue;
        app.values.state.filters[key] = value;
        if (key === "scope") app.values.state.filters.target = "";
        app.navigation.go("inspect");
      });
    });
    const applyBtn = app.values.$("#applyQueryBtn", node);
    if (applyBtn) {
      applyBtn.addEventListener("click", () => {
        app.values.state.filters.q = app.values.$("#inspectQuery", node).value.trim();
        app.navigation.go("inspect");
      });
      app.values.$("#inspectQuery", node).addEventListener("keydown", (event) => {
        if (event.key === "Enter") applyBtn.click();
      });
    }
    const clearBtn = app.values.$("#clearFiltersBtn", node);
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        app.values.state.filters = { scope: "", q: "", visibility: "", lifecycle: "", memoryType: "", target: "" };
        app.navigation.go("inspect");
      });
    }
    app.values.$$("[data-memory]", node).forEach((row) => {
      row.addEventListener("click", () => app.viewer.openMemory(row.dataset.memory));
    });
  },
});
}
return { initialize };
}
