
// settings: one page runtime owns shared state and DOM references.
export function createSettings(app) {
async function initialize() {
app.values.MODULE_LABELS = {
  appearance: "外观",
  memory_capture: "记忆捕获",
  scope_control: "范围控制",
  portrait: "统一画像",
  memory_summary: "记忆摘要",
  historical_chat_import: "历史聊天导入",
  retrieval: "检索",
  retrieval_advanced: "检索进阶",
  memory_injection: "记忆注入",
  conversation_memory: "对话记忆",
  conversation_memory_advanced: "对话记忆进阶",
  core_memory: "核心记忆",
  context_orchestration: "上下文编排",
  context_orchestration_advanced: "上下文编排进阶",
  private_companion_bridge: "陪伴桥接",
  visibility: "可见性",
  memory_tools: "记忆工具",
  memory_reconstruction: "记忆重建",
  knowledge_graph: "知识图谱",
  livingmemory_migration: "LivingMemory 迁移",
  maintenance: "维护",
  maintenance_audit: "记忆审计",
  maintenance_decay: "记忆衰减",
};
app.navigation.defineView("config", {
  title: "模块配置",
  navLabel: "模块配置",
  eyebrow: "Settings · Config",
  hint: "按模块调整检索、注入、编排与维护策略，保存后立即写入插件配置",
  async load() {
    const result = await app.api.apiGet("/config/schema");
    app.values.state.configSchema = result;
    return result;
  },
  render(data) {
    const schema = data.schema || {};
    const values = data.values || {};
    const modules = Object.keys(schema);
    if (!modules.includes(app.values.state.configModule)) app.values.state.configModule = modules[0] || "appearance";
    const moduleId = app.values.state.configModule;
    const moduleSchema = schema[moduleId] || { items: {} };
    const moduleValues = values[moduleId] || {};
    const items = moduleSchema.items || {};

    const nav = modules
      .map(
        (key) =>
          '<button type="button" class="' + (key === moduleId ? "is-active" : "") + '" data-module="' + app.common.esc(key) + '">' +
          "<span>" + app.common.esc(app.values.MODULE_LABELS[key] || key) + "</span>" +
          "<em>" + Object.keys((schema[key] || {}).items || {}).length + "</em></button>"
      )
      .join("");

    const fields = Object.keys(items)
      .map((key) => {
        const item = items[key] || {};
        const value = moduleValues[key] === undefined ? item.default : moduleValues[key];
        const desc = app.common.compact(item.description) || key;
        const hint = app.common.compact(item.hint);
        let input;
        if (Array.isArray(item.options) && item.options.length) {
          input =
            '<select id="cfg_' + app.common.esc(key) + '">' +
            item.options
              .map((opt) => '<option value="' + app.common.esc(opt) + '"' + (String(value) === String(opt) ? " selected" : "") + ">" + app.common.esc(opt) + "</option>")
              .join("") +
            "</select>";
        } else if (item.type === "bool" || item.type === "boolean") {
          input =
            '<label class="switch"><input type="checkbox" id="cfg_' + app.common.esc(key) + '"' + (value ? " checked" : "") + ' /><span class="switch-track"></span><span>' + (value ? "已启用" : "已关闭") + "</span></label>";
        } else if (item.type === "int" || item.type === "float" || item.type === "number") {
          input = '<input type="number" id="cfg_' + app.common.esc(key) + '" value="' + app.common.esc(value === undefined || value === null ? "" : value) + '" step="' + (item.type === "float" ? "0.01" : "1") + '" />';
        } else if (item.type === "list" || Array.isArray(value)) {
          input = '<input type="text" id="cfg_' + app.common.esc(key) + '" value="' + app.common.esc(Array.isArray(value) ? value.join(",") : app.common.compact(value)) + '" />';
        } else {
          input = '<input type="text" id="cfg_' + app.common.esc(key) + '" value="' + app.common.esc(value === undefined || value === null ? "" : value) + '" />';
        }
        return (
          '<div class="config-field"><div><div class="cf-label">' + app.common.esc(desc) + "</div>" +
          (hint ? '<div class="cf-desc">' + app.common.esc(hint) + "</div>" : "") +
          '<div class="cf-desc mono" style="opacity:.7;margin-top:4px">' + app.common.esc(key) + "</div></div>" +
          '<div class="cf-input">' + input + "</div></div>"
        );
      })
      .join("");

    return (
      '<div class="config-layout">' +
      '<nav class="config-nav">' + nav + "</nav>" +
      '<div style="display:grid;gap:14px">' +
        app.widgets.card(
          app.values.MODULE_LABELS[moduleId] || moduleId,
          app.common.compact(moduleSchema.description) || moduleId,
          '<form id="configForm" class="config-form">' + (fields || app.widgets.emptyState("该模块没有可配置项", "")) + "</form>",
          '<button class="btn is-primary is-sm" type="button" id="configSaveBtn">保存本模块</button>'
        ) +
        (moduleSchema.hint ? '<p class="section-note">' + app.common.esc(moduleSchema.hint) + "</p>" : "") +
      "</div>" +
      "</div>"
    );
  },
  mount(node, data) {
    const schema = data.schema || {};
    app.values.$$("[data-module]", node).forEach((button) => {
      button.addEventListener("click", () => {
        app.values.state.configModule = button.dataset.module;
        app.navigation.go("config");
      });
    });

    const saveBtn = app.values.$("#configSaveBtn", node);
    if (saveBtn) {
      saveBtn.addEventListener("click", async () => {
        const moduleId = app.values.state.configModule;
        const items = (schema[moduleId] || {}).items || {};
        const values = {};
        Object.keys(items).forEach((key) => {
          const item = items[key] || {};
          const el = app.values.$("#cfg_" + key, node);
          if (!el) return;
          if (el.type === "checkbox") values[key] = el.checked;
          else if (el.type === "number") values[key] = el.value === "" ? null : Number(el.value);
          else if (Array.isArray(item.options)) values[key] = el.value;
          else if (item.type === "list" || Array.isArray(items[key].default)) {
            values[key] = el.value.trim() === "" ? [] : el.value.split(",").map((v) => v.trim());
          } else values[key] = el.value;
        });
        await app.shell.withBusy("正在保存…", async () => {
          await app.api.apiPost("/config/module/update", { module: moduleId, values });
          app.shell.toast("已保存 " + (app.values.MODULE_LABELS[moduleId] || moduleId), "ok");
          app.pool.invalidatePool();
          // 配色改完当场生效，不用刷新页面——不然用户会以为这项又没生效
          if (moduleId === "appearance") app.appearance.syncPalette();
          app.navigation.go("config");
        });
      });
    }

    app.values.$$(".switch input", node).forEach((input) => {
      input.addEventListener("change", () => {
        const label = input.parentElement.querySelector("span:last-child");
        if (label) label.textContent = input.checked ? "已启用" : "已关闭";
      });
    });
  },
});
}
return { initialize };
}
