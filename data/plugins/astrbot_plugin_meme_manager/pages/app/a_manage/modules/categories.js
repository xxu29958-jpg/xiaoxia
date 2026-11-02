
// categories: one page runtime owns shared state and DOM references.
export function createCategories(app) {
async function restoreCategory(category) {
    try {
      const data = await app.api.apiPost("category/restore", { category });

      await app.api.refreshUi({ emojis: true, syncStatus: true });
      app.dialogs.showToast(
        `类别「${category}」已恢复。\n描述：${
          data.description || "请补充描述"
        }`,
        "success",
        "恢复成功",
      );
    } catch (error) {
      console.error("恢复类别失败:", error);
      app.dialogs.showToast(error.message, "error", "恢复失败");
    }
  }

async function removeFromConfig(category) {
    const confirmed = await app.dialogs.showConfirm({
      title: "从配置中删除类别",
      description: `确定要从配置中删除「${category}」吗？该操作不会删除磁盘上的文件夹，只会移除配置记录。`,
      confirmLabel: "确认删除",
      confirmClassName: "danger",
    });
    if (!confirmed) {
      return;
    }

    try {
      await app.api.apiPost("category/remove_from_config", { category });

      await app.api.refreshUi({ syncStatus: true });
      app.dialogs.showToast(`类别「${category}」已从配置中移除。`, "success", "移除成功");
    } catch (error) {
      console.error("从配置中删除类别失败:", error);
      app.dialogs.showToast(error.message, "error", "移除失败");
    }
  }

function closeCategoryEditModal() {
    if (app.dom.categoryEditModalRoot) {
      app.dom.categoryEditModalRoot.classList.add("hidden");
      app.dom.categoryEditModalRoot.setAttribute("aria-hidden", "true");
    }
    app.state.activeCategoryEdit = null;
    if (app.dom.categoryEditNameInput) {
      app.dom.categoryEditNameInput.value = "";
    }
    if (app.dom.categoryEditDescInput) {
      app.dom.categoryEditDescInput.value = "";
    }
    app.layout.unlockPageScroll();
  }

function editCategory(category) {
    const currentDescription = document
      .getElementById(`category-desc-${category}`)
      ?.textContent?.trim();

    app.state.activeCategoryEdit = category;
    if (app.dom.categoryEditModalTitle) {
      app.dom.categoryEditModalTitle.textContent = `编辑类别「${category}」`;
    }
    if (app.dom.categoryEditModalDescription) {
      app.dom.categoryEditModalDescription.textContent =
        "修改类别名称和描述，保存后立即生效。";
    }
    if (app.dom.categoryEditNameInput) {
      app.dom.categoryEditNameInput.value = category;
    }
    if (app.dom.categoryEditDescInput) {
      app.dom.categoryEditDescInput.value =
        currentDescription && currentDescription !== "请添加描述"
          ? currentDescription
          : "";
    }
    if (app.dom.categoryEditModalRoot) {
      app.layout.lockPageScroll();
      app.dom.categoryEditModalRoot.classList.remove("hidden");
      app.dom.categoryEditModalRoot.setAttribute("aria-hidden", "false");
    }
    window.setTimeout(() => {
      app.dom.categoryEditNameInput?.focus();
      app.dom.categoryEditNameInput?.select();
    }, 0);
  }

function cancelEdit() {
    closeCategoryEditModal();
  }

async function saveCategory(oldName = app.state.activeCategoryEdit) {
    const newName = app.dom.categoryEditNameInput?.value.trim() || "";
    const newDesc = app.dom.categoryEditDescInput?.value.trim() || "";

    if (!newName) {
      app.dialogs.showToast("类别名称不能为空。", "warning", "保存失败");
      return;
    }

    if (!oldName) {
      app.dialogs.showToast("未找到当前正在编辑的类别。", "error", "保存失败");
      return;
    }

    app.widgets.setButtonBusy(app.dom.categoryEditSaveBtn, "保存中...");

    try {
      if (oldName !== newName) {
        await app.api.apiPost("category/rename", {
          old_name: oldName,
          new_name: newName,
        });
      }

      await app.api.apiPost("category/update_description", {
        tag: newName,
        description: newDesc,
      });

      await app.api.refreshUi({ emojis: true, syncStatus: true });
      closeCategoryEditModal();
      app.dialogs.showToast(`类别「${newName}」已保存。`, "success", "保存成功");
    } catch (error) {
      console.error("保存类别修改失败:", error);
      app.dialogs.showToast(error.message, "error", "保存失败");
    } finally {
      app.widgets.restoreButton(app.dom.categoryEditSaveBtn);
    }
  }

async function initialize7() {
window.restoreCategory = restoreCategory;
window.removeFromConfig = removeFromConfig;
window.syncConfig = app.sync.syncConfig;
window.editCategory = editCategory;
window.cancelEdit = cancelEdit;
window.saveCategory = saveCategory;
app.layout.syncSidebarLayout();
app.layout.updatePanelToggleState();
window.addEventListener("resize", () => {
    app.layout.syncSidebarLayout();
    app.contextMenu.closeBatchContextMenu();
  });
}
return { restoreCategory, removeFromConfig, closeCategoryEditModal, editCategory, cancelEdit, saveCategory, initialize7 };
}
