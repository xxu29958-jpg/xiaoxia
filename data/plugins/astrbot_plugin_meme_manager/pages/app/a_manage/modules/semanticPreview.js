
// semanticPreview: one page runtime owns shared state and DOM references.
export function createSemanticPreview(app) {
function renderImageSemantic(semantic, { loading = false, error = "" } = {}) {
    if (!app.dom.imagePreviewSemantic) {
      return;
    }
    const statuses = ["loading", "complete", "partial", "failed", "none"];
    const rawStatus = loading
      ? "loading"
      : error
        ? "failed"
        : String(semantic?.status || "none");
    const status = rawStatus === "pending" ? "partial" : rawStatus;
    const normalizedStatus = statuses.includes(status) ? status : "none";
    if (app.state.imagePreviewState && !loading && !error) {
      app.state.imagePreviewState.semantic = semantic || { status: "none" };
    }
    statuses.forEach((item) => {
      app.dom.imagePreviewSemantic.classList.toggle(
        `semantic-${item}`,
        item === normalizedStatus,
      );
    });

    if (app.dom.imagePreviewSemanticFilename) {
      app.dom.imagePreviewSemanticFilename.textContent = app.state.imagePreviewState?.emoji || "";
    }
    const reviewStatus = String(
      semantic?.category_review_status || "unchecked",
    );
    const canEdit = !loading && !error && Boolean(semantic?.can_edit_semantic);
    const needsHumanReview = ["needs_review", "manual_rejected"].includes(
      reviewStatus,
    );
    const showCategoryReview =
      loading || (!error && normalizedStatus !== "none");
    app.dom.imagePreviewCategoryReview?.classList.toggle("hidden", !showCategoryReview);
    if (app.dom.imagePreviewCategoryTag) {
      app.dom.imagePreviewCategoryTag.textContent = loading
        ? "读取固定标签…"
        : String(
            semantic?.category_tag ||
              `category:${app.state.imagePreviewState?.category || ""}`,
          );
    }
    if (app.dom.imagePreviewCategoryReviewState) {
      app.dom.imagePreviewCategoryReviewState.textContent = loading
        ? "读取审核状态…"
        : app.library.semanticReviewLabel(reviewStatus);
    }
    const reviewReason = String(semantic?.category_review_reason || "").trim();
    if (app.dom.imagePreviewCategoryReviewReason) {
      app.dom.imagePreviewCategoryReviewReason.textContent = reviewReason;
      app.dom.imagePreviewCategoryReviewReason.classList.toggle(
        "hidden",
        loading || !reviewReason,
      );
    }
    const reclassificationStatus = String(
      semantic?.reclassification_status || "",
    );
    if (app.dom.imagePreviewReclassification) {
      const fromCategory = String(semantic?.reclassified_from_category || "");
      const toCategory = String(semantic?.reclassified_to_category || "");
      const reclassificationReason = String(
        semantic?.reclassification_reason || "",
      ).trim();
      app.dom.imagePreviewReclassification.textContent = reclassificationStatus
        ? `自动重分类：${fromCategory || "原分类"} → ${
            toCategory || "当前分类"
          }${reclassificationReason ? `；${reclassificationReason}` : ""}`
        : "";
      app.dom.imagePreviewReclassification.classList.toggle(
        "hidden",
        loading || !reclassificationStatus,
      );
    }
    app.dom.imagePreviewCategoryConfirmBtn?.classList.toggle(
      "hidden",
      !canEdit || !semantic?.can_confirm_category,
    );
    app.dom.imagePreviewReviewEditBtn?.classList.toggle(
      "hidden",
      !canEdit || !needsHumanReview,
    );
    const showReviewActions = Boolean(
      canEdit && (semantic?.can_confirm_category || needsHumanReview),
    );
    app.dom.imagePreviewReviewActions?.classList.toggle("hidden", !showReviewActions);
    const manualModified = Boolean(semantic?.manual_modified);
    if (app.dom.imagePreviewSourceState) {
      app.dom.imagePreviewSourceState.textContent = manualModified
        ? "人工修改"
        : "自动生成";
      app.dom.imagePreviewSourceState.classList.toggle("status-manual", manualModified);
    }
    const embeddingStatus = String(semantic?.embedding_status || "pending");
    if (app.dom.imagePreviewVectorState) {
      const vectorLabels = {
        done: "向量更新完成",
        failed: "向量更新失败",
        running: "向量更新中",
        pending: manualModified ? "语义已保存，向量待更新" : "向量等待更新",
        cleared: "向量等待更新",
      };
      app.dom.imagePreviewVectorState.textContent = loading
        ? "读取向量状态…"
        : vectorLabels[embeddingStatus] || "向量等待更新";
      app.dom.imagePreviewVectorState.classList.remove(
        "status-done",
        "status-failed",
        "status-pending",
      );
      app.dom.imagePreviewVectorState.classList.add(
        embeddingStatus === "done"
          ? "status-done"
          : embeddingStatus === "failed"
            ? "status-failed"
            : "status-pending",
      );
    }
    const duplicatePaths = Array.isArray(semantic?.same_content_paths)
      ? semantic.same_content_paths
      : [];
    if (app.dom.imagePreviewDuplicateNotice) {
      app.dom.imagePreviewDuplicateNotice.textContent = duplicatePaths.length
        ? `检测到另外 ${
            duplicatePaths.length
          } 张内容相同的图片。本次只修改当前路径，不会改动：${duplicatePaths.join(
            "、",
          )}`
        : "";
      app.dom.imagePreviewDuplicateNotice.classList.toggle(
        "hidden",
        loading || duplicatePaths.length === 0,
      );
    }
    const showBottomActions = Boolean(
      canEdit && (!needsHumanReview || semantic?.can_restore_auto),
    );
    app.dom.imagePreviewSemanticActions?.classList.toggle("hidden", !showBottomActions);
    app.dom.imagePreviewEditBtn?.classList.toggle(
      "hidden",
      !canEdit || needsHumanReview,
    );
    app.dom.imagePreviewRestoreAutoBtn?.classList.toggle(
      "hidden",
      !canEdit || !semantic?.can_restore_auto,
    );
    if (loading) {
      app.dom.imagePreviewSemanticState.textContent = "读取中";
      app.dom.imagePreviewSemanticCaption.textContent = "正在读取这张图片的语义信息…";
    } else if (error) {
      app.dom.imagePreviewSemanticState.textContent = "读取失败";
      app.dom.imagePreviewSemanticCaption.textContent = error;
    } else if (normalizedStatus === "complete") {
      app.dom.imagePreviewSemanticState.textContent = "已语义化";
      app.dom.imagePreviewSemanticCaption.textContent =
        String(semantic?.caption || "").trim() || "已有语义记录。";
    } else if (normalizedStatus === "failed") {
      app.dom.imagePreviewSemanticState.textContent = "语义化失败";
      app.dom.imagePreviewSemanticCaption.textContent =
        String(semantic?.error || "").trim() ||
        "这张图片上次语义化失败，可以前往语义化页面重试。";
    } else if (normalizedStatus === "partial") {
      app.dom.imagePreviewSemanticState.textContent = "等待完成";
      app.dom.imagePreviewSemanticCaption.textContent =
        "这张图片已进入语义化流程，但描述尚未完成。";
    } else {
      app.dom.imagePreviewSemanticState.textContent = "未语义化";
      app.dom.imagePreviewSemanticCaption.textContent =
        "这张图片还没有语义描述，可以前往语义化页面继续处理。";
    }

    const tags = Array.isArray(semantic?.tags) ? semantic.tags : [];
    if (app.dom.imagePreviewSemanticTags) {
      app.dom.imagePreviewSemanticTags.replaceChildren();
      tags.forEach((tag) => {
        const chip = document.createElement("span");
        chip.textContent = String(tag);
        app.dom.imagePreviewSemanticTags.appendChild(chip);
      });
    }
    app.dom.imagePreviewSemanticTagsWrap?.classList.toggle(
      "hidden",
      loading || tags.length === 0,
    );

    const visibleText = String(semantic?.visible_text || "").trim();
    if (app.dom.imagePreviewVisibleText) {
      app.dom.imagePreviewVisibleText.textContent = visibleText;
    }
    app.dom.imagePreviewVisibleTextWrap?.classList.toggle(
      "hidden",
      loading || !visibleText,
    );

    if (app.dom.imagePreviewSemanticIndex) {
      app.dom.imagePreviewSemanticIndex.textContent =
        normalizedStatus === "complete"
          ? embeddingStatus === "done"
            ? "语义向量已建立，可用于搜索。"
            : embeddingStatus === "failed"
              ? `语义已保存，但向量更新失败${
                  semantic?.embedding_error
                    ? `：${semantic.embedding_error}`
                    : "。"
                }`
              : "语义已保存，向量等待更新。"
          : "";
    }
  }
return { renderImageSemantic };
}
