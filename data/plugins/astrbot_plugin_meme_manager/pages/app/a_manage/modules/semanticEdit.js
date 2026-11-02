
// semanticEdit: one page runtime owns shared state and DOM references.
export function createSemanticEdit(app) {
function setImageSemanticEditing(editing) {
    if (!app.dom.imagePreviewSemantic || !app.dom.imagePreviewEditForm) return;
    app.dom.imagePreviewSemantic.classList.toggle("editing", editing);
    app.dom.imagePreviewEditForm.classList.toggle("hidden", !editing);
    if (!editing) return;
    const semantic = app.state.imagePreviewState?.semantic || {};
    if (app.dom.imagePreviewCaptionInput) {
      app.dom.imagePreviewCaptionInput.value = String(semantic.caption || "");
    }
    if (app.dom.imagePreviewTagsInput) {
      const tags = Array.isArray(semantic.editable_tags)
        ? semantic.editable_tags
        : [];
      app.dom.imagePreviewTagsInput.value = tags.join("，");
    }
    if (app.dom.imagePreviewVisibleTextInput) {
      app.dom.imagePreviewVisibleTextInput.value = String(semantic.visible_text || "");
    }
    if (app.dom.imagePreviewCategoryDecision) {
      app.dom.imagePreviewCategoryDecision.value =
        semantic.category_review_status === "manual_confirmed"
          ? "match"
          : semantic.category_review_status === "manual_rejected"
            ? "mismatch"
            : semantic.status === "none"
              ? "match"
              : "keep";
    }
    if (app.dom.imagePreviewFixedTags) {
      app.dom.imagePreviewFixedTags.replaceChildren();
      const fixedTags = Array.isArray(semantic.fixed_category_tags)
        ? semantic.fixed_category_tags
        : [];
      fixedTags.forEach((tag) => {
        const chip = document.createElement("span");
        chip.textContent = String(tag);
        app.dom.imagePreviewFixedTags.appendChild(chip);
      });
    }
    if (app.dom.imagePreviewReviewInstruction) {
      app.dom.imagePreviewReviewInstruction.value = "";
    }
    if (app.dom.imagePreviewReviewRewriteStatus) {
      app.dom.imagePreviewReviewRewriteStatus.textContent =
        "模型会重写语义并自动选择分类；只生成候选，不会直接保存或移动。";
      app.dom.imagePreviewReviewRewriteStatus.classList.remove("has-proposal");
    }
    if (app.dom.imagePreviewTargetCategory) {
      app.dom.imagePreviewTargetCategory.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "保持当前分类";
      app.dom.imagePreviewTargetCategory.appendChild(placeholder);
      const currentCategory = String(app.state.imagePreviewState?.category || "");
      const categories = Array.from(
        new Set([
          ...Object.keys(app.state.latestEmojiData || {}),
          ...Object.keys(app.state.latestTagDescriptions || {}),
        ]),
      )
        .filter((category) => category && category !== currentCategory)
        .sort((left, right) => left.localeCompare(right, "zh-CN"));
      categories.forEach((category) => {
        const option = document.createElement("option");
        option.value = category;
        option.textContent = category;
        app.dom.imagePreviewTargetCategory.appendChild(option);
      });
      updateImageSemanticMoveChoice();
    }
    const duplicateCount = Number(semantic.same_content_count || 0);
    if (app.dom.imagePreviewEditScope) {
      app.dom.imagePreviewEditScope.textContent = duplicateCount
        ? `只修改当前路径。检测到另外 ${duplicateCount} 张同内容图片，它们不会被一并修改。`
        : "只修改当前图片路径和当前分类，不会批量共享修改。";
    }
    window.setTimeout(() => app.dom.imagePreviewCaptionInput?.focus(), 0);
  }

function parseManualSemanticTags(value) {
    return Array.from(
      new Set(
        String(value || "")
          .split(/[，,\n]+/)
          .map((tag) => tag.trim())
          .filter(Boolean),
      ),
    );
  }

function imageSemanticSnapshotPayload() {
    const state = app.state.imagePreviewState;
    const semantic = state?.semantic || {};
    if (
      !state ||
      String(app.state.activeManagePackId || "") !== String(state.packId || "")
    ) {
      throw new Error("当前图包已经切换，请重新打开图片后再编辑。");
    }
    if (!semantic.content_sha256 || !semantic.entry_id) {
      throw new Error("图片编辑快照无效，请重新打开图片后再操作。");
    }
    return {
      expected_pack_id: state.packId,
      category: state.category,
      filename: state.emoji,
      expected_content_sha256: String(semantic.content_sha256),
      expected_entry_id: String(semantic.entry_id),
    };
  }

function imageSemanticEditPayload() {
    const caption = String(app.dom.imagePreviewCaptionInput?.value || "").trim();
    if (!caption) {
      throw new Error("图片含义不能为空。");
    }
    return {
      ...imageSemanticSnapshotPayload(),
      caption,
      tags: parseManualSemanticTags(app.dom.imagePreviewTagsInput?.value),
      visible_text: String(app.dom.imagePreviewVisibleTextInput?.value || "").trim(),
      category_decision: String(app.dom.imagePreviewCategoryDecision?.value || "keep"),
      target_category: String(app.dom.imagePreviewTargetCategory?.value || "").trim(),
    };
  }

function updateImageSemanticMoveChoice() {
    const targetCategory = String(
      app.dom.imagePreviewTargetCategory?.value || "",
    ).trim();
    const saving = app.dom.imagePreviewEditForm?.dataset.saving === "true";
    if (app.dom.imagePreviewSaveBtn && !saving) {
      app.dom.imagePreviewSaveBtn.disabled = Boolean(targetCategory);
      app.dom.imagePreviewSaveBtn.title = targetCategory
        ? "选择新分类后，需要同时更新该图向量"
        : "只保存语义，向量等待后续更新";
    }
    if (app.dom.imagePreviewSaveVectorBtn && !saving) {
      app.dom.imagePreviewSaveVectorBtn.disabled = false;
      app.dom.imagePreviewSaveVectorBtn.textContent = targetCategory
        ? "保存、移动并更新该图向量"
        : "保存并更新该图向量";
    }
  }

function setImageSemanticSaving(
    saving,
    activeButton = null,
    busyLabel = "保存中...",
  ) {
    if (app.dom.imagePreviewEditForm) {
      app.dom.imagePreviewEditForm.dataset.saving = saving ? "true" : "false";
    }
    [
      app.dom.imagePreviewEditCancelBtn,
      app.dom.imagePreviewSaveBtn,
      app.dom.imagePreviewSaveVectorBtn,
      app.dom.imagePreviewEditBtn,
      app.dom.imagePreviewReviewEditBtn,
      app.dom.imagePreviewRestoreAutoBtn,
      app.dom.imagePreviewCategoryConfirmBtn,
      app.dom.imagePreviewReviewRewriteBtn,
    ].forEach((button) => {
      if (button) button.disabled = saving;
    });
    [
      app.dom.imagePreviewCaptionInput,
      app.dom.imagePreviewTagsInput,
      app.dom.imagePreviewVisibleTextInput,
      app.dom.imagePreviewCategoryDecision,
      app.dom.imagePreviewReviewInstruction,
      app.dom.imagePreviewTargetCategory,
    ].forEach((field) => {
      if (field) field.disabled = saving;
    });
    if (saving && activeButton) {
      app.widgets.setButtonBusy(activeButton, busyLabel);
    } else if (!saving && activeButton) {
      app.widgets.restoreButton(activeButton);
    }
    if (!saving) {
      updateImageSemanticMoveChoice();
    }
  }

async function requestImageSemanticRevision() {
    const previewState = app.state.imagePreviewState;
    const reviewInstruction = String(
      app.dom.imagePreviewReviewInstruction?.value || "",
    ).trim();
    if (!reviewInstruction) {
      app.dialogs.showToast(
        "请先写明哪里判断错了，以及希望模型怎样修改。",
        "warning",
        "缺少复审意见",
      );
      app.dom.imagePreviewReviewInstruction?.focus();
      return;
    }
    try {
      const payload = {
        ...imageSemanticSnapshotPayload(),
        review_instruction: reviewInstruction,
      };
      setImageSemanticSaving(
        true,
        app.dom.imagePreviewReviewRewriteBtn,
        "视觉模型分析中...",
      );
      const result = await app.api.apiPost("semantic/propose_image_revision", payload);
      if (app.state.imagePreviewState !== previewState) return;
      const proposal = result?.proposal || {};
      if (app.dom.imagePreviewCaptionInput) {
        app.dom.imagePreviewCaptionInput.value = String(proposal.caption || "");
      }
      if (app.dom.imagePreviewTagsInput) {
        app.dom.imagePreviewTagsInput.value = Array.isArray(proposal.tags)
          ? proposal.tags.join("，")
          : "";
      }
      if (app.dom.imagePreviewVisibleTextInput) {
        app.dom.imagePreviewVisibleTextInput.value = String(
          proposal.visible_text || "",
        );
      }
      const categoryFit = String(proposal.category_fit || "uncertain");
      const classificationAction = String(
        proposal.classification_action || "manual_required",
      );
      if (app.dom.imagePreviewCategoryDecision) {
        if (
          classificationAction === "keep_current" &&
          categoryFit === "match"
        ) {
          app.dom.imagePreviewCategoryDecision.value = "match";
        } else if (
          ["return_original", "move_to_other"].includes(classificationAction) ||
          categoryFit === "conflict"
        ) {
          app.dom.imagePreviewCategoryDecision.value = "mismatch";
        } else {
          app.dom.imagePreviewCategoryDecision.value = "keep";
        }
      }
      const currentCategory = String(
        proposal.current_category || previewState?.category || "",
      ).trim();
      const originalCategory = String(proposal.original_category || "").trim();
      const selectedCategory = String(proposal.selected_category || "").trim();
      let targetOptionAvailable = true;
      if (app.dom.imagePreviewTargetCategory) {
        const selectableTarget =
          selectedCategory && selectedCategory !== currentCategory
            ? selectedCategory
            : "";
        const hasTargetOption = Array.from(
          app.dom.imagePreviewTargetCategory.options,
        ).some((option) => option.value === selectableTarget);
        targetOptionAvailable = !selectableTarget || hasTargetOption;
        app.dom.imagePreviewTargetCategory.value = hasTargetOption
          ? selectableTarget
          : "";
        updateImageSemanticMoveChoice();
      }
      if (app.dom.imagePreviewReviewRewriteStatus) {
        const reviewReason = String(
          proposal.category_review_reason || "",
        ).trim();
        let categoryChoiceText = "分类建议：模型未能确定，请手动选择。";
        if (!targetOptionAvailable) {
          categoryChoiceText = `模型建议改到“${selectedCategory}”，但当前分类列表已变化，请刷新页面后重新检查。`;
        } else if (classificationAction === "keep_current") {
          categoryChoiceText = `分类建议：保持当前分类“${currentCategory}”。`;
        } else if (classificationAction === "return_original") {
          categoryChoiceText = `分类建议：回到原分类“${
            originalCategory || selectedCategory
          }”。`;
        } else if (classificationAction === "move_to_other") {
          categoryChoiceText = `分类建议：改到“${selectedCategory}”。`;
        }
        app.dom.imagePreviewReviewRewriteStatus.textContent = [
          "视觉模型候选已填入，尚未保存。",
          categoryChoiceText,
          reviewReason ? `分类判断：${reviewReason}` : "",
        ]
          .filter(Boolean)
          .join(" ");
        app.dom.imagePreviewReviewRewriteStatus.classList.add("has-proposal");
      }
      app.dialogs.showToast(
        result?.message || "视觉模型候选已填入，请检查后再保存。",
        "success",
        "候选已生成",
        4800,
      );
    } catch (error) {
      app.dialogs.showToast(error?.message || String(error), "error", "重写失败", 4800);
    } finally {
      setImageSemanticSaving(false, app.dom.imagePreviewReviewRewriteBtn);
    }
  }

async function saveCurrentImageSemantic({ updateVector = false } = {}) {
    const activeButton = updateVector
      ? app.dom.imagePreviewSaveVectorBtn
      : app.dom.imagePreviewSaveBtn;
    const previewState = app.state.imagePreviewState;
    try {
      const payload = imageSemanticEditPayload();
      if (payload.target_category && !updateVector) {
        throw new Error("选择新分类后，请点击“保存、移动并更新该图向量”。");
      }
      setImageSemanticSaving(
        true,
        activeButton,
        payload.target_category ? "保存、移动并更新中..." : "保存中...",
      );
      const endpoint = updateVector
        ? "semantic/save_image_and_vector"
        : "semantic/save_image";
      const result = await app.api.apiPost(endpoint, payload);
      if (app.state.imagePreviewState === previewState) {
        if (result?.moved) {
          previewState.category = String(
            result?.category ||
              result?.target_category ||
              previewState.category,
          );
          previewState.emoji = String(result?.filename || previewState.emoji);
          if (app.dom.imagePreviewImg) {
            app.dom.imagePreviewImg.alt = `表情包预览：${previewState.emoji}`;
          }
        }
        app.semanticPreview.renderImageSemantic(result?.semantic || {});
        setImageSemanticEditing(false);
      }
      await app.library.fetchEmojis();
      const vectorStatus = String(result?.vector_update?.status || "pending");
      const toastType =
        updateVector && vectorStatus !== "done" ? "warning" : "success";
      app.dialogs.showToast(
        result?.message || "人工语义已保存，向量等待更新。",
        toastType,
        result?.moved
          ? "保存、移动与向量更新"
          : updateVector
            ? "保存与向量更新"
            : "保存成功",
      );
    } catch (error) {
      app.dialogs.showToast(error?.message || String(error), "error", "保存失败");
    } finally {
      setImageSemanticSaving(false, activeButton);
    }
  }

async function restoreCurrentImageAutoSemantic() {
    const state = app.state.imagePreviewState;
    const semantic = state?.semantic || {};
    if (!state || !semantic.can_restore_auto) return;
    const confirmed = await app.dialogs.showConfirm({
      title: "确认恢复自动生成",
      description:
        "这会放弃当前图片的人工描述、普通标签和可见文字。其他同内容图片不会受影响。确认继续吗？",
      confirmLabel: "放弃人工修改",
      confirmClassName: "danger",
    });
    if (!confirmed) return;
    try {
      if (String(app.state.activeManagePackId || "") !== String(state.packId || "")) {
        throw new Error("当前图包已经切换，请重新打开图片后再操作。");
      }
      setImageSemanticSaving(true, app.dom.imagePreviewRestoreAutoBtn);
      const result = await app.api.apiPost("semantic/restore_image_auto", {
        expected_pack_id: state.packId,
        category: state.category,
        filename: state.emoji,
        expected_content_sha256: String(semantic.content_sha256 || ""),
        expected_entry_id: String(semantic.entry_id || ""),
      });
      if (app.state.imagePreviewState === state) {
        app.semanticPreview.renderImageSemantic(result?.semantic || {});
        setImageSemanticEditing(false);
      }
      await app.library.fetchEmojis();
      app.dialogs.showToast(result?.message || "已恢复自动生成状态。", "success");
    } catch (error) {
      app.dialogs.showToast(error?.message || String(error), "error", "恢复失败");
    } finally {
      setImageSemanticSaving(false, app.dom.imagePreviewRestoreAutoBtn);
    }
  }

async function confirmCurrentImageCategory() {
    if (!app.state.imagePreviewState || !app.dom.imagePreviewCategoryConfirmBtn) return;
    const previewState = app.state.imagePreviewState;
    app.widgets.setButtonBusy(app.dom.imagePreviewCategoryConfirmBtn, "保存中...");
    try {
      const result = await app.api.apiPost("semantic/confirm_category", {
        expected_pack_id: previewState.packId,
        category: previewState.category,
        filename: previewState.emoji,
        expected_content_sha256: String(
          previewState.semantic?.content_sha256 || "",
        ),
        expected_entry_id: String(previewState.semantic?.entry_id || ""),
      });
      if (app.state.imagePreviewState === previewState) {
        app.semanticPreview.renderImageSemantic(result?.semantic || {});
      }
      await app.library.fetchEmojis();
      app.dialogs.showToast("已保存人工确认；图片或分类变化后会自动失效。", "success");
    } catch (error) {
      app.dialogs.showToast(error?.message || String(error), "error", "确认失败");
    } finally {
      app.widgets.restoreButton(app.dom.imagePreviewCategoryConfirmBtn);
    }
  }
return { setImageSemanticEditing, parseManualSemanticTags, imageSemanticSnapshotPayload, imageSemanticEditPayload, updateImageSemanticMoveChoice, setImageSemanticSaving, requestImageSemanticRevision, saveCurrentImageSemantic, restoreCurrentImageAutoSemantic, confirmCurrentImageCategory };
}
