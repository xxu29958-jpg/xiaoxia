
// contextMenu: one page runtime owns shared state and DOM references.
export function createContextMenu(app) {
function closeBatchContextMenu() {
    app.state.contextMenuState.items = [];
    app.state.contextMenuState.targetCategory = null;
    if (app.dom.batchContextMenu) {
      app.dom.batchContextMenu.classList.add("hidden");
      app.dom.batchContextMenu.setAttribute("aria-hidden", "true");
      app.dom.batchContextMenu.style.left = "-9999px";
      app.dom.batchContextMenu.style.top = "-9999px";
    }
  }

function openBatchContextMenu(event) {
    if (!app.dom.batchContextMenu || !app.state.selectionState.enabled) {
      return;
    }

    closeBatchContextMenu();

    const targetEmojiItem = event.target.closest(".emoji-item");
    const targetCategoryElement = event.target.closest(".category");
    const targetCategory =
      targetEmojiItem?.dataset.category ||
      targetCategoryElement?.dataset.category ||
      null;
    const targetItems = app.clipboard.getContextMenuTargetItems(targetEmojiItem);
    const pasteableItems = app.clipboard.getPasteableClipboardItems(targetCategory);

    if (targetItems.length === 0 && pasteableItems.length === 0) {
      return;
    }

    app.state.contextMenuState.items = targetItems;
    app.state.contextMenuState.targetCategory = targetCategory;

    if (app.dom.batchContextMenuTitle) {
      app.dom.batchContextMenuTitle.textContent =
        targetItems.length > 0
          ? `批量管理 ${targetItems.length} 个文件`
          : "批量管理";
    }
    if (app.dom.batchContextMenuSubtitle) {
      if (targetCategory && pasteableItems.length > 0) {
        app.dom.batchContextMenuSubtitle.textContent = `当前分类：${targetCategory}，可粘贴 ${pasteableItems.length} 个文件`;
      } else if (targetCategory) {
        app.dom.batchContextMenuSubtitle.textContent = `当前分类：${targetCategory}`;
      } else {
        app.dom.batchContextMenuSubtitle.textContent = "选择一个操作继续";
      }
    }

    if (app.dom.contextMenuDeleteBtn) {
      app.dom.contextMenuDeleteBtn.disabled = targetItems.length === 0;
    }
    if (app.dom.contextMenuMoveBtn) {
      app.dom.contextMenuMoveBtn.disabled =
        targetItems.length === 0 ||
        app.clipboard.getAvailableMoveTargets(targetItems).length === 0;
    }
    if (app.dom.contextMenuCopyBtn) {
      app.dom.contextMenuCopyBtn.disabled = targetItems.length === 0;
    }
    if (app.dom.contextMenuPasteBtn) {
      app.dom.contextMenuPasteBtn.disabled =
        pasteableItems.length === 0 || !targetCategory;
    }

    app.dom.batchContextMenu.classList.remove("hidden");
    app.dom.batchContextMenu.setAttribute("aria-hidden", "false");

    requestAnimationFrame(() => {
      const menuWidth = app.dom.batchContextMenu.offsetWidth || 240;
      const menuHeight = app.dom.batchContextMenu.offsetHeight || 220;
      const left = Math.min(
        window.innerWidth - menuWidth - 12,
        Math.max(12, event.clientX),
      );
      const top = Math.min(
        window.innerHeight - menuHeight - 12,
        Math.max(12, event.clientY),
      );
      app.dom.batchContextMenu.style.left = `${left}px`;
      app.dom.batchContextMenu.style.top = `${top}px`;
    });
  }

function shouldOpenBatchContextMenu(event) {
    if (!app.state.selectionState.enabled || app.drag.hasActiveDragInteraction()) {
      return false;
    }

    return Boolean(
      event.target.closest(".emoji-item") ||
      event.target.closest(".emoji-upload") ||
      event.target.closest(".category"),
    );
  }
return { closeBatchContextMenu, openBatchContextMenu, shouldOpenBatchContextMenu };
}
