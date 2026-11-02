
// microscope: one page runtime owns shared state and DOM references.
export function createMicroscope(app) {
function jsonPreview(value, maxDepth) {
  try {
    const text = JSON.stringify(value, null, 2);
    return text.length > 4200 ? text.slice(0, 4200) + "\n…（已截断）" : text;
  } catch (error) {
    return String(value);
  }
}

async function initialize() {
app.values.microResult = null;
app.navigation.defineView("microscope", {
  title: "记忆显微镜",
  navLabel: "记忆显微镜",
  eyebrow: "Insight · Microscope",
  hint: "模拟一次真实召回：看哪些记忆被选中、拿到多少分，以及哪些被过滤掉",
  async load() {
    return { result: app.values.microResult, form: app.values.microState };
  },
  render(data) {
    const form = data.form || app.values.microState;
    const result = data.result;

    const queryPanel =
      '<div class="config-form">' +
      '<label class="field"><span>模拟提问</span><textarea id="microQuery" rows="4" placeholder="例如：你最近写了什么 / 那个蓝色渐变小猪是什么">' +
      app.common.esc(form.query) + "</textarea></label>" +
      '<label class="field"><span>检索范围</span><select id="microScope">' +
        [["all", "全部可检索记忆（管理检索）"], ["private", "指定私聊窗口"], ["group", "指定群聊窗口"]]
          .map((row) => '<option value="' + row[0] + '"' + (form.scope === row[0] ? " selected" : "") + ">" + row[1] + "</option>")
          .join("") +
      "</select></label>" +
      '<label class="field is-inline"><span>用户 ID</span><input id="microUser" type="text" value="' + app.common.esc(form.userId) + '" placeholder="私聊时填写" /></label>' +
      '<label class="field is-inline"><span>群 ID</span><input id="microGroup" type="text" value="' + app.common.esc(form.groupId) + '" placeholder="群聊时填写" /></label>' +
      '<label class="field is-inline"><span>召回条数</span><input id="microTopK" type="number" min="1" max="50" value="' + app.common.int(form.topK || 8) + '" /></label>' +
      '<div class="pill-row"><button class="btn is-primary" type="button" id="microRunBtn">开始检索</button>' +
      '<button class="btn is-ghost" type="button" id="microClearBtn">清空结果</button></div>' +
      "</div>";

    let hits = app.widgets.emptyState("还没有检索结果", "输入一句模拟提问，看看记忆库会召回什么。");
    let blocked = app.widgets.emptyState("暂无过滤记录", "召回过程中被 ACL、作用域或策略拦截的记忆会列在这里。");
    let retrieval = "";

    if (result) {
      const rows = Array.isArray(result.results) ? result.results : [];
      hits = rows.length
        ? rows
            .map((memory, index) => {
              const tone = app.records.memoryTone(memory);
              return (
                '<button class="hit" type="button" data-memory="' + app.common.esc(memory.id) + '" data-rank="' + (index + 1) + '">' +
                '<span class="hit-rank">' + (index + 1) + "</span>" +
                '<span class="hit-main"><span class="hit-content">' + app.common.esc(app.common.clip(app.records.memoryTitle(memory), 110)) + "</span>" +
                '<span class="hit-meta">' +
                  app.widgets.badge(app.galaxy.typeLabel(memory), tone || "fact") +
                  app.widgets.badge(app.values.VISIBILITY_LABEL[app.common.compact(memory.visibility)] || app.common.compact(memory.visibility) || "-") +
                  (app.common.compact(memory.reason) ? app.widgets.badge(app.common.clip(memory.reason, 24)) : "") +
                  '<span class="hit-score">' + app.common.num(memory.score) + "</span>" +
                "</span></span></button>"
              );
            })
            .join("")
        : app.widgets.emptyState("没有召回任何记忆", "这句话没有命中记忆库，可以换个说法或放宽范围再试。");

      const blockedRows = Array.isArray(result.blocked) ? result.blocked : [];
      blocked = blockedRows.length
        ? blockedRows
            .slice(0, 12)
            .map((item) => {
              const text =
                typeof item === "string"
                  ? item
                  : app.common.compact(item.reason) || app.common.compact(item.memory_type) || jsonPreview(item);
              const title =
                typeof item === "object" && item !== null
                  ? app.common.clip(app.common.compact(item.content) || app.common.compact(item.title) || app.common.compact(item.id), 60)
                  : "";
              return '<div class="block-row"><span>' + app.common.esc(text) + (title ? "<br /><b>" + app.common.esc(title) + "</b>" : "") + "</span></div>";
            })
            .join("")
        : app.widgets.emptyState("没有过滤记录", "本次召回没有记忆被拦截。");

      const info = result.retrieval || {};
      const slotCounts = info.slot_counts || {};
      retrieval =
        '<dl class="kv" style="margin-bottom:10px">' +
        "<dt>检索模式</dt><dd>" + app.common.esc(app.common.compact((result.search_context || {}).mode) || "-") + "</dd>" +
        "<dt>编排</dt><dd>" + (info.orchestration_enabled ? "已启用" : "未启用") + "</dd>" +
        "<dt>Top K</dt><dd>" + app.common.int(info.top_k) + "</dd>" +
        "<dt>命中</dt><dd>" + ((result.results || []).length) + " 条</dd>" +
        "</dl>" +
        (Object.keys(slotCounts).length
          ? '<div class="tag-row">' + Object.keys(slotCounts).map((slot) => app.widgets.badge(slot + " " + app.common.int(slotCounts[slot]), "accent")).join("") + "</div>"
          : "") +
        ((info.capped_slots || []).length
          ? '<div class="tag-row" style="margin-top:6px">' + info.capped_slots.map((slot) => app.widgets.badge("受限 " + slot, "warn")).join("") + "</div>"
          : "");
    }

    return (
      '<div class="micro-grid">' +
      app.widgets.card("召回测试", "模拟一次真实提问", queryPanel + (retrieval ? '<div style="margin-top:14px">' + retrieval + "</div>" : "")) +
      app.widgets.card("命中记忆", result ? (result.results || []).length + " 条" : "待运行", '<div class="row-list">' + hits + "</div>") +
      app.widgets.card("过滤原因", "被拦截的记忆", '<div style="display:grid;gap:6px">' + blocked + "</div>") +
      "</div>"
    );
  },
  mount(node) {
    const run = async () => {
      app.values.microState.query = app.values.$("#microQuery", node).value.trim();
      app.values.microState.scope = app.values.$("#microScope", node).value;
      app.values.microState.userId = app.values.$("#microUser", node).value.trim();
      app.values.microState.groupId = app.values.$("#microGroup", node).value.trim();
      app.values.microState.topK = app.common.int(app.values.$("#microTopK", node).value) || 8;
      if (!app.values.microState.query) {
        app.shell.toast("请先输入一句模拟提问", "error");
        return;
      }
      await app.shell.withBusy("正在召回…", async () => {
        app.values.microResult = await app.api.apiPost("/search", {
          query: app.values.microState.query,
          scope: app.values.microState.scope === "all" ? "unknown" : app.values.microState.scope,
          user_id: app.values.microState.userId,
          group_id: app.values.microState.groupId,
          top_k: app.values.microState.topK,
          context_mode: app.values.microState.scope === "all" ? "all" : "session",
        });
        app.shell.toast("召回完成", "ok");
        app.navigation.go("microscope");
      });
    };
    app.values.$("#microRunBtn", node).addEventListener("click", run);
    app.values.$("#microClearBtn", node).addEventListener("click", () => {
      app.values.microResult = null;
      app.navigation.go("microscope");
    });
    app.values.$$("[data-memory]", node).forEach((row) => {
      row.addEventListener("click", () => app.viewer.openMemory(row.dataset.memory));
    });
  },
});
}
return { jsonPreview, initialize };
}
