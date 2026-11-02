import { PreviewClient } from "../../shared/preview.js";
// status: one page runtime owns shared state and DOM references.
export function createStatus(app) {
function showToast(message, isError = false) {
    app.dom.toastContainer.replaceChildren();
    const toast = document.createElement("div");
    toast.className = `toast${isError ? " error" : ""}`;
    toast.textContent = String(message || "");
    app.dom.toastContainer.append(toast);
    window.clearTimeout(app.state.toastTimer);
    app.state.toastTimer = window.setTimeout(
      () => app.dom.toastContainer.replaceChildren(),
      4200,
    );
  }

function showNotice(message, isError = false) {
    app.dom.notice.textContent = String(message || "");
    app.dom.notice.classList.toggle("error", isError);
  }

function errorMessage(error) {
    const message = error?.message || error;
    return String(message || "操作失败，请查看日志后重试");
  }

function reportError(title, error) {
    const message = errorMessage(error);
    showNotice(`${title}：${message}`, true);
    if (message !== app.state.lastReportedError) showToast(message, true);
    app.state.lastReportedError = message;
  }

async function waitForBridgeReady(pageApi) {
    let timer = null;
    try {
      return await Promise.race([
        pageApi.ready(),
        new Promise((_, reject) => {
          timer = window.setTimeout(
            () =>
              reject(
                new Error("AstrBot 页面桥接未就绪，请从 WebUI 入口重新打开"),
              ),
            8000,
          );
        }),
      ]);
    } finally {
      window.clearTimeout(timer);
    }
  }
return { showToast, showNotice, errorMessage, reportError, waitForBridgeReady };
}
