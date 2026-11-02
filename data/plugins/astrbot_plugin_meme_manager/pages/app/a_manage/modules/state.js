import { setupPackTransfer } from "../pack-transfer.js";

// state: one page runtime owns shared state and DOM references.
export function createState(app) {
async function initialize2() {
app.state.selectionState = {
    enabled: false,
    items: new Map(),
  };
app.state.latestEmojiData = {};
app.state.latestTagDescriptions = {};
app.state.semanticReviewByPath = new Map();
app.state.semanticReviewStatistics = {};
app.state.semanticReviewAvailable = false;
app.state.activeSemanticReviewFilter = "all";
app.state.librarySearch = "";
app.dom.librarySearchInput = document.getElementById("library-search-input");
app.dom.libraryLoadState = document.getElementById("library-load-state");
app.dom.libraryLoadMessage = document.getElementById("library-load-message");
app.dom.libraryRetryBtn = document.getElementById("library-retry-btn");
app.state.libraryLoadSequence = 0;
app.state.previewObserver = null;
app.state.dangerConfirmResolver = null;
app.state.dangerConfirmStage = "ack";
app.state.dangerConfirmTimer = null;
app.state.dangerConfirmConfig = null;
app.dom.clearAllBtn = document.getElementById("clear-all-btn");
app.dom.toastContainer = document.getElementById("toast-container");
app.dom.batchContextMenu = document.getElementById("batch-context-menu");
app.dom.batchContextMenuTitle = document.getElementById(
    "batch-context-menu-title",
  );
app.dom.batchContextMenuSubtitle = document.getElementById(
    "batch-context-menu-subtitle",
  );
app.dom.contextMenuDeleteBtn = document.getElementById(
    "context-menu-delete-btn",
  );
app.dom.contextMenuMoveBtn = document.getElementById("context-menu-move-btn");
app.dom.contextMenuCopyBtn = document.getElementById("context-menu-copy-btn");
app.dom.contextMenuPasteBtn = document.getElementById("context-menu-paste-btn");
app.dom.consoleToggleBtn = document.getElementById("console-toggle-btn");
app.dom.directoryToggleBtn = document.getElementById("directory-toggle-btn");
app.dom.sidebarBackdrop = document.getElementById("sidebar-backdrop");
app.dom.leftPanel = document.getElementById("app-sidebar-panel");
app.dom.directoryPanel = document.getElementById("app-directory-panel");
app.dom.dragHud = document.getElementById("drag-hud");
app.dom.dragHudLabel = document.getElementById("drag-hud-label");
app.dom.dragHudCaption = document.getElementById("drag-hud-caption");
app.dom.imagePreviewModalRoot = document.getElementById("image-preview-modal");
app.dom.imagePreviewImg = document.getElementById("image-preview-img");
app.dom.imagePreviewLoading = document.getElementById("image-preview-loading");
app.dom.imagePreviewOriginalBtn = document.getElementById(
    "image-preview-original-btn",
  );
app.dom.imagePreviewCloseBtn = document.getElementById(
    "image-preview-close-btn",
  );
app.dom.imagePreviewSemantic = document.getElementById(
    "image-preview-semantic",
  );
app.dom.imagePreviewSemanticState = document.getElementById(
    "image-preview-semantic-state",
  );
app.dom.imagePreviewSemanticFilename = document.getElementById(
    "image-preview-semantic-filename",
  );
app.dom.imagePreviewSemanticCaption = document.getElementById(
    "image-preview-semantic-caption",
  );
app.dom.imagePreviewSemanticTagsWrap = document.getElementById(
    "image-preview-semantic-tags-wrap",
  );
app.dom.imagePreviewSemanticTags = document.getElementById(
    "image-preview-semantic-tags",
  );
app.dom.imagePreviewVisibleTextWrap = document.getElementById(
    "image-preview-visible-text-wrap",
  );
app.dom.imagePreviewVisibleText = document.getElementById(
    "image-preview-visible-text",
  );
app.dom.imagePreviewSemanticIndex = document.getElementById(
    "image-preview-semantic-index",
  );
app.dom.imagePreviewCategoryTag = document.getElementById(
    "image-preview-category-tag",
  );
app.dom.imagePreviewCategoryReview = document.getElementById(
    "image-preview-category-review",
  );
app.dom.imagePreviewCategoryReviewState = document.getElementById(
    "image-preview-category-review-state",
  );
app.dom.imagePreviewCategoryReviewReason = document.getElementById(
    "image-preview-category-review-reason",
  );
app.dom.imagePreviewReclassification = document.getElementById(
    "image-preview-reclassification",
  );
app.dom.imagePreviewCategoryConfirmBtn = document.getElementById(
    "image-preview-category-confirm-btn",
  );
app.dom.imagePreviewReviewActions = document.getElementById(
    "image-preview-review-actions",
  );
app.dom.imagePreviewReviewEditBtn = document.getElementById(
    "image-preview-review-edit-btn",
  );
app.dom.imagePreviewSourceState = document.getElementById(
    "image-preview-source-state",
  );
app.dom.imagePreviewVectorState = document.getElementById(
    "image-preview-vector-state",
  );
app.dom.imagePreviewDuplicateNotice = document.getElementById(
    "image-preview-duplicate-notice",
  );
app.dom.imagePreviewSemanticActions = document.getElementById(
    "image-preview-semantic-actions",
  );
app.dom.imagePreviewEditBtn = document.getElementById("image-preview-edit-btn");
app.dom.imagePreviewRestoreAutoBtn = document.getElementById(
    "image-preview-restore-auto-btn",
  );
app.dom.imagePreviewEditForm = document.getElementById(
    "image-preview-edit-form",
  );
app.dom.imagePreviewFixedTags = document.getElementById(
    "image-preview-fixed-tags",
  );
app.dom.imagePreviewReviewInstruction = document.getElementById(
    "image-preview-review-instruction",
  );
app.dom.imagePreviewReviewRewriteBtn = document.getElementById(
    "image-preview-review-rewrite-btn",
  );
app.dom.imagePreviewReviewRewriteStatus = document.getElementById(
    "image-preview-review-rewrite-status",
  );
app.dom.imagePreviewTargetCategory = document.getElementById(
    "image-preview-target-category",
  );
app.dom.imagePreviewCaptionInput = document.getElementById(
    "image-preview-caption-input",
  );
app.dom.imagePreviewTagsInput = document.getElementById(
    "image-preview-tags-input",
  );
app.dom.imagePreviewVisibleTextInput = document.getElementById(
    "image-preview-visible-text-input",
  );
app.dom.imagePreviewCategoryDecision = document.getElementById(
    "image-preview-category-decision",
  );
app.dom.imagePreviewEditScope = document.getElementById(
    "image-preview-edit-scope",
  );
app.dom.imagePreviewEditCancelBtn = document.getElementById(
    "image-preview-edit-cancel-btn",
  );
app.dom.imagePreviewSaveBtn = document.getElementById("image-preview-save-btn");
app.dom.imagePreviewSaveVectorBtn = document.getElementById(
    "image-preview-save-vector-btn",
  );
app.dom.semanticReviewStats = document.getElementById("semantic-review-stats");
app.dom.semanticReviewToolbar = document.getElementById(
    "semantic-review-toolbar",
  );
app.dom.moveTargetModalRoot = document.getElementById("move-target-modal");
app.dom.moveTargetModalTitle = document.getElementById(
    "move-target-modal-title",
  );
app.dom.moveTargetModalDescription = document.getElementById(
    "move-target-modal-description",
  );
app.dom.moveTargetList = document.getElementById("move-target-list");
app.dom.moveTargetSearch = document.getElementById("move-target-search");
app.state.moveTargetRestoreFocus = null;
app.dom.moveTargetCancelBtn = document.getElementById("move-target-cancel-btn");
app.dom.categoryEditModalRoot = document.getElementById("category-edit-modal");
app.dom.categoryEditModalTitle = document.getElementById(
    "category-edit-modal-title",
  );
app.dom.categoryEditModalDescription = document.getElementById(
    "category-edit-modal-description",
  );
app.dom.categoryEditNameInput = document.getElementById(
    "category-edit-name-input",
  );
app.dom.categoryEditDescInput = document.getElementById(
    "category-edit-desc-input",
  );
app.dom.categoryEditCancelBtn = document.getElementById(
    "category-edit-cancel-btn",
  );
app.dom.categoryEditSaveBtn = document.getElementById("category-edit-save-btn");
app.dom.confirmModalRoot = document.getElementById("confirm-modal");
app.dom.confirmModalTitle = document.getElementById("confirm-modal-title");
app.dom.confirmModalDescription = document.getElementById(
    "confirm-modal-description",
  );
app.dom.confirmModalCancelBtn = document.getElementById(
    "confirm-modal-cancel-btn",
  );
app.dom.confirmModalConfirmBtn = document.getElementById(
    "confirm-modal-confirm-btn",
  );
app.dom.dangerModalRoot = document.getElementById("danger-confirm-modal");
app.dom.dangerModalTitle = document.getElementById("danger-modal-title");
app.dom.dangerModalDescription = document.getElementById(
    "danger-modal-description",
  );
app.dom.dangerModalStageText = document.getElementById(
    "danger-modal-stage-text",
  );
app.dom.dangerModalAcknowledge = document.getElementById("danger-modal-ack");
app.dom.dangerModalCancelBtn = document.getElementById(
    "danger-modal-cancel-btn",
  );
app.dom.dangerModalConfirmBtn = document.getElementById(
    "danger-modal-confirm-btn",
  );
app.dom.imgHostSyncProgress = document.getElementById("img-host-sync-progress");
app.dom.imgHostSyncProgressText = document.getElementById(
    "img-host-sync-progress-text",
  );
app.dom.managePackSelect = document.getElementById("manage-pack-select");
app.dom.packSemanticStatus = document.getElementById("pack-semantic-status");
app.dom.packSemanticStatusText = document.getElementById(
    "pack-semantic-status-text",
  );
app.dom.packVectorStatus = document.getElementById("pack-vector-status");
app.dom.packVectorStatusText = document.getElementById(
    "pack-vector-status-text",
  );
app.dom.rebuildPackVectorsBtn = document.getElementById(
    "rebuild-pack-vectors-btn",
  );
app.dom.retryManagePacksBtn = document.getElementById("retry-manage-packs-btn");
app.dom.deleteManagePackBtn = document.getElementById("delete-manage-pack-btn");
app.dom.transferCurrentPack = document.getElementById("transfer-current-pack");
app.dom.exportModeInputs = Array.from(
    document.querySelectorAll('input[name="export-mode"]'),
  );
app.dom.exportModeBackup = document.getElementById("export-mode-backup");
app.dom.vectorBackupHint = document.getElementById("vector-backup-hint");
app.dom.exportPackDownloadBtn = document.getElementById(
    "export-pack-download-btn",
  );
app.dom.exportPackResult = document.getElementById("export-pack-result");
app.dom.packImportDropzone = document.getElementById("pack-import-dropzone");
app.dom.packImportFile = document.getElementById("pack-import-file");
app.dom.packImportFileLabel = document.getElementById("pack-import-file-label");
app.dom.packImportPreview = document.getElementById("pack-import-preview");
app.dom.packImportPreviewName = document.getElementById(
    "pack-import-preview-name",
  );
app.dom.packImportPreviewFormat = document.getElementById(
    "pack-import-preview-format",
  );
app.dom.packImportImageCount = document.getElementById(
    "pack-import-image-count",
  );
app.dom.packImportCategoryCount = document.getElementById(
    "pack-import-category-count",
  );
app.dom.packImportSemanticCount = document.getElementById(
    "pack-import-semantic-count",
  );
app.dom.packImportVectorState = document.getElementById(
    "pack-import-vector-state",
  );
app.dom.packImportWarning = document.getElementById("pack-import-warning");
app.dom.packImportSetDefault = document.getElementById(
    "pack-import-set-default",
  );
app.dom.packImportOverwrite = document.getElementById("pack-import-overwrite");
app.dom.packImportOverwriteManual = document.getElementById(
    "pack-import-overwrite-manual",
  );
app.dom.packImportResetBtn = document.getElementById("pack-import-reset-btn");
app.dom.packImportConfirmBtn = document.getElementById(
    "pack-import-confirm-btn",
  );
app.dom.packImportResult = document.getElementById("pack-import-result");
app.state.defaultManagePackId = "";
app.state.activeManagePackId = "";
app.state.managePacksById = new Map();
app.state.pendingPackImportToken = "";
app.state.exportCapabilityRequestId = 0;
app.state.managePackVectorStatusRequestId = 0;
app.state.latestManagePackVectorStatus = null;
app.state.latestManagePackVectorStatusId = "";
app.state.confirmResolver = null;
app.state.confirmRestoreFocusElement = null;
app.state.MOBILE_LAYOUT_MEDIA = "(max-width: 960px)";
app.state.DRAG_HUD_OFFSET_X = 18;
app.state.DRAG_HUD_OFFSET_Y = 88;
app.state.LONG_PRESS_DURATION_MS = 500;
app.state.LONG_PRESS_TICK_MS = 60;
app.state.LONG_PRESS_CANCEL_DISTANCE_PX = 10;
app.state.MOUSE_DRAG_DISTANCE_PX = 6;
app.state.moveBusy = false;
app.state.lastMove = null;
app.dom.dragTargetTray = document.getElementById("drag-target-tray");
app.dom.dragTargetList = document.getElementById("drag-target-list");
app.dom.dragHudPreview = document.getElementById("drag-hud-preview");
app.dom.selectionDock = document.getElementById("selection-dock");
app.dom.selectionDockSummary = document.getElementById(
    "selection-dock-summary",
  );
app.dom.selectionDockMove = document.getElementById("selection-dock-move");
app.dom.selectionDockDelete = document.getElementById("selection-dock-delete");
app.dom.selectionDockPack = document.getElementById("selection-dock-pack");
app.dom.moveResult = document.getElementById("move-result");
app.dom.moveResultText = document.getElementById("move-result-text");
app.dom.moveUndoBtn = document.getElementById("move-undo-btn");
app.state.transferPackSelectDisabled = false;
app.state.packTransfer = setupPackTransfer({
    apiGet: app.api.apiGet,
    apiPost: app.api.apiPost,
    onBusy(busy) {
      app.state.moveBusy = busy;
      if (busy) {
        app.drag.clearDragMode();
        app.state.transferPackSelectDisabled = app.dom.managePackSelect.disabled;
        app.dom.managePackSelect.disabled = true;
        app.state.lastMove = null;
        app.dom.moveResult.classList.add("hidden");
      } else app.dom.managePackSelect.disabled = app.state.transferPackSelectDisabled;
      app.selection.updateSelectionToolbar();
    },
    async onComplete(result, sourcePackId) {
      if (sourcePackId === app.state.activeManagePackId) {
        result.moved.forEach((item) =>
          app.state.selectionState.items.delete(
            app.selection.createSelectionKey(item.category, item.emoji),
          ),
        );
        result.failed.forEach((item) =>
          app.state.selectionState.items.set(
            app.selection.createSelectionKey(item.category, item.emoji),
            { category: item.category, emoji: item.emoji },
          ),
        );
      }
      app.dom.moveResultText.textContent = `已跨包移动 ${result.moved.length} 张${result.failed.length ? `，${result.failed.length} 张未移动，已保留选择` : ""}`;
      app.dom.moveUndoBtn.classList.add("hidden");
      app.dom.moveResult.classList.remove("hidden");
      const details = result.failed
        .slice(0, 5)
        .map((item) => `${item.category}/${item.emoji}：${item.reason}`);
      app.dialogs.showToast(
        [
          app.dom.moveResultText.textContent,
          ...details,
          ...(result.warnings || []),
        ].join("\n"),
        result.failed.length || result.warnings?.length ? "warning" : "success",
        "跨表情包移动结果",
        7000,
      );
      try {
        await app.api.refreshUi({ emojis: true, imgHostStatus: true });
      } catch (error) {
        app.dialogs.showToast(
          `文件操作已完成，但列表刷新失败：${error.message}`,
          "warning",
          "请刷新页面",
        );
      }
      app.selection.updateSelectionUI();
    },
  });
app.state.longPressState = {
    emojiItem: null,
    pointerId: null,
    pointerType: null,
    startTime: 0,
    startX: 0,
    startY: 0,
    currentX: 0,
    currentY: 0,
    timeoutId: null,
    intervalId: null,
  };
app.state.dragModeState = {
    items: [],
    pointerId: null,
    activeCategory: null,
    isPointerDragging: false,
    captureElement: null,
    autoScrollFrameId: null,
    lastClientX: 0,
    lastClientY: 0,
  };
app.state.clipboardState = {
    items: [],
  };
app.state.contextMenuState = {
    items: [],
    targetCategory: null,
  };
app.state.uploadStateByCategory = new Map();
app.state.initialStatusTimerId = null;
app.state.activeCategoryEdit = null;
app.state.pendingMoveTargetItems = [];
app.state.imagePreviewState = null;
app.state.emptyPackGuideShown = false;
app.state.firstUseCatalogGuideShown = false;
}
return { initialize2 };
}
