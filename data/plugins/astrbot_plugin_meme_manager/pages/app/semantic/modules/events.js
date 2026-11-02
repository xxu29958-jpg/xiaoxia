import { PreviewClient } from "../../shared/preview.js";
// events: one page runtime owns shared state and DOM references.
export function createEvents(app) {
async function initialize3() {
app.dom.buttons.forEach((button) =>
    button.addEventListener("click", () =>
      app.actions.runAction(app.state.apiPost, button.dataset.action),
    ),
  );
app.dom.autoInboxSemanticize.addEventListener("click", () => app.actions.openCollectionReview());
app.dom.recordsPrev.addEventListener("click", async () => {
    if (app.state.recordsCurrentPage <= 1 || app.state.requestRunning) return;
    app.state.recordsCurrentPage -= 1;
    try {
      await app.queries.loadStatus(app.state.apiGet);
    } catch (error) {
      await app.status.reportError("读取任务记录失败", error);
    }
  });
app.dom.recordsNext.addEventListener("click", async () => {
    if (app.state.recordsCurrentPage >= app.state.recordsTotalPages || app.state.requestRunning) return;
    app.state.recordsCurrentPage += 1;
    try {
      await app.queries.loadStatus(app.state.apiGet);
    } catch (error) {
      await app.status.reportError("读取任务记录失败", error);
    }
  });
app.dom.recordsFilter.addEventListener("change", async () => {
    app.state.recordsStatus = app.dom.recordsFilter.value || "all";
    app.state.recordsCurrentPage = 1;
    try {
      await app.queries.loadStatus(app.state.apiGet);
    } catch (error) {
      await app.status.reportError("读取筛选结果失败", error);
    }
  });
app.dom.concurrencyInput.addEventListener("change", () => {
    const value = Math.max(
      1,
      Math.min(16, Math.floor(Number(app.dom.concurrencyInput.value)) || 1),
    );
    app.dom.concurrencyInput.value = String(value);
    app.state.concurrencyDirty = true;
  });
app.dom.packSelect.addEventListener("change", async () => {
    app.state.recordsCurrentPage = 1;
    app.state.latestStatus = null;
    app.state.lastActionError = "";
    app.state.lastRecordsSnapshot = "";
    app.state.concurrencyDirty = false;
    app.preview.closeImagePreview();
    app.dom.taskState.textContent = "正在加载所选资源包…";
    app.dom.taskProgress.value = 0;
    app.dom.taskProgressLabel.textContent = "";
    app.dom.statusBox.replaceChildren();
    app.dom.recordsBox.replaceChildren();
    try {
      const statusData = await app.queries.loadStatus(app.state.apiGet);
      if (statusData?.dimension_rebuild_required) {
        document.querySelector(".advanced-actions").open = true;
      }
    } catch (error) {
      await app.status.reportError("读取状态失败", error);
    }
  });
try {
    app.controls.setBusy(true);
    await app.status.waitForBridgeReady(app.state.pageApi);
    await app.state.applySecureNavLinks(app.state.pageApi);
    await app.queries.loadPacks(app.state.apiGet);
    await app.queries.loadStatus(app.state.apiGet);
  } catch (error) {
    app.controls.setBusy(false);
    app.dom.statusError.textContent = `加载失败：${app.status.errorMessage(
      error,
    )}。请检查连接后点击“重试刷新”。`;
    app.dom.statusError.classList.remove("hidden");
    app.dom.statusRefreshLabel.textContent = "连接中断";
    app.dom.statusRetry.textContent = "重试刷新";
    app.dom.taskState.textContent = "暂时无法读取任务状态";
    app.dom.recordsBox.setAttribute("aria-busy", "false");
  }
window.setInterval(app.metrics.updateTaskTimer, 1000);
window.setInterval(() => {
    if (document.hidden || app.state.statusLoading) return;
    void app.queries.loadStatus(app.state.apiGet).catch(() => {});
  }, 3000);
}
return { initialize3 };
}
