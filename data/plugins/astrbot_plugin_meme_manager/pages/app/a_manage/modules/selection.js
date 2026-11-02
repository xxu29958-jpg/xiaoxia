
// selection: one page runtime owns shared state and DOM references.
export function createSelection(app) {
function createSelectionKey(category, emoji) {
    return `${category}::${emoji}`;
  }

function isEmojiSelected(category, emoji) {
    return app.state.selectionState.items.has(createSelectionKey(category, emoji));
  }

function getCategorySelectedCount(category) {
    let count = 0;
    app.state.selectionState.items.forEach((item) => {
      if (item.category === category) {
        count += 1;
      }
    });
    return count;
  }

function pruneSelectionState() {
    const availableKeys = new Set();
    Object.entries(app.state.latestEmojiData).forEach(([category, emojis]) => {
      if (!Array.isArray(emojis)) return;
      emojis.forEach((emoji) => {
        availableKeys.add(createSelectionKey(category, emoji));
      });
    });

    Array.from(app.state.selectionState.items.keys()).forEach((key) => {
      if (!availableKeys.has(key)) {
        app.state.selectionState.items.delete(key);
      }
    });
  }

function updateSelectionToolbar() {
    const selectedCount = app.state.selectionState.items.size;
    const availableMoveTargets = app.clipboard.getAvailableMoveTargets();
    const readOnlyPack =
      app.state.activeManagePackId &&
      app.state.defaultManagePackId &&
      app.state.activeManagePackId !== app.state.defaultManagePackId;

    app.dom.selectionDock.classList.toggle("hidden", !app.state.selectionState.enabled);
    document.body.classList.toggle(
      "has-selection-dock",
      app.state.selectionState.enabled,
    );
    app.dom.selectionDockSummary.textContent = `已选 ${selectedCount} 张`;
    app.dom.selectionDockMove.disabled =
      app.state.moveBusy ||
      readOnlyPack ||
      !selectedCount ||
      !availableMoveTargets.length;
    app.dom.selectionDockMove.textContent = app.state.moveBusy ? "移动中…" : "移动分类";
    app.dom.selectionDockDelete.disabled = app.state.moveBusy || readOnlyPack || !selectedCount;
    app.dom.selectionDockPack.disabled =
      app.state.moveBusy || !selectedCount || !app.state.activeManagePackId;
  }

function updateSelectionDecorations() {
    document.querySelectorAll(".emoji-item").forEach((emojiItem) => {
      const category = emojiItem.dataset.category;
      const emoji = emojiItem.dataset.emoji;
      const selected = isEmojiSelected(category, emoji);
      const selectionIndicator = emojiItem.querySelector(
        ".selection-indicator",
      );

      emojiItem.classList.toggle("selection-mode", app.state.selectionState.enabled);
      emojiItem.classList.toggle("selected", selected);
      if (selectionIndicator) {
        selectionIndicator.classList.toggle("checked", selected);
        selectionIndicator.setAttribute(
          "aria-label",
          selected ? "已选中" : "未选择",
        );
      }
    });

    document.querySelectorAll(".category").forEach((categoryDiv) => {
      const category = categoryDiv.dataset.category;
      const visibleItems = [...categoryDiv.querySelectorAll(".emoji-item")];
      const totalCount = visibleItems.length;
      const selectedCount = visibleItems.filter((item) =>
        isEmojiSelected(category, item.dataset.emoji),
      ).length;
      const filtered =
        Boolean(app.state.librarySearch) || app.state.activeSemanticReviewFilter !== "all";
      const summary = categoryDiv.querySelector(".category-selection-summary");
      const selectAllBtn = categoryDiv.querySelector(
        ".select-all-category-btn",
      );
      const hasEmojis = totalCount > 0;
      const allSelected = hasEmojis && selectedCount === totalCount;

      if (summary) {
        summary.textContent = app.state.selectionState.enabled
          ? `${
              filtered ? "可见项已选" : "已选"
            } ${selectedCount} / ${totalCount}`
          : "未开启批量选择";
      }
      if (selectAllBtn) {
        selectAllBtn.disabled = !hasEmojis;
        selectAllBtn.textContent = app.state.selectionState.enabled
          ? allSelected
            ? filtered
              ? "取消可见项"
              : "取消本类"
            : filtered
              ? "全选可见项"
              : "本类全选"
          : filtered
            ? "选择可见项"
            : "本类选择";
      }
    });
  }

function updateSelectionUI() {
    updateSelectionToolbar();
    updateSelectionDecorations();
  }

function clearSelections() {
    app.drag.clearDragMode();
    app.move.closeMoveTargetModal();
    app.contextMenu.closeBatchContextMenu();
    app.state.selectionState.items.clear();
    updateSelectionUI();
  }

function setSelectionMode(enabled) {
    app.drag.clearDragMode();
    app.move.closeMoveTargetModal();
    app.contextMenu.closeBatchContextMenu();
    app.state.selectionState.enabled = enabled;
    if (!enabled) {
      app.state.selectionState.items.clear();
    }
    updateSelectionUI();
  }

function toggleEmojiSelection(category, emoji) {
    app.drag.clearDragMode();
    app.move.closeMoveTargetModal();
    app.contextMenu.closeBatchContextMenu();
    const selectionKey = createSelectionKey(category, emoji);
    if (app.state.selectionState.items.has(selectionKey)) {
      app.state.selectionState.items.delete(selectionKey);
    } else {
      app.state.selectionState.items.set(selectionKey, { category, emoji });
    }
    updateSelectionUI();
  }

function toggleCategorySelection(category, emojis) {
    if (!Array.isArray(emojis) || emojis.length === 0) {
      return;
    }

    app.drag.clearDragMode();
    app.move.closeMoveTargetModal();
    app.contextMenu.closeBatchContextMenu();
    if (!app.state.selectionState.enabled) {
      setSelectionMode(true);
    }

    const allSelected = emojis.every((emoji) =>
      isEmojiSelected(category, emoji),
    );
    emojis.forEach((emoji) => {
      const selectionKey = createSelectionKey(category, emoji);
      if (allSelected) {
        app.state.selectionState.items.delete(selectionKey);
      } else {
        app.state.selectionState.items.set(selectionKey, { category, emoji });
      }
    });
    updateSelectionUI();
  }

function getSelectedItemsByCategory() {
    const groupedSelections = new Map();
    app.state.selectionState.items.forEach(({ category, emoji }) => {
      if (!groupedSelections.has(category)) {
        groupedSelections.set(category, []);
      }
      groupedSelections.get(category).push(emoji);
    });
    return groupedSelections;
  }

function copyItemsToClipboard(items) {
    const uniqueItems = app.clipboard.dedupeEmojiItems(items);
    if (uniqueItems.length === 0) {
      app.dialogs.showToast("请先选择要复制的表情包。", "warning", "未选择项目");
      return false;
    }

    app.clipboard.setClipboardItems(uniqueItems);
    app.dialogs.showToast(
      uniqueItems.length > 1
        ? `已复制 ${uniqueItems.length} 个表情包，可在目标分类右键后粘贴。`
        : "已复制 1 个表情包，可在目标分类右键后粘贴。",
      "success",
      "已复制到批量剪贴板",
    );
    return true;
  }
return { createSelectionKey, isEmojiSelected, getCategorySelectedCount, pruneSelectionState, updateSelectionToolbar, updateSelectionDecorations, updateSelectionUI, clearSelections, setSelectionMode, toggleEmojiSelection, toggleCategorySelection, getSelectedItemsByCategory, copyItemsToClipboard };
}
