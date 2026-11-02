import { PreviewClient } from "../../shared/preview.js";
// queries: one page runtime owns shared state and DOM references.
export function createQueries(app) {
async function loadPacks(apiGet) {
    const data = await apiGet("packs");
    const packs = Array.isArray(data?.packs) ? data.packs : [];
    app.dom.packSelect.replaceChildren();
    packs.forEach((item) => {
      const option = document.createElement("option");
      option.value = String(item.id || "");
      option.textContent = item.name || item.id || "未命名资源包";
      app.dom.packSelect.append(option);
    });
    app.state.packsLoaded = true;
    app.dom.statusError.classList.add("hidden");
    app.dom.statusRetry.textContent = "刷新";
    if (!packs.length) {
      app.dom.packSelect.add(new Option("暂无资源包", ""));
      app.dom.taskState.textContent = "暂无资源包";
      app.dom.taskGuidance.textContent =
        "请先在表情管理中创建资源包，或从资源广场导入。";
      app.dom.recordsBox.textContent = "暂无记录，请先添加资源包。";
      app.dom.recordsBox.classList.add("empty");
      app.dom.recordsBox.setAttribute("aria-busy", "false");
      app.dom.statusRefreshLabel.textContent = "暂无任务";
    }
    app.controls.setBusy(false);
  }

async function loadStatus(apiGet) {
    if (!app.dom.packSelect.value || app.state.requestRunning) return null;
    const requestSequence = ++app.state.statusRequestSequence;
    const requestedPackId = app.dom.packSelect.value;
    app.state.statusLoading = true;
    app.dom.recordsBox.setAttribute("aria-busy", "true");
    app.controls.updateButtonState();
    const params = { pack_id: requestedPackId };
    const itemParams = {
      ...params,
      page: app.state.recordsCurrentPage,
      page_size: app.state.recordsPageSize,
    };
    if (app.state.recordsStatus !== "all") itemParams.status = app.state.recordsStatus;
    let statusData;
    let recordsData;
    try {
      [statusData, recordsData] = await Promise.all([
        apiGet("semantic/status", params),
        apiGet("semantic/items", itemParams),
      ]);
    } catch (error) {
      if (
        requestSequence !== app.state.statusRequestSequence ||
        requestedPackId !== app.dom.packSelect.value
      )
        return null;
      app.dom.statusError.textContent = `刷新失败：${app.status.errorMessage(error)}。${
        app.state.latestStatus ? "已保留上次结果，" : ""
      }可点击“重试刷新”。`;
      app.dom.statusError.classList.remove("hidden");
      app.dom.statusRefreshLabel.textContent = "连接中断";
      app.dom.statusRetry.textContent = "重试刷新";
      throw error;
    } finally {
      if (requestSequence === app.state.statusRequestSequence) {
        app.state.statusLoading = false;
        app.dom.recordsBox.setAttribute("aria-busy", "false");
        app.controls.updateButtonState();
      }
    }
    if (
      requestSequence !== app.state.statusRequestSequence ||
      requestedPackId !== app.dom.packSelect.value
    )
      return null;
    if (!statusData.last_error) app.state.lastReportedError = "";
    app.dom.statusError.classList.add("hidden");
    app.dom.statusRefreshLabel.textContent = "每 3 秒自动刷新";
    app.dom.statusRetry.textContent = "刷新";
    app.metrics.renderStatus(statusData);
    app.records.renderRecords(recordsData);
    return statusData;
  }
return { loadPacks, loadStatus };
}
