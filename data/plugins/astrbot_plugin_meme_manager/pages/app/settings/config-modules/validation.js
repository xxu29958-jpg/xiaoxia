import { openStorageGuide } from "../storage-guide.js";
import { configState } from "../settings-state.js";
// validation: one page runtime owns shared state and DOM references.
export function createValidation(app) {
function updateState(message = "", error = false) {
    configState.dirty = app.state.changes.size > 0;
    app.dom.save.disabled = !app.state.loaded || !app.state.changes.size || configState.busy;
    app.dom.reload.disabled = configState.busy;
    app.dom.resetPrompts.disabled = !app.state.loaded || configState.busy;
    app.dom.configDialogReset.disabled = !app.state.loaded || configState.busy;
    app.dom.configDialogSave.disabled = !app.state.loaded || !app.state.changes.size || configState.busy;
    app.dom.sections.inert = configState.busy;
    app.dom.modePanel.inert = !app.state.loaded || configState.busy;
    app.dom.workflow.inert = !app.state.loaded || configState.busy;
    app.dom.configDialogBody.inert = !app.state.loaded || configState.busy;
    app.dom.collectionWorkflow.inert = !app.state.loaded || configState.busy;
    app.dom.form.setAttribute("aria-busy", String(configState.busy));
    app.dom.save.textContent = configState.busy ? "正在处理…" : "保存并应用";
    app.dom.status.textContent =
      message ||
      (app.state.changes.size
        ? `有 ${app.state.changes.size} 项更改未保存`
        : app.state.loaded
        ? "所有更改已保存"
        : "尚未读取配置");
    app.dom.status.classList.toggle("error", error);
    app.dom.configDialogStatus.textContent = app.dom.status.textContent;
    app.dom.configDialogStatus.classList.toggle("error", error);
    for (const tab of app.dom.tabs) {
      const dot = tab.querySelector(".settings-dirty-dot");
      if (dot)
        dot.hidden = ![...app.state.changes.keys()].some(
          (path) =>
            app.state.fields.get(path)?.category === tab.dataset.settingsTab ||
            tab.dataset.settingsTab === "models",
        );
    }
  }
return { updateState };
}
