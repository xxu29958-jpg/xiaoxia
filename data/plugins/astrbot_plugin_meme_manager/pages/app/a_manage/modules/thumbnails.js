
// thumbnails: one page runtime owns shared state and DOM references.
export function createThumbnails(app) {
function getImageRequestParams(category, emoji, size = "preview") {
    return {
      category,
      filename: emoji,
      size,
    };
  }

function setEmojiPreviewLoading(emojiItem) {
    emojiItem.classList.remove("emoji-load-error", "emoji-loaded");
    emojiItem.classList.add("emoji-loading");
    emojiItem.setAttribute("aria-label", "正在加载表情包预览");
  }

function setEmojiPreviewLoaded(emojiItem, dataUrl) {
    app.state.previewObserver?.unobserve(emojiItem);
    emojiItem.style.backgroundImage = `url("${dataUrl}")`;
    emojiItem.dataset.previewDataUrl = dataUrl;
    emojiItem.classList.remove("emoji-loading", "emoji-load-error");
    emojiItem.classList.add("emoji-loaded");
    emojiItem.setAttribute(
      "aria-label",
      `预览表情包 ${emojiItem.dataset.emoji || ""}`,
    );
  }

function setEmojiPreviewError(emojiItem) {
    emojiItem.style.backgroundImage = "";
    emojiItem.classList.remove("emoji-loading", "emoji-loaded");
    emojiItem.classList.add("emoji-load-error");
    emojiItem.setAttribute("aria-label", "预览加载失败，点击重试");
  }

async function loadEmojiPreview(emojiItem, { force = false } = {}) {
    if (
      !emojiItem ||
      (!force &&
        (emojiItem.dataset.previewDataUrl ||
          emojiItem.dataset.loading === "true"))
    ) {
      return;
    }

    const { category, emoji } = emojiItem.dataset;
    if (!category || !emoji) {
      setEmojiPreviewError(emojiItem);
      return;
    }

    emojiItem.dataset.loading = "true";
    setEmojiPreviewLoading(emojiItem);
    try {
      const data = await app.api.apiGet(
        "meme_image_data",
        getImageRequestParams(category, emoji, "preview"),
        {
          isCurrent: () =>
            emojiItem.isConnected &&
            (force || emojiItem.dataset.previewVisible !== "false"),
        },
      );
      if (!data?.data_url) {
        throw new Error("图片接口未返回预览数据");
      }
      setEmojiPreviewLoaded(emojiItem, data.data_url);
    } catch (error) {
      if (!emojiItem.isConnected || error?.name === "AbortError") return;
      console.error("加载表情预览失败：", error);
      setEmojiPreviewError(emojiItem);
    } finally {
      emojiItem.dataset.loading = "false";
    }
  }

function retryEmojiPreview(emojiItem) {
    if (!emojiItem) {
      return;
    }
    delete emojiItem.dataset.previewDataUrl;
    void loadEmojiPreview(emojiItem, { force: true });
  }

async function loadPreviewImage(category, emoji, size = "preview") {
    const data = await app.api.apiGet(
      "meme_image_data",
      getImageRequestParams(category, emoji, size),
      { priority: true },
    );
    if (!data?.data_url) {
      throw new Error("图片接口未返回预览数据");
    }
    return data.data_url;
  }

async function loadImageSemantic(category, emoji) {
    const data = await app.api.apiGet("meme_image_semantic", {
      category,
      filename: emoji,
    });
    return data?.semantic || { status: "none" };
  }
return { getImageRequestParams, setEmojiPreviewLoading, setEmojiPreviewLoaded, setEmojiPreviewError, loadEmojiPreview, retryEmojiPreview, loadPreviewImage, loadImageSemantic };
}
