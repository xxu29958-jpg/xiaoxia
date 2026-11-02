
// packImport: one page runtime owns shared state and DOM references.
export function createPackImport(app) {
function resetPackImportPreview({ keepResult = false } = {}) {
    app.state.pendingPackImportToken = "";
    if (app.dom.packImportFile) app.dom.packImportFile.value = "";
    if (app.dom.packImportFileLabel)
      app.dom.packImportFileLabel.textContent = "选择或拖入 zip 压缩包";
    app.dom.packImportDropzone?.classList.remove("hidden");
    app.dom.packImportPreview?.classList.add("hidden");
    app.dom.packImportWarning?.classList.add("hidden");
    if (app.dom.packImportSetDefault) app.dom.packImportSetDefault.checked = false;
    if (app.dom.packImportOverwrite) app.dom.packImportOverwrite.checked = false;
    if (app.dom.packImportOverwriteManual) app.dom.packImportOverwriteManual.checked = false;
    if (!keepResult) app.packTransfer.setPackTransferResult(app.dom.packImportResult, "", "");
  }

function renderPackImportInspection(data) {
    const formatLabels = {
      v2: data?.export_mode === "backup" ? "新版带向量备份" : "新版分享包",
      v1: "兼容版资源包",
      legacy: "旧版无语义包，将自动转换",
    };
    if (app.dom.packImportPreviewName) {
      app.dom.packImportPreviewName.textContent = `${
        data?.name || data?.pack_id || "待导入表情包"
      } (${data?.pack_id || "未知 ID"})`;
    }
    if (app.dom.packImportPreviewFormat) {
      app.dom.packImportPreviewFormat.textContent =
        formatLabels[data?.detected_format] || "已识别的表情包";
    }
    if (app.dom.packImportImageCount) {
      app.dom.packImportImageCount.textContent = Number(data?.image_count || 0);
    }
    if (app.dom.packImportCategoryCount) {
      app.dom.packImportCategoryCount.textContent = Number(data?.category_count || 0);
    }
    if (app.dom.packImportSemanticCount) {
      app.dom.packImportSemanticCount.textContent = data?.semantic_metadata
        ? `${Number(data?.semantic_done || 0)} 条`
        : "无";
    }
    if (app.dom.packImportVectorState) {
      app.dom.packImportVectorState.textContent = data?.vectors_present
        ? "包含，将校验"
        : "不包含";
    }
    const warnings = Array.isArray(data?.warnings) ? data.warnings : [];
    if (app.dom.packImportWarning) {
      app.dom.packImportWarning.textContent = warnings.join(" ");
      app.dom.packImportWarning.classList.toggle("hidden", warnings.length === 0);
    }
    app.dom.packImportDropzone?.classList.add("hidden");
    app.dom.packImportPreview?.classList.remove("hidden");
  }

async function stagePackImport(file) {
    if (!file) {
      return;
    }
    if (
      !String(file.name || "")
        .toLowerCase()
        .endsWith(".zip")
    ) {
      app.dialogs.showToast("请选择 zip 格式的表情包。", "warning", "格式不支持");
      return;
    }
    app.state.pendingPackImportToken = "";
    if (app.dom.packImportFileLabel)
      app.dom.packImportFileLabel.textContent = `正在检查 ${file.name}…`;
    app.dom.packImportDropzone?.classList.add("checking");
    app.packTransfer.setPackTransferResult(app.dom.packImportResult, "正在检查压缩包结构和兼容性…", "");
    try {
      const data = await window.AstrBotPluginPage.upload(
        "packs/import/stage",
        file,
      );
      app.state.pendingPackImportToken = String(data?.import_token || "").trim();
      if (!app.state.pendingPackImportToken) {
        throw new Error("服务器没有返回导入凭证");
      }
      renderPackImportInspection(data);
      app.packTransfer.setPackTransferResult(
        app.dom.packImportResult,
        "检查完成，请确认导入选项。",
        "success",
      );
    } catch (error) {
      resetPackImportPreview({ keepResult: true });
      app.packTransfer.setPackTransferResult(
        app.dom.packImportResult,
        error?.message || String(error),
        "error",
      );
      app.dialogs.showToast(error?.message || String(error), "error", "压缩包检查失败");
    } finally {
      app.dom.packImportDropzone?.classList.remove("checking");
    }
  }

async function confirmPackImport() {
    if (!app.state.pendingPackImportToken) {
      app.dialogs.showToast("请先选择并检查压缩包。", "warning", "无法导入");
      return;
    }
    if (app.dom.packImportOverwrite?.checked) {
      const confirmed = await app.dialogs.showConfirm({
        title: "确认覆盖同名表情包？",
        description: app.dom.packImportOverwriteManual?.checked
          ? "原表情包、向量和本机人工语义都会被替换。建议先导出自用备份。"
          : "原表情包及其向量会被替换，但本机人工描述、标签和图片文字会保留。",
        confirmLabel: "确认覆盖并导入",
        confirmClassName: "danger",
      });
      if (!confirmed) {
        return;
      }
    }

    app.widgets.setButtonBusy(app.dom.packImportConfirmBtn, "正在导入…");
    app.packTransfer.setPackTransferResult(
      app.dom.packImportResult,
      "正在安装表情包，请不要关闭页面。",
      "",
    );
    try {
      const data = await app.api.apiPost("packs/import/apply", {
        import_token: app.state.pendingPackImportToken,
        overwrite: Boolean(app.dom.packImportOverwrite?.checked),
        overwrite_manual_semantics: Boolean(
          app.dom.packImportOverwrite?.checked && app.dom.packImportOverwriteManual?.checked,
        ),
        set_as_default: Boolean(app.dom.packImportSetDefault?.checked),
      });
      const importedPackId = String(data?.pack_id || "").trim();
      const vectorHint = data?.vectors_restored
        ? "，向量已恢复"
        : data?.vector_warning
          ? `；${data.vector_warning}`
          : "";
      resetPackImportPreview({ keepResult: true });
      app.packTransfer.setPackTransferResult(
        app.dom.packImportResult,
        `已导入 ${data?.name || importedPackId}${vectorHint}`,
        "success",
      );
      await app.packCatalog.loadManagePackSwitcher(importedPackId);
      await app.api.refreshUi({ emojis: true, syncStatus: true });
      await app.packTransfer.refreshPackExportCapability(importedPackId);
      app.dialogs.showToast(`表情包 ${importedPackId} 已导入。`, "success", "导入成功");
      await app.vectors.maybeOfferVectorRebuild(importedPackId, data);
    } catch (error) {
      app.packTransfer.setPackTransferResult(
        app.dom.packImportResult,
        error?.message || String(error),
        "error",
      );
      app.dialogs.showToast(error?.message || String(error), "error", "导入失败");
    } finally {
      app.widgets.restoreButton(app.dom.packImportConfirmBtn);
    }
  }
return { resetPackImportPreview, renderPackImportInspection, stagePackImport, confirmPackImport };
}
