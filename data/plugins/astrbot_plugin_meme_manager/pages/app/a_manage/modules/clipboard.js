
// clipboard: one page runtime owns shared state and DOM references.
export function createClipboard(app) {
function getSortedCategories() {
    return Object.keys(app.state.latestEmojiData).sort((left, right) =>
      left.localeCompare(right, "zh-CN"),
    );
  }

function getMoveableCountForTarget(items, targetCategory) {
    if (!targetCategory) {
      return 0;
    }

    return dedupeEmojiItems(items).filter(
      (item) => item.category !== targetCategory,
    ).length;
  }

function getAvailableMoveTargets(
    items = Array.from(app.state.selectionState.items.values()),
  ) {
    const uniqueItems = dedupeEmojiItems(items);
    if (uniqueItems.length === 0) {
      return [];
    }

    return getSortedCategories().filter(
      (category) => getMoveableCountForTarget(uniqueItems, category) > 0,
    );
  }

function dedupeEmojiItems(items) {
    const uniqueItems = new Map();
    (items || []).forEach((item) => {
      if (!item?.category || !item?.emoji) {
        return;
      }
      uniqueItems.set(app.selection.createSelectionKey(item.category, item.emoji), {
        category: item.category,
        emoji: item.emoji,
      });
    });
    return Array.from(uniqueItems.values());
  }

function groupEmojiItemsByCategory(items) {
    const groupedItems = new Map();
    dedupeEmojiItems(items).forEach((item) => {
      if (!groupedItems.has(item.category)) {
        groupedItems.set(item.category, []);
      }
      groupedItems.get(item.category).push(item.emoji);
    });
    return groupedItems;
  }

function setClipboardItems(items) {
    app.state.clipboardState.items = dedupeEmojiItems(items);
  }

function getClipboardItems() {
    return dedupeEmojiItems(app.state.clipboardState.items);
  }

function getContextMenuTargetItems(targetEmojiItem) {
    if (!targetEmojiItem) {
      return dedupeEmojiItems(Array.from(app.state.selectionState.items.values()));
    }

    const targetCategory = targetEmojiItem.dataset.category;
    const targetEmoji = targetEmojiItem.dataset.emoji;
    if (
      app.state.selectionState.enabled &&
      app.selection.isEmojiSelected(targetCategory, targetEmoji)
    ) {
      return dedupeEmojiItems(Array.from(app.state.selectionState.items.values()));
    }

    return [{ category: targetCategory, emoji: targetEmoji }];
  }

function getPasteableClipboardItems(targetCategory) {
    if (!targetCategory) {
      return [];
    }

    return getClipboardItems().filter(
      (item) => item.category !== targetCategory,
    );
  }
return { getSortedCategories, getMoveableCountForTarget, getAvailableMoveTargets, dedupeEmojiItems, groupEmojiItemsByCategory, setClipboardItems, getClipboardItems, getContextMenuTargetItems, getPasteableClipboardItems };
}
