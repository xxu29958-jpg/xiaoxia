import { PreviewClient } from "../../shared/preview.js";
// controls: one page runtime owns shared state and DOM references.
export function createControls(app) {
function updateButtonState() {
    app.dom.buttons.forEach((button) => {
      const action = button.dataset.action;
      const needsEmbedding =
        ["start", "retry", "index", "dimension", "force"].includes(action) ||
        (action === "resume" && app.state.latestStatus?.task_mode !== "caption_only");
      const needsCompleteCaption = ["index", "dimension"].includes(action);
      const needsVision =
        ["start", "resume", "retry", "force"].includes(action) &&
        (action === "force" || !app.state.captionComplete);
      const allowedByTaskState =
        !app.state.latestStatus ||
        {
          start: app.state.latestStatus.can_start,
          force: app.state.latestStatus.can_start,
          resume: app.state.latestStatus.can_resume,
          retry: app.state.latestStatus.can_retry,
          pause: app.state.latestStatus.can_pause,
          index: app.state.latestStatus.can_rebuild_index,
          dimension: app.state.latestStatus.can_rebuild_index,
          clear: !app.state.latestStatus.external_operation,
          "delete-all": !app.state.latestStatus.external_operation,
        }[action] !== false;
      button.disabled =
        app.state.requestRunning ||
        !app.state.latestStatus ||
        !app.dom.packSelect.value ||
        !allowedByTaskState ||
        (needsEmbedding && !app.state.embeddingReady) ||
        (needsCompleteCaption && !app.state.captionComplete) ||
        (needsVision && !app.state.visionReady);
      if (action === "pause" && app.state.latestStatus?.task_phase === "indexing") {
        button.title = "正在建立向量索引，这个收尾阶段不能暂停";
      } else if (action === "pause") {
        button.title = "立即中断本轮模型请求，未完成图片会退回等待队列";
      } else if (needsEmbedding && !app.state.embeddingReady) {
        button.title = "请先在设置中心配置可用的向量模型";
      } else if (needsVision && !app.state.visionReady) {
        button.title = "请先在设置中心配置可用的视觉模型";
      } else if (needsCompleteCaption && !app.state.captionComplete) {
        button.title = "请先完成图片描述生成";
      } else {
        button.removeAttribute("title");
      }
    });
    const queueIsActive = Boolean(app.state.latestStatus?.worker_alive);
    app.dom.concurrencyInput.disabled =
      app.state.requestRunning || queueIsActive || !app.state.latestStatus;
    app.dom.concurrencyHint.textContent = queueIsActive
      ? `当前队列固定为 ${
          app.state.latestStatus?.concurrency || app.dom.concurrencyInput.value || 1
        } 并发；如需调整，请先清空队列后重新开始。`
      : "同时提交给视觉模型的图片上限，建议按模型限流设置。";
    app.dom.autoInboxSemanticize.disabled =
      app.state.requestRunning ||
      !app.state.latestStatus ||
      app.state.pendingAutoInboxCount <= 0 ||
      !app.state.embeddingReady ||
      !app.state.visionReady ||
      app.state.latestStatus?.can_start === false;
    app.dom.recordsPrev.disabled =
      app.state.requestRunning || app.state.statusLoading || app.state.recordsCurrentPage <= 1;
    app.dom.recordsNext.disabled =
      app.state.requestRunning ||
      app.state.statusLoading ||
      app.state.recordsCurrentPage >= app.state.recordsTotalPages;
    app.dom.recordsFilter.disabled = app.state.requestRunning || !app.state.packsLoaded;
    app.dom.statusRetry.disabled = app.state.requestRunning || app.state.statusLoading;
  }

function setBusy(value) {
    if (value) {
      app.state.statusRequestSequence += 1;
      app.state.statusLoading = false;
      app.dom.recordsBox.setAttribute("aria-busy", "false");
    }
    app.state.requestRunning = value;
    updateButtonState();
    app.dom.packSelect.disabled = value;
    document
      .querySelector(".control-panel")
      .setAttribute("aria-busy", String(value));
  }
return { updateButtonState, setBusy };
}
