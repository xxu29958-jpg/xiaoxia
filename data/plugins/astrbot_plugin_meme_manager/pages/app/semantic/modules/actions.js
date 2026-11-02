import { PreviewClient } from "../../shared/preview.js";
// actions: one page runtime owns shared state and DOM references.
export function createActions(app) {
async function runAction(apiPost, name) {
    if (!app.dom.packSelect.value || app.state.requestRunning) return;
    const requestedPackId = app.dom.packSelect.value;
    const confirmation = {
      force: {
        title: "强制重新生成",
        message:
          "将重新调用视觉模型处理全部图片，覆盖已有描述，并产生新的模型调用消耗。",
        confirmText: "覆盖并重新生成",
      },
      clear: {
        title: "清空当前任务队列",
        message:
          "将取消正在运行的语义任务，并清空待处理队列。已完成的描述和原图片会保留。",
        confirmText: "取消并清空队列",
      },
      "delete-all": {
        title: "删除全部语义化数据",
        message:
          "将删除全部图片描述、标签、失败记录、任务状态和本机向量索引，保留原图片。删除后无法恢复描述，重新语义化会再次产生模型调用消耗。",
        confirmText: "删除语义化数据",
      },
    }[name];
    if (confirmation) {
      const confirmed = await window.MemeUI.confirm({
        ...confirmation,
        message: `资源包：${
          app.dom.packSelect.selectedOptions[0]?.textContent || requestedPackId
        }\n\n${confirmation.message}`,
        danger: true,
      });
      if (!confirmed) return;
    }
    if (requestedPackId !== app.dom.packSelect.value || app.state.requestRunning) return;
    const route =
      name === "index"
        ? "semantic/rebuild-index"
        : name === "dimension"
        ? "semantic/rebuild-index"
        : name === "clear"
        ? "semantic/clear-local-state"
        : name === "delete-all"
        ? "semantic/delete-all"
        : `semantic/${name === "force" ? "start" : name}`;
    const mode = "full";
    const startsTask = ["start", "retry", "resume", "force"].includes(name);
    app.state.lastActionError = "";
    app.controls.setBusy(true);
    app.status.showNotice(
      name === "pause" ? "正在中断本轮请求并恢复等待队列……" : "正在提交操作……",
    );
    try {
      const body = {
        pack_id: app.dom.packSelect.value,
        mode,
        force: name === "force" || name === "dimension",
      };
      if (startsTask) {
        body.concurrency = Math.max(
          1,
          Math.min(16, Math.floor(Number(app.dom.concurrencyInput.value)) || 1),
        );
      }
      const result = await apiPost(route, body);
      if (startsTask) app.state.concurrencyDirty = false;
      app.status.showToast(result?.message || "操作已提交");
      app.status.showNotice(result?.message || "操作已提交");
    } catch (error) {
      app.state.lastActionError = `操作失败：${app.status.errorMessage(
        error,
      )}。请检查提示后重新提交。`;
      await app.status.reportError("操作失败", error);
    } finally {
      app.controls.setBusy(false);
      try {
        await app.queries.loadStatus(app.state.apiGet);
      } catch (error) {
        await app.status.reportError("读取状态失败", error);
      }
    }
  }

async function openCollectionReview() {
    const token = await window.MemeUI.ensureNavAuthToken();
    window.location.href = window.MemeUI.withCurrentAuthParams(
      "../a_manage/index.html",
      {
        managed_pack_id: app.dom.packSelect.value,
        review_collection: "1",
        asset_token: token || null,
      },
    ).toString();
  }

async function initialize2() {
app.dom.imagePreviewClose.addEventListener("click", app.preview.closeImagePreview);
app.dom.imagePreviewRetry.addEventListener("click", () => {
    if (app.state.activePreviewItem)
      void app.preview.openImagePreview(
        app.state.activePreviewItem,
        app.dom.imagePreviewImg.getAttribute("src") || "",
      );
  });
app.dom.imagePreviewMask.addEventListener("click", (event) => {
    if (event.target === app.dom.imagePreviewMask) app.preview.closeImagePreview();
  });
document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      !app.dom.imagePreviewMask.classList.contains("hidden")
    ) {
      app.preview.closeImagePreview();
    }
  });
app.state.pageApi = window.AstrBotPluginPage;
if (!app.state.pageApi) {
    app.status.reportError(
      "页面无法连接 AstrBot",
      "请从 AstrBot WebUI 的“语义化”入口打开此页面，不要直接访问本地 HTML 文件。",
    );
    app.dom.taskState.textContent = "无法连接 AstrBot";
    app.dom.statusRefreshLabel.textContent = "未连接";
    app.dom.statusRetry.disabled = true;
    app.dom.recordsBox.setAttribute("aria-busy", "false");
    return;
  }
app.state.apiGet = (path, params = {}) => app.state.pageApi.apiGet(path, params);
app.state.previews = new PreviewClient(app.state.pageApi);
app.state.apiPost = (path, body = {}) => app.state.pageApi.apiPost(path, body);
app.dom.statusRetry.addEventListener("click", async () => {
    if (app.state.requestRunning || app.state.statusLoading) return;
    app.dom.statusRetry.disabled = true;
    app.dom.statusRefreshLabel.textContent = "正在刷新…";
    try {
      await app.status.waitForBridgeReady(app.state.pageApi);
      if (!app.state.packsLoaded || !app.dom.packSelect.value) await app.queries.loadPacks(app.state.apiGet);
      await app.queries.loadStatus(app.state.apiGet);
    } catch (error) {
      app.dom.statusError.textContent = `加载失败：${app.status.errorMessage(
        error,
      )}。请检查连接后重试。`;
      app.dom.statusError.classList.remove("hidden");
      app.dom.statusRefreshLabel.textContent = "连接中断";
      app.dom.statusRetry.textContent = "重试刷新";
    } finally {
      app.controls.updateButtonState();
    }
  });
}
return { runAction, openCollectionReview, initialize2 };
}
