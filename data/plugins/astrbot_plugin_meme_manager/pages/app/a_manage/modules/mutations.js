
// mutations: one page runtime owns shared state and DOM references.
export function createMutations(app) {
async function uploadEmoji(category, file) {
    return await window.AstrBotPluginPage.upload(
      "emoji/add/" + encodeURIComponent(category),
      file,
    );
  }

async function deleteEmoji(category, emoji) {
    const confirmed = await app.dialogs.showConfirm({
      title: "删除表情包",
      description: `确认删除分类「${category}」中的表情包「${emoji}」？此操作不可恢复。`,
      confirmLabel: "确认删除",
      confirmClassName: "danger",
    });
    if (!confirmed) return;

    try {
      const data = await app.api.apiPost("emoji/delete", {
        category,
        image_file: emoji,
      });
      app.state.selectionState.items.delete(app.selection.createSelectionKey(category, emoji));
      await app.api.refreshUi({ emojis: true });
      app.dialogs.showToast(
        `已从 ${data.category} 删除 ${data.filename}`,
        "success",
        "删除成功",
      );
    } catch (error) {
      console.error("删除表情包失败", error);
      app.dialogs.showToast(`删除表情包失败：${error.message}`, "error", "删除失败", 4500);
    }
  }

async function deleteEmojiItems(
    items,
    { useSelectionState = true, confirmMode = "normal" } = {},
  ) {
    const uniqueItems = app.clipboard.dedupeEmojiItems(items);
    const selectedCount = uniqueItems.length;
    if (selectedCount === 0) {
      app.dialogs.showToast("请先选择要删除的表情包", "warning", "未选择项目");
      return;
    }

    const confirmDescription = `确认删除已选中的 ${selectedCount} 个表情包？未成功删除的项目会保留选中状态。`;
    const confirmed =
      confirmMode === "danger"
        ? await app.danger.showDangerConfirm({
            title: "批量删除表情包",
            description: confirmDescription,
            actionLabel: "确认删除已选文件",
            countdown: 5,
          })
        : await app.dialogs.showConfirm({
            title: "批量删除表情包",
            description: confirmDescription,
            confirmLabel: "确认批量删除",
            confirmClassName: "danger",
          });
    if (!confirmed) {
      return;
    }

    let deletedCount = 0;
    const errors = [];
    const deletedKeys = [];
    const groupedSelections = app.clipboard.groupEmojiItemsByCategory(uniqueItems);

    for (const [category, imageFiles] of groupedSelections.entries()) {
      try {
        const data = await app.api.apiPost("emoji/batch_delete", {
          category,
          image_files: imageFiles,
        });
        deletedCount += data.deleted_count || 0;
        (data.deleted_files || []).forEach((filename) => {
          deletedKeys.push(app.selection.createSelectionKey(category, filename));
        });
      } catch (error) {
        console.error("批量删除失败", error);
        errors.push(`${category}: ${error.message}`);
      }
    }

    if (useSelectionState) {
      deletedKeys.forEach((selectionKey) => {
        app.state.selectionState.items.delete(selectionKey);
      });
    }

    if (deletedCount > 0) {
      await app.api.refreshUi({ emojis: true });
    } else {
      app.selection.updateSelectionUI();
    }

    if (errors.length > 0) {
      app.dialogs.showToast(
        `已删除 ${deletedCount} 个表情包。\n失败分类：${errors.join("；")}`,
        "warning",
        "批量删除部分完成",
        5200,
      );
      return;
    }

    app.dialogs.showToast(`已删除 ${deletedCount} 个表情包`, "success", "批量删除完成");
  }

async function batchDeleteSelected() {
    await deleteEmojiItems(Array.from(app.state.selectionState.items.values()));
  }

async function deleteCategory(category) {
    const emojiCount = Array.isArray(app.state.latestEmojiData[category])
      ? app.state.latestEmojiData[category].length
      : 0;

    const confirmed = await app.danger.showDangerConfirm({
      title: `删除分类「${category}」`,
      description: `该操作会删除分类「${category}」本身，并移除其描述配置${
        emojiCount > 0 ? `，同时删除其中的 ${emojiCount} 个表情包` : ""
      }。`,
      actionLabel: "确认删除当前分类",
      countdown: 5,
    });
    if (!confirmed) {
      return;
    }

    try {
      await app.api.apiPost("category/delete", { category });
      await app.api.refreshUi({ emojis: true, syncStatus: true });
      app.dialogs.showToast(`已删除分类 ${category}`, "success", "删除成功");
    } catch (error) {
      console.error("删除分类失败:", error);
      app.dialogs.showToast(`删除分类失败：${error.message}`, "error", "删除失败", 4500);
    }
  }

async function clearCategory(category) {
    const emojiCount = Array.isArray(app.state.latestEmojiData[category])
      ? app.state.latestEmojiData[category].length
      : 0;
    if (emojiCount === 0) {
      app.dialogs.showToast(
        `分类 ${category} 当前没有可清空的表情包`,
        "warning",
        "无需清空",
      );
      return;
    }

    const confirmed = await app.danger.showDangerConfirm({
      title: `清空分类「${category}」`,
      description: `该操作会删除分类「${category}」下的 ${emojiCount} 个表情包，但会保留分类名称和描述配置。`,
      actionLabel: "确认清空当前分类",
      countdown: 5,
    });
    if (!confirmed) {
      return;
    }

    try {
      const data = await app.api.apiPost("category/clear", { category });
      app.selection.clearSelections();
      await app.api.refreshUi({ emojis: true });
      app.dialogs.showToast(
        `已清空分类 ${category}，删除 ${data.deleted_count} 个表情包。`,
        "success",
        "清空成功",
      );
    } catch (error) {
      console.error("清空分类失败:", error);
      app.dialogs.showToast(`清空分类失败：${error.message}`, "error", "清空失败", 4500);
    }
  }

async function clearAllEmojiFiles() {
    const totalEmojiCount = Object.values(app.state.latestEmojiData).reduce(
      (sum, emojis) => sum + (Array.isArray(emojis) ? emojis.length : 0),
      0,
    );
    if (totalEmojiCount === 0) {
      app.dialogs.showToast("当前没有可清空的表情包", "warning", "无需清空");
      return;
    }

    const confirmed = await app.danger.showDangerConfirm({
      title: "清空全部表情包",
      description: `该操作会删除全部 ${totalEmojiCount} 个表情包，但保留现有分类目录和描述配置。`,
      actionLabel: "确认清空全部表情包",
      countdown: 5,
    });
    if (!confirmed) {
      return;
    }

    try {
      const data = await app.api.apiPost("emoji/clear_all");
      app.selection.clearSelections();
      await app.api.refreshUi({ emojis: true });
      app.dialogs.showToast(
        `已清空全部表情包，共删除 ${data.deleted_count} 个文件，涉及 ${data.affected_categories} 个分类。`,
        "success",
        "清空成功",
        4200,
      );
    } catch (error) {
      console.error("清空全部表情包失败:", error);
      app.dialogs.showToast(
        `清空全部表情包失败：${error.message}`,
        "error",
        "清空失败",
        4500,
      );
    }
  }

async function initialize4() {
app.dom.selectionDockMove.addEventListener("click", () => app.move.openMoveTargetModal());
app.dom.selectionDockDelete.addEventListener("click", batchDeleteSelected);
app.dom.selectionDockPack.addEventListener("click", () => {
    if (!app.state.moveBusy)
      void app.state.packTransfer.open(
        app.state.activeManagePackId,
        app.clipboard.dedupeEmojiItems(Array.from(app.state.selectionState.items.values())),
      );
  });
}
return { uploadEmoji, deleteEmoji, deleteEmojiItems, batchDeleteSelected, deleteCategory, clearCategory, clearAllEmojiFiles, initialize4 };
}
