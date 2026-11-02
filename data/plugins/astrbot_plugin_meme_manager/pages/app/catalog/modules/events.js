import { setupCatalogUpdates } from "../updates.js";
// events: one page runtime owns shared state and DOM references.
export function createEvents(app) {
async function initialize2() {
app.dom.catalogSearch.addEventListener("input", app.catalog.renderCatalog);
app.state.refreshingVisibility = false;
app.dom.refreshOnReturn = async () => {
    if (document.hidden || app.state.installBusy || app.state.indexLoading || app.state.refreshingVisibility)
      return;
    app.state.refreshingVisibility = true;
    try {
      await app.catalog.refreshInstalledSet();
      app.catalog.renderCatalog();
    } finally {
      app.state.refreshingVisibility = false;
    }
  };
window.addEventListener("focus", app.dom.refreshOnReturn);
window.addEventListener("pageshow", app.dom.refreshOnReturn);
document.addEventListener("visibilitychange", app.dom.refreshOnReturn);
app.dom.catalogFilter.addEventListener("change", app.catalog.renderCatalog);
app.dom.refreshCatalogBtn.addEventListener("click", () => void app.catalog.fetchIndex());
document
    .getElementById("install-progress-hide")
    .addEventListener("click", () => {
      app.dom.installProgressDialog.classList.add("hidden");
      app.dom.installProgressDialog.setAttribute("aria-hidden", "true");
    });
document
    .getElementById("show-install-progress")
    .addEventListener("click", () => {
      if (!app.state.activeInstallJobId) return;
      app.dom.installProgressDialog.classList.remove("hidden");
      app.dom.installProgressDialog.setAttribute("aria-hidden", "false");
    });
app.dom.installDialogCancel.addEventListener("click", () => {
    app.install.closeInstallDialog();
  });
app.dom.installDialogConfirm.addEventListener("click", () => {
    void app.install.confirmInstallDialog();
  });
app.dom.installDialog.addEventListener("click", (event) => {
    if (event.target === app.dom.installDialog) {
      app.install.closeInstallDialog();
    }
  });
app.dom.installProgressCancel.addEventListener("click", async () => {
    if (!app.state.activeInstallJobId || app.dom.installProgressCancel.disabled) return;
    app.dom.installProgressCancel.disabled = true;
    app.dom.installProgressCancel.textContent = "正在取消...";
    try {
      await app.api.apiPost("community/install/cancel", {
        job_id: app.state.activeInstallJobId,
      });
      app.dom.installProgressPhase.textContent = "正在取消安装，请稍候";
    } catch (error) {
      app.dom.installProgressCancel.disabled = false;
      app.dom.installProgressCancel.textContent = "取消安装";
      app.status.addLog(`取消安装失败: ${error?.message || String(error)}`, true);
    }
  });
app.status.addLog("资源广场已就绪");
await app.install.restoreActiveInstall();
await app.catalog.loadCachedIndex({ silentOnMissing: true });
await app.catalog.fetchIndex();
await app.state.updates.resume();
}
return { initialize2 };
}
