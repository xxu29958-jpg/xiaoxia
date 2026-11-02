
// bindings: one page runtime owns shared state and DOM references.
export function createBindings(app) {
async function initialize5() {
document
    .getElementById("selection-dock-exit")
    .addEventListener("click", () => app.selection.setSelectionMode(false));
app.dom.moveUndoBtn.addEventListener("click", () => {
    if (app.state.lastMove)
      void app.move.moveEmojiItemsToCategory(
        app.state.lastMove.targetCategory,
        app.state.lastMove.items,
        true,
      );
  });
document
    .getElementById("move-result-dismiss")
    .addEventListener("click", () => {
      if (app.state.moveBusy) return;
      app.state.lastMove = null;
      app.dom.moveResult.classList.add("hidden");
    });
if (app.dom.clearAllBtn) {
    app.dom.clearAllBtn.addEventListener("click", app.mutations.clearAllEmojiFiles);
  }
if (app.dom.contextMenuDeleteBtn) {
    app.dom.contextMenuDeleteBtn.addEventListener("click", async () => {
      const menuItems = app.clipboard.dedupeEmojiItems(app.state.contextMenuState.items);
      app.contextMenu.closeBatchContextMenu();
      await app.mutations.deleteEmojiItems(menuItems, {
        useSelectionState:
          menuItems.length > 0 &&
          menuItems.every((item) => app.selection.isEmojiSelected(item.category, item.emoji)),
        confirmMode: "danger",
      });
    });
  }
if (app.dom.contextMenuMoveBtn) {
    app.dom.contextMenuMoveBtn.addEventListener("click", () => {
      const menuItems = app.clipboard.dedupeEmojiItems(app.state.contextMenuState.items);
      app.contextMenu.closeBatchContextMenu();
      app.move.openMoveTargetModal(menuItems);
    });
  }
if (app.dom.contextMenuCopyBtn) {
    app.dom.contextMenuCopyBtn.addEventListener("click", () => {
      const menuItems = app.clipboard.dedupeEmojiItems(app.state.contextMenuState.items);
      app.contextMenu.closeBatchContextMenu();
      app.selection.copyItemsToClipboard(menuItems);
    });
  }
if (app.dom.contextMenuPasteBtn) {
    app.dom.contextMenuPasteBtn.addEventListener("click", async () => {
      const targetCategory = app.state.contextMenuState.targetCategory;
      const clipboardItems = app.clipboard.getClipboardItems();
      app.contextMenu.closeBatchContextMenu();
      const confirmed = await app.dialogs.showConfirm({
        title: "粘贴表情包",
        description: `确认将剪贴板中的 ${clipboardItems.length} 个表情包粘贴到「${targetCategory}」？`,
        confirmLabel: "确认粘贴",
      });
      if (!confirmed) {
        return;
      }
      await app.move.copyEmojiItemsToCategory(targetCategory, clipboardItems);
    });
  }
if (app.dom.consoleToggleBtn) {
    app.dom.consoleToggleBtn.addEventListener("click", () => {
      app.layout.toggleConsolePanel();
    });
  }
if (app.dom.directoryToggleBtn) {
    app.dom.directoryToggleBtn.addEventListener("click", () => {
      app.layout.toggleDirectoryPanel();
    });
  }
if (app.dom.sidebarBackdrop) {
    app.dom.sidebarBackdrop.addEventListener("click", () => {
      app.layout.closeAllPanels();
      app.layout.updatePanelToggleState();
    });
  }
document.querySelectorAll(".panel-close-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      app.layout.closeAllPanels();
      app.layout.updatePanelToggleState();
    });
  });
app.dom.sidebarListElement = document.getElementById("sidebar-list");
if (app.dom.sidebarListElement) {
    app.dom.sidebarListElement.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link || !app.layout.isCompactViewport()) {
        return;
      }
      // 移动端：选中目录项后自动收起抽屉，让正文露出来
      app.layout.closeAllPanels();
      app.layout.updatePanelToggleState();
    });
  }
if (app.dom.dangerModalAcknowledge) {
    app.dom.dangerModalAcknowledge.addEventListener("change", () => {
      if (app.state.dangerConfirmStage === "ack") {
        if (!app.dom.dangerModalAcknowledge.checked) {
          app.dom.dangerModalConfirmBtn.disabled = true;
          app.dom.dangerModalConfirmBtn.textContent = "请先勾选上方选项";
          return;
        }
        app.danger.startDangerCountdown();
      }
    });
  }
if (app.dom.dangerModalCancelBtn) {
    app.dom.dangerModalCancelBtn.addEventListener("click", () => {
      app.danger.closeDangerConfirm(false);
    });
  }
if (app.dom.dangerModalConfirmBtn) {
    app.dom.dangerModalConfirmBtn.addEventListener("click", () => {
      if (app.state.dangerConfirmStage === "ack" && app.dom.dangerModalAcknowledge?.checked) {
        app.danger.startDangerCountdown();
        return;
      }
      if (app.state.dangerConfirmStage === "ready") {
        app.danger.closeDangerConfirm(true);
      }
    });
  }
if (app.dom.dangerModalRoot) {
    app.dom.dangerModalRoot.addEventListener("click", (event) => {
      if (event.target === app.dom.dangerModalRoot) {
        app.danger.closeDangerConfirm(false);
      }
    });
  }
if (app.dom.confirmModalCancelBtn) {
    app.dom.confirmModalCancelBtn.addEventListener("click", () => {
      app.dialogs.closeConfirm(false);
    });
  }
if (app.dom.confirmModalConfirmBtn) {
    app.dom.confirmModalConfirmBtn.addEventListener("click", () => {
      app.dialogs.closeConfirm(true);
    });
  }
if (app.dom.confirmModalRoot) {
    app.dom.confirmModalRoot.addEventListener("click", (event) => {
      if (event.target === app.dom.confirmModalRoot) {
        app.dialogs.closeConfirm(false);
      }
    });
  }
if (app.dom.categoryEditCancelBtn) {
    app.dom.categoryEditCancelBtn.addEventListener("click", () => {
      app.categories.closeCategoryEditModal();
    });
  }
if (app.dom.categoryEditSaveBtn) {
    app.dom.categoryEditSaveBtn.addEventListener("click", async () => {
      await app.categories.saveCategory();
    });
  }
if (app.dom.moveTargetCancelBtn) {
    app.dom.moveTargetCancelBtn.addEventListener("click", () => {
      app.move.closeMoveTargetModal();
    });
  }
if (app.dom.moveTargetModalRoot) {
    app.dom.moveTargetSearch.addEventListener("input", () => {
      const query = app.dom.moveTargetSearch.value.trim().toLocaleLowerCase();
      let visible = 0;
      app.dom.moveTargetList.querySelectorAll("button").forEach((button) => {
        button.hidden = !button.dataset.category
          .toLocaleLowerCase()
          .includes(query);
        if (!button.hidden) visible++;
      });
      document
        .getElementById("move-target-empty")
        .classList.toggle("hidden", visible > 0);
    });
    app.dom.moveTargetModalRoot.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const focusable = [
        ...app.dom.moveTargetModalRoot.querySelectorAll("input, button"),
      ].filter((element) => !element.hidden && !element.disabled);
      const index = focusable.indexOf(document.activeElement);
      event.preventDefault();
      focusable[
        (index + (event.shiftKey ? -1 : 1) + focusable.length) %
          focusable.length
      ]?.focus();
    });
    app.dom.moveTargetModalRoot.addEventListener("click", (event) => {
      if (event.target === app.dom.moveTargetModalRoot) {
        app.move.closeMoveTargetModal();
      }
    });
  }
if (app.dom.imagePreviewCloseBtn) {
    app.dom.imagePreviewCloseBtn.addEventListener("click", () => {
      app.imagePreview.closeImagePreview();
    });
  }
if (app.dom.imagePreviewOriginalBtn) {
    app.dom.imagePreviewOriginalBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      void app.imagePreview.showOriginalPreview();
    });
  }
if (app.dom.imagePreviewModalRoot) {
    app.dom.imagePreviewModalRoot.addEventListener("click", (event) => {
      if (
        event.target === app.dom.imagePreviewModalRoot ||
        event.target?.classList?.contains("image-preview-stage")
      ) {
        app.imagePreview.closeImagePreview();
      }
    });
  }
if (app.dom.categoryEditModalRoot) {
    app.dom.categoryEditModalRoot.addEventListener("click", (event) => {
      if (event.target === app.dom.categoryEditModalRoot) {
        app.categories.closeCategoryEditModal();
      }
    });
  }
[app.dom.categoryEditNameInput, app.dom.categoryEditDescInput].forEach((input) => {
    input?.addEventListener("keydown", async (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        await app.categories.saveCategory();
      }
    });
  });
document.addEventListener("pointermove", (event) => {
    if (
      app.state.longPressState.emojiItem &&
      typeof event.pointerId === "number" &&
      event.pointerId === app.state.longPressState.pointerId
    ) {
      const offsetX = event.clientX - app.state.longPressState.startX;
      const offsetY = event.clientY - app.state.longPressState.startY;
      const movedDistance = Math.hypot(offsetX, offsetY);
      if (app.state.longPressState.pointerType === "mouse") {
        if (movedDistance < app.state.MOUSE_DRAG_DISTANCE_PX) return;
        const sourceElement = app.state.longPressState.emojiItem;
        const items = app.drag.getDragItemsForEmoji(
          sourceElement.dataset.category,
          sourceElement.dataset.emoji,
        );
        sourceElement.dataset.suppressClick = "true";
        app.drag.armDragMode(items, {
          pointerId: event.pointerId,
          clientX: event.clientX,
          clientY: event.clientY,
          sourceElement,
        });
      } else {
        if (movedDistance > app.state.LONG_PRESS_CANCEL_DISTANCE_PX) app.drag.cancelLongPress();
        return;
      }
    }

    if (
      app.state.dragModeState.pointerId !== null &&
      typeof event.pointerId === "number" &&
      event.pointerId === app.state.dragModeState.pointerId
    ) {
      app.drag.updatePointerDrag(event);
      event.preventDefault();
    }
  });
app.state.handlePointerRelease = async (event) => {
    app.drag.finishLongPress(event);
    await app.drag.finishPointerDrag(event);
  };
document.addEventListener("pointerup", (event) => {
    void app.state.handlePointerRelease(event);
  });
document.addEventListener("pointercancel", (event) => {
    if (
      event.pointerId === app.state.longPressState.pointerId ||
      event.pointerId === app.state.dragModeState.pointerId
    )
      app.drag.clearDragMode();
  });
window.addEventListener("blur", () => app.drag.clearDragMode());
document.addEventListener("visibilitychange", () => {
    if (document.hidden) app.drag.clearDragMode();
  });
document.addEventListener(
    "touchmove",
    (event) => {
      if (app.state.dragModeState.pointerId !== null) {
        event.preventDefault();
      }
    },
    { passive: false },
  );
document.addEventListener("dragstart", (event) => {
    if (app.drag.hasActiveDragInteraction() || event.target?.closest?.(".emoji-item")) {
      event.preventDefault();
    }
  });
document.addEventListener("contextmenu", (event) => {
    if (
      event.target?.closest(".emoji-item") &&
      (event.pointerType === "touch" ||
        event.pointerType === "pen" ||
        event.target.closest(".emoji-item").dataset.suppressClick === "true")
    ) {
      event.preventDefault();
      return;
    }
    if (app.contextMenu.shouldOpenBatchContextMenu(event)) {
      event.preventDefault();
      app.contextMenu.openBatchContextMenu(event);
      return;
    }

    app.contextMenu.closeBatchContextMenu();

    if (app.drag.hasActiveDragInteraction()) {
      event.preventDefault();
    }
  });
document.addEventListener("click", (event) => {
    if (!app.dom.batchContextMenu || app.dom.batchContextMenu.classList.contains("hidden")) {
      return;
    }
    if (event.target.closest("#batch-context-menu")) {
      return;
    }
    app.contextMenu.closeBatchContextMenu();
  });
document.addEventListener(
    "scroll",
    () => {
      app.contextMenu.closeBatchContextMenu();
      if (app.state.longPressState.pointerType !== "mouse") app.drag.cancelLongPress();
    },
    true,
  );
document.addEventListener("selectstart", (event) => {
    if (
      app.drag.hasActiveDragInteraction() ||
      event.target?.closest?.(".emoji-item") ||
      event.target?.closest?.(".emoji-upload")
    ) {
      event.preventDefault();
    }
  });
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && app.drag.hasActiveDragInteraction()) {
      app.drag.clearDragMode();
      app.dialogs.showToast("已退出拖拽模式。", "info", "拖拽模式已关闭");
      return;
    }
    if (event.key === "Escape" && app.dom.batchContextMenu) {
      const isBatchContextMenuOpen =
        !app.dom.batchContextMenu.classList.contains("hidden");
      if (isBatchContextMenuOpen) {
        app.contextMenu.closeBatchContextMenu();
        return;
      }
    }
    if (event.key === "Escape" && app.layout.isCompactViewport()) {
      const isAnyPanelOpen = app.layout.isConsoleVisible() || app.layout.isDirectoryVisible();
      if (isAnyPanelOpen) {
        app.layout.closeAllPanels();
        app.layout.updatePanelToggleState();
        return;
      }
    }
    if (event.key === "Escape" && app.dom.moveTargetModalRoot) {
      const isMoveTargetOpen =
        !app.dom.moveTargetModalRoot.classList.contains("hidden");
      if (isMoveTargetOpen) {
        app.move.closeMoveTargetModal();
        return;
      }
    }
    if (event.key === "Escape" && app.dom.imagePreviewModalRoot) {
      const isPreviewOpen = !app.dom.imagePreviewModalRoot.classList.contains("hidden");
      if (isPreviewOpen) {
        app.imagePreview.closeImagePreview();
        return;
      }
    }
    if (event.key === "Escape" && app.dom.categoryEditModalRoot) {
      const isEditOpen = !app.dom.categoryEditModalRoot.classList.contains("hidden");
      if (isEditOpen) {
        app.categories.closeCategoryEditModal();
        return;
      }
    }
    if (event.key === "Escape" && app.dom.confirmModalRoot) {
      const isConfirmOpen = !app.dom.confirmModalRoot.classList.contains("hidden");
      if (isConfirmOpen) {
        app.dialogs.closeConfirm(false);
        return;
      }
    }
    if (event.key === "Escape" && app.dom.dangerModalRoot) {
      const isOpen = !app.dom.dangerModalRoot.classList.contains("hidden");
      if (isOpen) {
        app.danger.closeDangerConfirm(false);
      }
    }
  });
document
    .getElementById("add-category-btn")
    .addEventListener("click", function () {
      document.getElementById("add-category-form").style.display = "block";
      this.style.display = "none";
      document.getElementById("new-category-name").focus();
    });
document
    .getElementById("cancel-category-btn")
    .addEventListener("click", () => {
      document.getElementById("add-category-form").style.display = "none";
      document.getElementById("add-category-btn").style.display = "inline-flex";
      document.getElementById("add-category-btn").focus();
    });
document
    .getElementById("add-category-form")
    .addEventListener("keydown", (event) => {
      if (
        event.key === "Enter" &&
        event.target.matches("input") &&
        !event.isComposing
      ) {
        event.preventDefault();
        document.getElementById("save-category-btn").click();
      }
    });
document
    .getElementById("save-category-btn")
    .addEventListener("click", async function () {
      const categoryName = document
        .getElementById("new-category-name")
        .value.trim();
      const categoryDesc =
        document.getElementById("new-category-description").value.trim() ||
        "请添加描述";

      if (!categoryName) {
        app.dialogs.showToast("请输入类别名称后再保存。", "warning", "缺少类别名称");
        return;
      }

      const saveButton = this;
      app.widgets.setButtonBusy(saveButton, "保存中...");

      try {
        await app.api.apiPost("category/restore", {
          category: categoryName,
          description: categoryDesc,
        });

        document.getElementById("new-category-name").value = "";
        document.getElementById("new-category-description").value = "";
        document.getElementById("add-category-form").style.display = "none";
        document.getElementById("add-category-btn").style.display = "block";
        await app.api.refreshUi({ emojis: true, syncStatus: true });
        app.dialogs.showToast(`类别「${categoryName}」已添加。`, "success", "添加成功");
      } catch (error) {
        console.error("添加类别失败:", error);
        app.dialogs.showToast(error.message, "error", "添加失败");
      } finally {
        app.widgets.restoreButton(saveButton);
      }
    });
}

async function initialize8() {
app.dom.exportModeInputs.forEach((input) => {
    input.addEventListener("change", app.packTransfer.updateExportModeAppearance);
  });
app.dom.exportPackDownloadBtn?.addEventListener("click", () => {
    void app.packTransfer.downloadCurrentPack();
  });
app.dom.packImportFile?.addEventListener("change", (event) => {
    const file = event.target?.files?.[0];
    void app.packImport.stagePackImport(file);
  });
["dragenter", "dragover"].forEach((eventName) => {
    app.dom.packImportDropzone?.addEventListener(eventName, (event) => {
      event.preventDefault();
      app.dom.packImportDropzone.classList.add("dragover");
    });
  });
["dragleave", "drop"].forEach((eventName) => {
    app.dom.packImportDropzone?.addEventListener(eventName, (event) => {
      event.preventDefault();
      app.dom.packImportDropzone.classList.remove("dragover");
    });
  });
app.dom.packImportDropzone?.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    void app.packImport.stagePackImport(file);
  });
app.dom.packImportResetBtn?.addEventListener("click", () => {
    app.packImport.resetPackImportPreview();
  });
app.dom.packImportConfirmBtn?.addEventListener("click", () => {
    void app.packImport.confirmPackImport();
  });
app.dom.rebuildPackVectorsBtn?.addEventListener("click", () => {
    const packId = String(app.state.activeManagePackId || "").trim();
    const status =
      app.state.latestManagePackVectorStatusId === packId
        ? app.state.latestManagePackVectorStatus
        : null;
    if (!status) {
      void app.vectors.refreshManagePackVectorStatus(packId);
      return;
    }
    void app.vectors.confirmAndRebuildVector(packId, status, { manual: true });
  });
app.packTransfer.updateExportModeAppearance();
app.dom.librarySearchInput.addEventListener("input", () => {
    app.state.librarySearch = app.dom.librarySearchInput.value.trim().toLocaleLowerCase();
    app.library.displayCategories(app.state.latestEmojiData, app.state.latestTagDescriptions);
    app.library.updateSidebar(app.state.latestEmojiData, app.state.latestTagDescriptions);
  });
app.dom.libraryRetryBtn.addEventListener("click", () => {
    void app.library.fetchEmojis();
  });
await app.packCatalog.loadManagePackSwitcher();
await app.library.fetchEmojis();
await app.collection.loadCollectionReview();
if (
    new URLSearchParams(location.search).get("review_collection") === "1" &&
    !app.dom.collectionReview.open.hidden
  )
    app.dom.collectionReview.open.click();
window.setInterval(() => {
    if (
      !document.hidden &&
      !app.dom.collectionReview.dialog.open &&
      !app.dom.collectionReview.busy
    )
      void app.collection.loadCollectionReview();
  }, 30000);
app.dom.semanticReviewStats?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-review-filter]");
    if (!button) return;
    app.state.activeSemanticReviewFilter = String(button.dataset.reviewFilter || "all");
    app.library.updateSemanticReviewToolbar();
    app.library.displayCategories(app.state.latestEmojiData, app.state.latestTagDescriptions);
    app.library.updateSidebar(app.state.latestEmojiData, app.state.latestTagDescriptions);
  });
app.dom.imagePreviewCategoryConfirmBtn?.addEventListener("click", () => {
    void app.semanticEdit.confirmCurrentImageCategory();
  });
app.dom.imagePreviewEditBtn?.addEventListener("click", () => {
    app.semanticEdit.setImageSemanticEditing(true);
  });
app.dom.imagePreviewReviewEditBtn?.addEventListener("click", () => {
    app.semanticEdit.setImageSemanticEditing(true);
  });
app.dom.imagePreviewEditCancelBtn?.addEventListener("click", () => {
    app.semanticEdit.setImageSemanticEditing(false);
  });
app.dom.imagePreviewEditForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void app.semanticEdit.saveCurrentImageSemantic({ updateVector: false });
  });
app.dom.imagePreviewSaveVectorBtn?.addEventListener("click", () => {
    void app.semanticEdit.saveCurrentImageSemantic({ updateVector: true });
  });
app.dom.imagePreviewReviewRewriteBtn?.addEventListener("click", () => {
    void app.semanticEdit.requestImageSemanticRevision();
  });
app.dom.imagePreviewTargetCategory?.addEventListener("change", () => {
    app.semanticEdit.updateImageSemanticMoveChoice();
  });
app.dom.imagePreviewRestoreAutoBtn?.addEventListener("click", () => {
    void app.semanticEdit.restoreCurrentImageAutoSemantic();
  });
await app.vectors.maybeOfferVectorRebuild(app.state.activeManagePackId);
app.dom.managePackSelect?.addEventListener("change", () => {
    if (app.dom.managePackSelect.value === "__create__") {
      app.dom.managePackSelect.value = app.state.activeManagePackId;
      document.getElementById("create-pack-form").reset();
      document.getElementById("create-pack-error").textContent = "";
      document.getElementById("create-pack-dialog").showModal();
      document.querySelector("#create-pack-fields input").focus();
      return;
    }
    void app.packCatalog.switchManagePack();
  });
app.dom.createPackDialog = document.getElementById("create-pack-dialog");
app.dom.createPackFields = document.getElementById("create-pack-fields");
app.dom.createPackSubmit = document.getElementById("create-pack-submit");
app.dom.createPackCancel = document.getElementById("create-pack-cancel");
app.dom.createPackCancel.addEventListener("click", () => app.dom.createPackDialog.close());
app.dom.createPackDialog.addEventListener("cancel", (event) => {
    if (app.dom.createPackFields.disabled) event.preventDefault();
  });
document
    .getElementById("create-pack-form")
    .addEventListener("submit", async (event) => {
      event.preventDefault();
      if (app.dom.createPackFields.disabled) return;
      const data = new FormData(event.currentTarget);
      app.dom.createPackFields.disabled =
        app.dom.createPackSubmit.disabled =
        app.dom.createPackCancel.disabled =
          true;
      app.dom.createPackSubmit.textContent = "正在创建…";
      const errorLabel = document.getElementById("create-pack-error");
      errorLabel.textContent = "";
      try {
        const result = await app.api.apiPost("packs/create", {
          name: String(data.get("name") || "").trim(),
          description: data.get("description") || "",
        });
        app.dom.createPackDialog.close();
        app.selection.clearSelections();
        app.state.lastMove = null;
        app.dom.moveResult.classList.add("hidden");
        await app.packCatalog.loadManagePackSwitcher(result.pack_id);
        await app.api.refreshUi({ emojis: true });
        app.dialogs.showToast(`已创建 ${result.name}`, "success", "创建成功");
      } catch (error) {
        errorLabel.textContent = error?.message || String(error);
        app.dialogs.showToast(errorLabel.textContent, "error", "创建失败");
      } finally {
        app.dom.createPackFields.disabled =
          app.dom.createPackSubmit.disabled =
          app.dom.createPackCancel.disabled =
            false;
        app.dom.createPackSubmit.textContent = "创建并打开";
      }
    });
app.dom.retryManagePacksBtn?.addEventListener("click", async () => {
    app.dom.retryManagePacksBtn.disabled = true;
    try {
      await app.packCatalog.loadManagePackSwitcher(app.state.activeManagePackId);
      if (app.dom.managePackSelect.value) await app.library.fetchEmojis();
    } finally {
      app.dom.retryManagePacksBtn.disabled = false;
    }
  });
app.dom.deleteManagePackBtn?.addEventListener("click", () => {
    void app.packCatalog.deleteCurrentManagePack();
  });
app.state.initialStatusTimerId = window.setTimeout(() => {
    app.state.initialStatusTimerId = null;
    void app.sync.checkSyncStatus(false);
    void app.sync.checkImgHostSyncStatus(false);
    void app.api.apiGet("img_host/sync/task_status")
      .then(async (status) => {
        if (!status.running) return;
        const selectedPack =
          app.state.activeManagePackId || app.dom.managePackSelect?.value || "";
        if (
          status.managed_pack_id &&
          selectedPack &&
          status.managed_pack_id !== selectedPack
        )
          return;
        try {
          await app.sync.waitForSyncCompletion("图床同步");
          await app.api.refreshUi({
            emojis: true,
            syncStatus: true,
            imgHostStatus: true,
          });
          app.sync.setImgHostSyncProgress("图床同步已完成。", "success");
        } catch (error) {
          app.sync.setImgHostSyncProgress(error.message, "error");
        }
      })
      .catch((error) => console.warn("无法恢复图片同步进度：", error));
  }, 180);
}
return { initialize5, initialize8 };
}
