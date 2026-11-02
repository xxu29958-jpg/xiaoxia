
// imagePreview: one page runtime owns shared state and DOM references.
export function createImagePreview(app) {
function setImagePreviewBusy(isBusy) {
    if (app.dom.imagePreviewLoading) {
      app.dom.imagePreviewLoading.classList.toggle("hidden", !isBusy);
    }
    if (app.dom.imagePreviewOriginalBtn) {
      app.dom.imagePreviewOriginalBtn.disabled = isBusy;
    }
  }

function closeImagePreview() {
    app.semanticEdit.setImageSemanticEditing(false);
    app.state.imagePreviewState = null;
    if (app.dom.imagePreviewModalRoot) {
      app.dom.imagePreviewModalRoot.classList.add("hidden");
      app.dom.imagePreviewModalRoot.setAttribute("aria-hidden", "true");
    }
    if (app.dom.imagePreviewImg) {
      app.dom.imagePreviewImg.removeAttribute("src");
    }
    app.semanticPreview.renderImageSemantic(null, { loading: true });
    setImagePreviewBusy(false);
    app.layout.unlockPageScroll();
  }

async function openImagePreview(category, emoji, previewDataUrl = "") {
    if (!app.dom.imagePreviewModalRoot || !app.dom.imagePreviewImg) {
      return;
    }

    const previewState = {
      category,
      emoji,
      packId: String(app.state.activeManagePackId || app.dom.managePackSelect?.value || ""),
      semantic: null,
    };
    app.state.imagePreviewState = previewState;
    app.layout.lockPageScroll();
    app.dom.imagePreviewModalRoot.classList.remove("hidden");
    app.dom.imagePreviewModalRoot.setAttribute("aria-hidden", "false");
    app.dom.imagePreviewImg.alt = `表情包预览：${emoji}`;
    app.semanticPreview.renderImageSemantic(null, { loading: true });
    if (previewDataUrl) {
      app.dom.imagePreviewImg.src = previewDataUrl;
    } else {
      app.dom.imagePreviewImg.removeAttribute("src");
    }

    setImagePreviewBusy(!previewDataUrl);
    const previewRequest = previewDataUrl
      ? Promise.resolve(previewDataUrl)
      : app.thumbnails.loadPreviewImage(category, emoji, "preview");
    const semanticRequest = app.thumbnails.loadImageSemantic(category, emoji);
    const [previewResult, semanticResult] = await Promise.allSettled([
      previewRequest,
      semanticRequest,
    ]);
    if (app.state.imagePreviewState !== previewState) {
      return;
    }
    if (previewResult.status === "rejected") {
      const error = previewResult.reason;
      console.error("打开大图预览失败:", error);
      closeImagePreview();
      app.dialogs.showToast("图片预览加载失败，请稍后重试。", "error", "加载失败");
      return;
    }
    app.dom.imagePreviewImg.src = previewResult.value;
    if (semanticResult.status === "fulfilled") {
      app.semanticPreview.renderImageSemantic(semanticResult.value);
    } else {
      console.error("读取图片语义失败:", semanticResult.reason);
      app.semanticPreview.renderImageSemantic(null, {
        error: "语义信息暂时读取失败，请稍后重新打开图片。",
      });
    }
    setImagePreviewBusy(false);

    app.dom.imagePreviewCloseBtn?.focus();
  }

async function showOriginalPreview() {
    if (!app.state.imagePreviewState || !app.dom.imagePreviewImg) {
      return;
    }

    const previewState = app.state.imagePreviewState;
    setImagePreviewBusy(true);
    try {
      const originalDataUrl = await app.thumbnails.loadPreviewImage(
        previewState.category,
        previewState.emoji,
        "original",
      );
      if (app.state.imagePreviewState === previewState) {
        app.dom.imagePreviewImg.src = originalDataUrl;
      }
    } catch (error) {
      console.error("加载原图失败:", error);
      app.dialogs.showToast("原图加载失败，可能文件过大或已不存在。", "error", "加载失败");
    } finally {
      if (app.state.imagePreviewState === previewState) {
        setImagePreviewBusy(false);
      }
    }
  }
return { setImagePreviewBusy, closeImagePreview, openImagePreview, showOriginalPreview };
}
