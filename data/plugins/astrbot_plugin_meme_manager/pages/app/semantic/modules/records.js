import { PreviewClient } from "../../shared/preview.js";
// records: one page runtime owns shared state and DOM references.
export function createRecords(app) {
function renderRecords(data) {
    app.dom.recordsBox.classList.remove("empty");
    const records = Array.isArray(data.items) ? data.items : [];
    app.state.visibleRecordPaths = new Set(records.map((item) => item.relative_path));
    app.state.recordsCurrentPage = Number(data.page || app.state.recordsCurrentPage || 1);
    app.state.recordsTotalPages = Math.max(1, Number(data.total_pages || 1));
    app.dom.recordCount.textContent = `共 ${Number(data.total || 0)} 条`;
    app.dom.recordsPage.textContent = `第 ${app.state.recordsCurrentPage} / ${app.state.recordsTotalPages} 页`;
    app.dom.recordsPrev.disabled = app.state.recordsCurrentPage <= 1 || app.state.requestRunning;
    app.dom.recordsNext.disabled =
      app.state.recordsCurrentPage >= app.state.recordsTotalPages || app.state.requestRunning;
    const snapshot = JSON.stringify([app.dom.packSelect.value, app.state.recordsStatus, data]);
    if (snapshot === app.state.lastRecordsSnapshot) return;
    app.state.lastRecordsSnapshot = snapshot;
    const focusedPreview = app.dom.recordsBox.contains(document.activeElement)
      ? document.activeElement.dataset.previewPath
      : null;
    app.dom.recordsBox.replaceChildren();
    if (!records.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent =
        app.state.recordsStatus === "all"
          ? "还没有语义记录。选择资源包后，点击“一键完整语义化”开始处理。"
          : "没有符合此筛选条件的记录，可切换为“全部”查看。";
      app.dom.recordsBox.append(empty);
      return;
    }
    records.forEach((item) => {
      const row = document.createElement("div");
      row.className = "record";
      const path = document.createElement("div");
      path.className = "record-path";
      path.textContent = item.relative_path || "路径不可用";
      const copy = document.createElement("div");
      copy.className = "record-copy";
      const tags = Array.isArray(item.tags) ? item.tags.join("、") : "";
      copy.textContent = `描述结果：${item.caption || "暂无返回结果"}${
        tags ? `；标签：${tags}` : ""
      }`;
      if (item.visible_text)
        copy.textContent += `；图片文字：${item.visible_text}`;
      if (item.reclassification_status) {
        const reclassification = document.createElement("div");
        reclassification.className = "record-reclassification";
        reclassification.textContent = `自动重分类：${
          item.reclassified_from_category || "原分类"
        } → ${item.reclassified_to_category || item.category || "当前分类"}${
          item.reclassification_reason
            ? `；原因：${item.reclassification_reason}`
            : ""
        }`;
        copy.append(reclassification);
      }
      if (item.error) {
        const error = document.createElement("div");
        error.className = "record-error";
        error.textContent = `失败原因：${item.error}`;
        copy.append(error);
      }
      const state = document.createElement("div");
      state.className = "record-state";
      const statusText = (value) =>
        ({
          pending: "待处理",
          running: "进行中",
          done: "已完成",
          failed: "失败",
          cleared: "已清理",
        })[value] ||
        value ||
        "-";
      state.textContent = `描述：${statusText(
        item.caption_status,
      )} / 向量：${statusText(item.embedding_status)}`;
      const previewButton = document.createElement("button");
      previewButton.type = "button";
      previewButton.className = "record-preview-button";
      previewButton.dataset.previewPath = item.relative_path || "";
      previewButton.setAttribute("aria-haspopup", "dialog");
      previewButton.title = "点击放大查看表情包";
      previewButton.setAttribute(
        "aria-label",
        `放大查看 ${item.relative_path || "表情包"}`,
      );
      const previewImage = document.createElement("img");
      previewImage.alt = `表情包缩略图：${item.relative_path || ""}`;
      const previewText = document.createElement("span");
      previewText.textContent = "加载缩略图";
      const cachedPreview = app.state.previewCache.get(`${app.preview.previewKey(item)}:preview`);
      if (cachedPreview) {
        previewImage.src = cachedPreview;
        previewButton.append(previewImage);
      } else {
        previewButton.append(previewText);
        void app.preview.loadRecordImage(item)
          .then((dataUrl) => {
            if (!previewButton.isConnected) return;
            previewImage.src = dataUrl;
            previewButton.replaceChildren(previewImage);
          })
          .catch(() => {
            if (previewButton.isConnected)
              previewText.textContent = "加载失败，点击重试";
          });
      }
      previewButton.addEventListener("click", () => {
        const dataUrl =
          app.state.previewCache.get(`${app.preview.previewKey(item)}:preview`) ||
          previewImage.src ||
          "";
        void app.preview.openImagePreview(item, dataUrl);
      });
      row.append(path, copy, state, previewButton);
      app.dom.recordsBox.append(row);
    });
    if (focusedPreview) {
      Array.from(app.dom.recordsBox.querySelectorAll(".record-preview-button"))
        .find((button) => button.dataset.previewPath === focusedPreview)
        ?.focus({ preventScroll: true });
    }
  }

async function initialize1() {
({ applySecureNavLinks: app.state.applySecureNavLinks } = window.MemeUI);
}
return { renderRecords, initialize1 };
}
