import { PreviewClient } from "../../shared/preview.js";
// preview: one page runtime owns shared state and DOM references.
export function createPreview(app) {
function imageLocation(item) {
    const parts = String(item?.relative_path || "")
      .replace(/\\/g, "/")
      .split("/")
      .filter(Boolean);
    if (parts[0] === "memes") parts.shift();
    const filename = parts.pop() || "";
    return { category: parts.join("/"), filename };
  }

function previewKey(item) {
    return `${app.dom.packSelect.value}:${String(item?.relative_path || "")}`;
  }

function rememberPreview(key, dataUrl) {
    app.state.previewCache.delete(key);
    app.state.previewCache.set(key, dataUrl);
    while (app.state.previewCache.size > 24) {
      app.state.previewCache.delete(app.state.previewCache.keys().next().value);
    }
  }

async function loadRecordImage(item, size = "preview") {
    const requestedPackId = app.dom.packSelect.value;
    const key = `${previewKey(item)}:${size}`;
    if (size === "preview" && app.state.previewCache.has(key))
      return app.state.previewCache.get(key);
    if (app.state.previewRequests.has(key)) return await app.state.previewRequests.get(key);
    const { category, filename } = imageLocation(item);
    if (!category || !filename) throw new Error("图片路径不可用");
    const requestPromise = app.state.previews
      .get(
        "meme_image_data",
        {
          managed_pack_id: requestedPackId,
          category,
          filename,
          size,
        },
        {
          priority: size === "original",
          isCurrent: () =>
            requestedPackId === app.dom.packSelect.value &&
            (size === "original" || app.state.visibleRecordPaths.has(item.relative_path)),
        },
      )
      .then((data) => {
        if (!data?.data_url) throw new Error("图片接口未返回预览数据");
        if (size === "preview") rememberPreview(key, data.data_url);
        return data.data_url;
      })
      .finally(() => app.state.previewRequests.delete(key));
    app.state.previewRequests.set(key, requestPromise);
    return await requestPromise;
  }

function closeImagePreview() {
    app.state.activePreviewKey = "";
    app.state.activePreviewItem = null;
    app.state.previewVersion += 1;
    app.dom.imagePreviewMask.classList.add("hidden");
    app.dom.imagePreviewMask.setAttribute("aria-hidden", "true");
    app.dom.imagePreviewImg.removeAttribute("src");
    app.dom.imagePreviewLoading.textContent = "正在加载大图……";
    app.dom.imagePreviewLoading.classList.remove("hidden");
    app.dom.imagePreviewRetry.classList.add("hidden");
  }

async function openImagePreview(item, previewDataUrl = "") {
    const key = previewKey(item);
    const version = ++app.state.previewVersion;
    app.state.activePreviewKey = key;
    app.state.activePreviewItem = item;
    app.dom.imagePreviewTitle.textContent = item.relative_path || "表情包预览";
    app.dom.imagePreviewMask.classList.remove("hidden");
    app.dom.imagePreviewMask.setAttribute("aria-hidden", "false");
    app.dom.imagePreviewLoading.textContent = "正在加载大图……";
    app.dom.imagePreviewLoading.classList.remove("hidden");
    app.dom.imagePreviewRetry.classList.add("hidden");
    if (previewDataUrl) app.dom.imagePreviewImg.src = previewDataUrl;
    else app.dom.imagePreviewImg.removeAttribute("src");
    try {
      const original = await loadRecordImage(item, "original");
      if (app.state.activePreviewKey !== key || version !== app.state.previewVersion) return;
      app.dom.imagePreviewImg.src = original;
      app.dom.imagePreviewLoading.classList.add("hidden");
    } catch (error) {
      if (app.state.activePreviewKey !== key || version !== app.state.previewVersion) return;
      app.dom.imagePreviewLoading.textContent = "大图加载失败，已保留缩略图";
      if (!previewDataUrl) {
        app.dom.imagePreviewLoading.textContent = "图片预览加载失败";
      }
      app.dom.imagePreviewRetry.classList.remove("hidden");
    }
  }
return { imageLocation, previewKey, rememberPreview, loadRecordImage, closeImagePreview, openImagePreview };
}
