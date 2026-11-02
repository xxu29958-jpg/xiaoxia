
// status: one page runtime owns shared state and DOM references.
export function createStatus(app) {
function addLog(message, isError = false) {
    const item = document.createElement("div");
    item.className = `log-item${isError ? " error" : ""}`;
    const now = new Date();
    item.textContent = `[${now.toLocaleTimeString("zh-CN", {
      hour12: false,
    })}] ${message}`;
    app.dom.logList.prepend(item);
    while (app.dom.logList.children.length > 80) app.dom.logList.lastElementChild.remove();
  }

function setLoading(button, loadingText) {
    button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = loadingText;
  }

function clearLoading(button) {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    if (button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
      delete button.dataset.originalHtml;
    }
  }

function setPackTransferResult(element, message = "", type = "") {
    if (!element) {
      return;
    }
    element.textContent = String(message || "");
    element.classList.toggle("success", type === "success");
    element.classList.toggle("error", type === "error");
  }

function updateRulesState(message = "", type = "") {
    const dirty = app.state.rulesLoaded && JSON.stringify(app.state.rules) !== app.state.savedRulesSnapshot;
    app.dom.rulesList.inert = app.state.rulesBusy;
    app.dom.rulesList.setAttribute("aria-busy", String(app.state.rulesBusy));
    app.dom.addRuleBtn.disabled = app.state.rulesBusy || !app.state.rulesLoaded || !app.state.installedPacks.length;
    app.dom.reloadRulesBtn.disabled = app.state.rulesBusy;
    app.dom.saveRulesBtn.disabled = app.state.rulesBusy || !app.state.rulesLoaded || !dirty;
    app.dom.importBackupBtn.disabled = app.state.rulesBusy || app.state.packImportBusy;
    app.dom.packImportConfirmBtn.disabled = app.state.rulesBusy || app.state.packImportBusy;
    setPackTransferResult(
      app.dom.rulesSaveStatus,
      message ||
        (dirty
          ? "有未保存的更改"
          : app.state.rulesLoaded
          ? "所有更改已保存"
          : "正在读取设置…"),
      type,
    );
    app.dom.rulesSaveStatus.classList.toggle("unsaved", dirty);
  }
return { addLog, setLoading, clearLoading, setPackTransferResult, updateRulesState };
}
