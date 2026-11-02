import { openStorageGuide } from "../storage-guide.js";
import { configState } from "../settings-state.js";
// events: one page runtime owns shared state and DOM references.
export function createEvents(app) {
async function initialize3() {
app.state.initialTab = location.hash.slice(1);
if (app.dom.tabs.some((tab) => tab.dataset.settingsTab === app.state.initialTab))
    app.state.active = app.state.initialTab;
window.addEventListener("hashchange", () => {
    const target = location.hash.slice(1);
    if (app.dom.tabs.some((tab) => tab.dataset.settingsTab === target)) {
      app.state.active = target;
      app.dom.search.value = "";
      app.workflowView.updateView();
    }
  });
app.dom.form.addEventListener("input", (event) => {
    const control = event.target;
    const path = control.dataset.configPath;
    const field = app.state.fields.get(path);
    if (!field) return;
    let value = control.value;
    if (field.type === "bool") value = control.checked;
    else if (["int", "float"].includes(field.type))
      value = control.valueAsNumber;
    else if (field.type === "list")
      value = control.value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean);
    if (field.secret && value === "") app.state.changes.delete(path);
    else if (JSON.stringify(value) === JSON.stringify(field.value))
      app.state.changes.delete(path);
    else app.state.changes.set(path, value);
    if (field.secret) {
      const clear = control.parentElement.querySelector("button");
      clear.textContent = "清除";
      clear.setAttribute("aria-label", `清除${app.state.labels[path] || field.label}`);
      control.placeholder = field.configured
        ? "已保存，输入新值可替换"
        : "请输入";
    }
    app.validation.updateState();
    if (
      path === "storage.provider" ||
      path === "semantic.embedding_provider_id" ||
      path.startsWith("auto_collect.")
    )
      app.workflowView.updateView();
  });
app.dom.resetPrompts.addEventListener("click", async () => {
    if (!app.state.loaded || configState.busy) return;
    const confirmed = await window.MemeUI.confirm({
      title: "恢复默认模板",
      message:
        "将前缀、后缀和两组示例恢复为当前版本的默认内容，保留各示例的开关选择。你可以检查、修改后再保存。",
      confirmText: "填入默认模板",
    });
    if (!confirmed || configState.busy) return;
    for (const [path, field] of app.state.fields) {
      if (!path.startsWith("generation.prompt.") || field.type !== "text")
        continue;
      const control = app.state.controls.get(path);
      control.value = field.default;
      control.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
app.dom.reload.addEventListener("click", async () => {
    if (
      app.state.changes.size &&
      !(await window.MemeUI.confirm({
        title: "重新加载配置",
        message: "这会丢弃当前未保存的插件配置。使用规则的更改会保留。",
        confirmText: "重新加载",
        danger: true,
      }))
    )
      return;
    await app.persistence.loadConfig();
  });
app.dom.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!app.state.changes.size || configState.busy) return;
    for (const path of app.state.changes.keys()) {
      const control = app.state.controls.get(path);
      if (!control.checkValidity()) {
        if (!app.dom.configDialog.contains(control)) {
          app.dialogs.restoreDialogCard();
          app.state.active = app.state.fields.get(path).category;
          app.dom.search.value = app.state.labels[path] || app.state.fields.get(path).label;
          app.workflowView.updateView();
        }
        control.focus();
        control.reportValidity();
        app.validation.updateState(
          `请检查「${app.state.labels[path] || app.state.fields.get(path).label}」的填写内容。`,
          true,
        );
        return;
      }
    }
    configState.busy = true;
    app.dom.notice.hidden = true;
    app.validation.updateState("正在保存并应用设置…");
    let message = "";
    let failed = false;
    try {
      const result = await app.state.api.apiPost("settings/config", {
        revision: app.state.revision,
        changes: Object.fromEntries(app.state.changes),
      });
      app.state.revision = result.revision;
      app.state.changes.clear();
      app.state.applicationFailed = result.applied === false;
      app.fields.renderFields(result);
      message = result.message || "设置已保存并生效。";
      if (result.applied === false) {
        app.dom.notice.hidden = false;
        app.dom.notice.classList.add("error");
        app.dom.notice.textContent = message;
      }
      const defaultPack = app.state.packs.find((pack) => pack.is_default);
      app.state.semanticStatus = defaultPack
        ? await app.state.api
            .apiGet("semantic/status", { pack_id: defaultPack.id })
            .catch(() => null)
        : null;
      app.workflowView.updateView();
    } catch (error) {
      failed = true;
      message = error.message || String(error);
      app.dom.notice.hidden = false;
      app.dom.notice.classList.add("error");
      app.dom.notice.textContent = message;
    } finally {
      configState.busy = false;
      app.validation.updateState(message, failed);
    }
  });
app.workflowView.updateView();
await app.persistence.loadConfig();
}
return { initialize3 };
}
