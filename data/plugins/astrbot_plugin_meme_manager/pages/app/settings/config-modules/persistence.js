import { openStorageGuide } from "../storage-guide.js";
import { configState } from "../settings-state.js";
// persistence: one page runtime owns shared state and DOM references.
export function createPersistence(app) {
async function loadConfig() {
    configState.busy = true;
    app.dom.notice.hidden = false;
    app.dom.notice.classList.remove("error");
    app.dom.notice.textContent = "正在读取配置…";
    app.validation.updateState("正在读取配置…");
    try {
      const [snapshot, packResult] = await Promise.all([
        app.state.api.apiGet("settings/config"),
        app.state.api.apiGet("packs").catch(() => null),
      ]);
      app.state.providers = snapshot.providers || app.state.providers;
      if (packResult) app.state.packs = packResult.packs || [];
      const defaultPack = app.state.packs.find((pack) => pack.is_default);
      app.state.semanticStatus = defaultPack
        ? await app.state.api
            .apiGet("semantic/status", { pack_id: defaultPack.id })
            .catch(() => null)
        : null;
      app.state.revision = snapshot.revision;
      app.state.changes.clear();
      app.fields.renderFields(snapshot);
      app.state.loaded = true;
      app.dom.notice.hidden = true;
      app.workflowView.updateView();
    } catch (error) {
      app.dom.notice.textContent = `读取失败：${
        error.message || String(error)
      }。可点击“重新加载”重试。`;
      app.dom.notice.classList.add("error");
    } finally {
      configState.busy = false;
      app.validation.updateState();
    }
  }

async function initialize2() {
for (const tab of app.dom.tabs) {
    tab.addEventListener("click", () => {
      app.state.active = tab.dataset.settingsTab;
      app.dom.search.value = "";
      history.replaceState(
        null,
        "",
        `${location.pathname}${location.search}#${app.state.active}`,
      );
      app.workflowView.updateView();
      window.scrollTo({ top: 0, behavior: "instant" });
    });
  }
app.dom.search.addEventListener("input", app.workflowView.updateView);
app.dom.form.addEventListener("click", (event) => {
    const trigger = event.target.closest(
      "[data-workflow-target], [data-workflow-extra]",
    );
    if (!trigger || !app.state.loaded || configState.busy) return;
    app.state.dialogOrigin = trigger;
    if (trigger.dataset.workflowExtra === "rules") {
      app.state.rulesPlaceholder = document.createComment("");
      app.dom.rulesPanel.before(app.state.rulesPlaceholder);
      app.dom.rulesDialogBody.replaceChildren(app.dom.rulesPanel);
      app.dom.rulesDialog.showModal();
      app.workflowView.updateView();
      return;
    }
    const group = app.state.groups.find(
      (item) => item.id === trigger.dataset.workflowTarget,
    );
    if (group) app.dialogs.openConfigDialog(group, trigger);
  });
app.dom.configDialog.addEventListener("click", (event) => {
    if (event.target.closest("[data-flow-dialog-close]")) app.dom.configDialog.close();
  });
app.dom.configDialogReset.addEventListener("click", () => app.dom.resetPrompts.click());
app.dom.configDialog.addEventListener("close", () => {
    app.dialogs.restoreDialogCard();
    app.workflowView.updateView();
    if (app.state.dialogOrigin?.isConnected) app.state.dialogOrigin.focus({ preventScroll: true });
  });
app.dom.rulesDialog.addEventListener("click", (event) => {
    if (event.target.closest("[data-rules-dialog-close]")) app.dom.rulesDialog.close();
  });
app.dom.rulesDialog.addEventListener("close", () => {
    if (app.state.rulesPlaceholder?.isConnected) app.state.rulesPlaceholder.replaceWith(app.dom.rulesPanel);
    app.state.rulesPlaceholder = null;
    app.dom.rulesDialogBody.replaceChildren();
    app.workflowView.updateView();
    if (app.state.dialogOrigin?.isConnected) app.state.dialogOrigin.focus({ preventScroll: true });
  });
for (const input of app.dom.modeInputs) {
    input.addEventListener("change", () => {
      if (!app.state.loaded || configState.busy || !input.checked) return;
      app.dialogs.restoreDialogCard();
      const values = [
        input.value.startsWith("semantic-"),
        input.value.endsWith("-auxiliary"),
      ];
      for (const [index, path] of app.state.modePaths.entries()) {
        app.state.controls.get(path).checked = values[index];
        if (values[index] === Boolean(app.state.fields.get(path).value))
          app.state.changes.delete(path);
        else app.state.changes.set(path, values[index]);
      }
      app.validation.updateState();
      app.workflowView.updateView();
    });
  }
}
return { loadConfig, initialize2 };
}
