
// knowledge: one page runtime owns shared state and DOM references.
export function createKnowledge(app) {
async function initialize() {
app.navigation.defineView("external", {
  title: "外部接口记忆",
  navLabel: "外部接口",
  eyebrow: "Bridge · External",
  hint: "由其他插件经 bridge 写入的记忆，按来源插件归类并可单独检索",
  async load() {
    const pool = await app.pool.loadPool();
    const external = pool.filter((memory) => {
      const source = app.common.compact(memory.source_plugin);
      return source && source !== "self" && source !== "astrbot_plugin_memory_companion";
    });
    const bySource = {};
    external.forEach((memory) => {
      const source = app.common.compact(memory.source_plugin);
      bySource[source] = bySource[source] || { count: 0, types: {}, latest: "" };
      bySource[source].count += 1;
      const type = app.common.compact(memory.memory_type) || "unknown";
      bySource[source].types[type] = (bySource[source].types[type] || 0) + 1;
      const ts = app.common.compact(memory.created_at);
      if (ts > bySource[source].latest) bySource[source].latest = ts;
    });
    return { external, bySource, poolSize: pool.length, capped: external.length >= pool.length && pool.length >= 800 };
  },
  render(data) {
    const external = data.external || [];
    const bySource = data.bySource || {};
    const sources = Object.keys(bySource).sort((a, b) => bySource[b].count - bySource[a].count);

    const sourceCards = sources.length
      ? sources
          .map((source) => {
            const info = bySource[source];
            const types = Object.entries(info.types)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 3)
              .map((entry) => (app.values.TYPE_LABEL[entry[0]] || entry[0]) + " " + entry[1])
              .join(" · ");
            return (
              '<button class="scope-card" type="button" data-tone="external" data-goto="inspect" data-filter="external:' + app.common.esc(source) + '">' +
              '<div class="scope-top">' + app.common.icon("plug", "scope-icon") + '<span class="scope-count">' + app.common.fmtInt(info.count) + " 条</span></div>" +
              '<div class="scope-body"><div class="scope-name">' + app.common.esc(source) + "</div>" +
              '<div class="scope-desc">' + app.common.esc(types || "无类型信息") + "</div></div>" +
              '<div class="scope-bar"><i style="width:' + ((info.count / Math.max(1, external.length)) * 100).toFixed(1) + '%"></i></div>' +
              '<div class="scope-foot"><span>最近 ' + app.common.esc(app.common.fmtDate(info.latest)) + "</span><span>查看 ›</span></div></button>"
            );
          })
          .join("")
      : app.widgets.emptyState("没有外部写入的记忆", "其他插件通过 bridge 写入记忆后，会按来源插件归类显示在这里。");

    const rows = external.length
      ? external
          .slice(0, 60)
          .map((memory) => app.records.memoryRow(memory))
          .join("")
      : "";

    return (
      '<div class="grid" style="gap:16px">' +
      (data.capped
        ? app.widgets.card("抽样提示", "", '<p style="font-size:12px;color:var(--text-2)">当前统计基于最近 ' + app.common.fmtInt(data.poolSize) + " 条记忆抽样，实际数量可能更多。</p>")
        : "") +
      '<div class="section-label"><h2>来源插件</h2><span class="section-note">' + sources.length + " 个来源 · 共 " + app.common.fmtInt(external.length) + " 条</span></div>" +
      '<div class="grid g3">' + sourceCards + "</div>" +
      (rows ? app.widgets.card("最近写入", "最多显示 60 条", '<div class="row-list">' + rows + "</div>") : "") +
      "</div>"
    );
  },
  mount(node) {
    app.values.$$("[data-memory]", node).forEach((row) => {
      row.addEventListener("click", () => app.viewer.openMemory(row.dataset.memory));
    });
  },
});
app.values.importState = { tab: "qq", caps: null, preview: null, status: null, batchId: "" };
}
return { initialize };
}
