
// move: one page runtime owns shared state and DOM references.
export function createMove(app) {
function closeMoveTargetModal() {
    const wasOpen =
      app.dom.moveTargetModalRoot && !app.dom.moveTargetModalRoot.classList.contains("hidden");
    if (app.dom.moveTargetModalRoot) {
      app.dom.moveTargetModalRoot.classList.add("hidden");
      app.dom.moveTargetModalRoot.setAttribute("aria-hidden", "true");
    }
    app.state.pendingMoveTargetItems = [];
    if (app.dom.moveTargetList) {
      app.dom.moveTargetList.innerHTML = "";
    }
    app.layout.unlockPageScroll();
    if (wasOpen && app.state.moveTargetRestoreFocus?.isConnected)
      app.state.moveTargetRestoreFocus.focus({ preventScroll: true });
    app.state.moveTargetRestoreFocus = null;
  }

function openMoveTargetModal(
    items = Array.from(app.state.selectionState.items.values()),
  ) {
    if (app.state.moveBusy) return;
    const uniqueItems = app.clipboard.dedupeEmojiItems(items);
    if (uniqueItems.length === 0) {
      app.dialogs.showToast("请先选择要移动的表情包。", "warning", "未选择项目");
      return;
    }

    const availableTargets = app.clipboard.getAvailableMoveTargets(uniqueItems);
    if (availableTargets.length === 0) {
      app.dialogs.showToast("当前没有可移动到的其他分类。", "warning", "无法移动");
      return;
    }

    app.state.pendingMoveTargetItems = uniqueItems;
    app.state.moveTargetRestoreFocus = document.activeElement;
    app.dom.moveTargetSearch.value = "";
    document.getElementById("move-target-empty").classList.add("hidden");
    if (app.dom.moveTargetModalTitle) {
      app.dom.moveTargetModalTitle.textContent = "选择目标分类";
    }
    if (app.dom.moveTargetModalDescription) {
      app.dom.moveTargetModalDescription.textContent =
        uniqueItems.length > 1
          ? `已选 ${uniqueItems.length} 个表情包，选择要批量移动到的分类。`
          : "选择要移动到的目标分类。";
    }

    if (app.dom.moveTargetList) {
      app.dom.moveTargetList.innerHTML = "";
      availableTargets.forEach((category) => {
        const moveableCount = app.clipboard.getMoveableCountForTarget(uniqueItems, category);
        const optionButton = app.widgets.createButton({
          className: "move-target-option",
          onClick: async () => {
            closeMoveTargetModal();
            await moveEmojiItemsToCategory(category, uniqueItems);
          },
        });

        const title = document.createElement("span");
        optionButton.dataset.category = category;
        title.className = "move-target-option-title";
        title.textContent = category;

        const meta = document.createElement("span");
        meta.className = "move-target-option-meta";
        meta.textContent = `可移动 ${moveableCount} 个表情包`;

        optionButton.appendChild(title);
        optionButton.appendChild(meta);
        app.dom.moveTargetList.appendChild(optionButton);
      });
    }

    if (app.dom.moveTargetModalRoot) {
      app.layout.lockPageScroll();
      app.dom.moveTargetModalRoot.classList.remove("hidden");
      app.dom.moveTargetModalRoot.setAttribute("aria-hidden", "false");
      app.dom.moveTargetCancelBtn.focus({ preventScroll: true });
    }
  }

async function moveEmojiItemsToCategory(targetCategory, items, undo = false) {
    if (app.state.moveBusy) return;
    const packId = app.state.activeManagePackId;
    if (
      (packId && app.state.defaultManagePackId && packId !== app.state.defaultManagePackId) ||
      (undo && app.state.lastMove?.packId !== packId)
    ) {
      app.dialogs.showToast("请切回执行移动时的默认管理包。", "warning", "无法移动");
      return;
    }
    if (!targetCategory) {
      app.dialogs.showToast("请先选择目标分类。", "warning", "缺少目标分类");
      return;
    }

    const moveableItems = app.clipboard.dedupeEmojiItems(items).filter(
      (item) => item.category !== targetCategory,
    );
    if (moveableItems.length === 0) {
      app.dialogs.showToast("当前选择的表情包已经都在目标分类中。", "warning", "无需移动");
      app.drag.clearDragMode();
      return;
    }

    app.drag.clearDragMode();
    app.state.moveBusy = true;
    const wasPackSelectDisabled = app.dom.managePackSelect?.disabled;
    if (app.dom.managePackSelect) app.dom.managePackSelect.disabled = true;
    app.dom.moveUndoBtn.disabled = true;
    app.selection.updateSelectionToolbar();
    if (!undo) {
      app.state.lastMove = null;
      app.dom.moveResult.classList.add("hidden");
    }
    const groupedItems = app.clipboard.groupEmojiItemsByCategory(moveableItems);

    let movedCount = 0;
    const movedKeys = [];
    const conflictFiles = [];
    const missingFiles = [];
    const requestErrors = [];
    const completedItems = [];
    try {
      for (const [sourceCategory, imageFiles] of groupedItems.entries()) {
        try {
          if (app.state.activeManagePackId !== packId || app.state.defaultManagePackId !== packId)
            throw new Error("管理包已变化，请重新加载后操作");
          const data = await app.api.apiPost("emoji/batch_move", {
            source_category: undo ? targetCategory : sourceCategory,
            target_category: undo ? sourceCategory : targetCategory,
            image_files: imageFiles,
          });

          movedCount += (data.moved_files || []).length;
          (data.moved_files || []).forEach((filename) => {
            movedKeys.push(app.selection.createSelectionKey(sourceCategory, filename));
            completedItems.push({ category: sourceCategory, emoji: filename });
          });
          (data.conflicting_files || []).forEach((filename) => {
            conflictFiles.push(`${sourceCategory}/${filename}`);
          });
          (data.missing_files || []).forEach((filename) => {
            missingFiles.push(`${sourceCategory}/${filename}`);
          });
        } catch (error) {
          console.error("Failed to move emoji batch", error);
          requestErrors.push(`${sourceCategory}: ${error.message}`);
        }
      }

      const completedKeys = new Set(movedKeys);
      const remainingItems = moveableItems.filter(
        (item) =>
          !completedKeys.has(app.selection.createSelectionKey(item.category, item.emoji)),
      );
      if (undo) {
        completedItems.forEach(({ emoji }) =>
          app.state.selectionState.items.delete(
            app.selection.createSelectionKey(targetCategory, emoji),
          ),
        );
        app.state.lastMove = remainingItems.length
          ? { packId, targetCategory, items: remainingItems }
          : null;
      } else {
        movedKeys.forEach((key) => app.state.selectionState.items.delete(key));
        if (remainingItems.length) {
          app.state.selectionState.enabled = true;
          remainingItems.forEach((item) =>
            app.state.selectionState.items.set(
              app.selection.createSelectionKey(item.category, item.emoji),
              item,
            ),
          );
        }
        app.state.lastMove = completedItems.length
          ? { packId, targetCategory, items: completedItems }
          : null;
      }
      app.dom.moveResultText.textContent = undo
        ? `已撤销 ${movedCount} 张${remainingItems.length ? `，${remainingItems.length} 张未恢复，可重试` : ""}`
        : `已移动 ${movedCount} 张到 ${targetCategory}${remainingItems.length ? `，${remainingItems.length} 张未移动` : ""}`;
      app.dom.moveUndoBtn.textContent = undo && app.state.lastMove ? "重试撤销" : "撤销";
      app.dom.moveUndoBtn.classList.toggle("hidden", !app.state.lastMove);
      app.dom.moveResult.classList.remove("hidden");

      if (movedCount > 0) {
        try {
          await app.api.refreshUi({ emojis: true, imgHostStatus: true });
        } catch (error) {
          app.dialogs.showToast(
            `文件操作已完成，但列表刷新失败：${error.message}。请刷新页面。`,
            "warning",
            "刷新失败",
          );
        }
      }
      app.selection.updateSelectionUI();

      if (
        requestErrors.length > 0 ||
        conflictFiles.length > 0 ||
        missingFiles.length > 0
      ) {
        const messageParts = [
          `已${undo ? "恢复" : "移动"} ${movedCount} 个表情包。`,
        ];
        if (conflictFiles.length > 0) {
          messageParts.push(`目标分类已存在：${conflictFiles.join("、")}`);
        }
        if (missingFiles.length > 0) {
          messageParts.push(`源文件不存在：${missingFiles.join("、")}`);
        }
        if (requestErrors.length > 0) {
          messageParts.push(`请求失败：${requestErrors.join("；")}`);
        }
        app.dialogs.showToast(
          messageParts.join("\n"),
          "warning",
          undo ? "撤销未全部完成" : "移动未全部完成",
          5600,
        );
        return;
      }

      app.dialogs.showToast(
        undo
          ? `已将 ${movedCount} 个表情包恢复到原分类`
          : `已移动 ${movedCount} 个表情包到 ${targetCategory}`,
        "success",
        undo ? "撤销成功" : "移动成功",
      );
    } finally {
      app.state.moveBusy = false;
      if (app.dom.managePackSelect) app.dom.managePackSelect.disabled = wasPackSelectDisabled;
      app.dom.moveUndoBtn.disabled = false;
      app.selection.updateSelectionToolbar();
    }
  }

async function copyEmojiItemsToCategory(targetCategory, items) {
    if (!targetCategory) {
      app.dialogs.showToast("请先选择要粘贴到的分类。", "warning", "缺少目标分类");
      return;
    }

    const pasteableItems = app.clipboard.dedupeEmojiItems(items).filter(
      (item) => item.category !== targetCategory,
    );

    if (pasteableItems.length === 0) {
      app.dialogs.showToast("当前没有可粘贴到该分类的文件。", "warning", "无需粘贴");
      return;
    }

    const groupedItems = app.clipboard.groupEmojiItemsByCategory(pasteableItems);
    let copiedCount = 0;
    const conflictFiles = [];
    const missingFiles = [];
    const requestErrors = [];

    for (const [sourceCategory, imageFiles] of groupedItems.entries()) {
      try {
        const data = await app.api.apiPost("emoji/batch_copy", {
          source_category: sourceCategory,
          target_category: targetCategory,
          image_files: imageFiles,
        });

        copiedCount += data.copied_count || 0;
        (data.conflicting_files || []).forEach((filename) => {
          conflictFiles.push(`${sourceCategory}/${filename}`);
        });
        (data.missing_files || []).forEach((filename) => {
          missingFiles.push(`${sourceCategory}/${filename}`);
        });
      } catch (error) {
        console.error("批量复制表情包失败", error);
        requestErrors.push(`${sourceCategory}: ${error.message}`);
      }
    }

    if (copiedCount > 0) {
      await app.api.refreshUi({ emojis: true, imgHostStatus: true });
    }

    if (
      requestErrors.length > 0 ||
      conflictFiles.length > 0 ||
      missingFiles.length > 0
    ) {
      const messageParts = [`已成功粘贴 ${copiedCount} 个表情包。`];
      if (conflictFiles.length > 0) {
        messageParts.push(`目标分类已存在：${conflictFiles.join("、")}`);
      }
      if (missingFiles.length > 0) {
        messageParts.push(`源文件不存在：${missingFiles.join("、")}`);
      }
      if (requestErrors.length > 0) {
        messageParts.push(`请求失败：${requestErrors.join("；")}`);
      }
      app.dialogs.showToast(messageParts.join("\n"), "warning", "粘贴部分完成", 5600);
      return;
    }

    app.dialogs.showToast(
      `已粘贴 ${copiedCount} 个表情包到 ${targetCategory}`,
      "success",
      "粘贴成功",
    );
  }

function attachCategoryDropTarget(categoryDiv, category) {
    let dragDepth = 0;

    const setActive = (active) => {
      categoryDiv.classList.toggle("category-drop-active", active);
    };

    categoryDiv.addEventListener("dragenter", (event) => {
      if (!app.drag.isInternalEmojiDrag(event)) {
        return;
      }

      const payload = app.drag.getDraggedEmojiPayload(event);
      if (!payload || !app.drag.hasMoveableItemsForTarget(payload.items, category)) {
        return;
      }

      event.preventDefault();
      dragDepth += 1;
      setActive(true);
    });

    categoryDiv.addEventListener("dragover", (event) => {
      if (!app.drag.isInternalEmojiDrag(event)) {
        return;
      }

      const payload = app.drag.getDraggedEmojiPayload(event);
      if (!payload || !app.drag.hasMoveableItemsForTarget(payload.items, category)) {
        return;
      }

      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = "move";
      }
      setActive(true);
    });

    categoryDiv.addEventListener("dragleave", (event) => {
      if (!app.drag.isInternalEmojiDrag(event)) {
        return;
      }

      event.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) {
        setActive(false);
      }
    });

    categoryDiv.addEventListener("drop", async (event) => {
      if (!app.drag.isInternalEmojiDrag(event)) {
        return;
      }

      const payload = app.drag.getDraggedEmojiPayload(event);
      dragDepth = 0;
      setActive(false);
      if (!payload || !app.drag.hasMoveableItemsForTarget(payload.items, category)) {
        return;
      }

      event.preventDefault();
      await moveEmojiItemsToCategory(category, payload.items);
    });
  }
return { closeMoveTargetModal, openMoveTargetModal, moveEmojiItemsToCategory, copyEmojiItemsToCategory, attachCategoryDropTarget };
}
