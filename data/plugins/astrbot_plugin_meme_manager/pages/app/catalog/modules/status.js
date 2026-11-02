import { setupCatalogUpdates } from "../updates.js";
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
    while (app.dom.logList.children.length > 60) {
      app.dom.logList.lastElementChild.remove();
    }
    if (isError) {
      window.MemeUI?.toast?.(message, "error");
    }
  }

function setLoading(button, loadingText) {
    if (!button.dataset.originalHtml) {
      button.dataset.originalHtml = button.innerHTML;
    }
    button.disabled = true;
    button.textContent = loadingText;
  }

function clearLoading(button) {
    button.disabled = false;
    if (button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
    }
  }
return { addLog, setLoading, clearLoading };
}
