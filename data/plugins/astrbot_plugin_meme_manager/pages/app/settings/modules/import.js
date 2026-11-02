
// import: one page runtime owns shared state and DOM references.
export function createImport(app) {
function resetPackImportPreview({ keepResult = false } = {}) {
    app.state.pendingPackImportToken = "";
    if (app.dom.packImportFile) app.dom.packImportFile.value = "";
    if (app.dom.packImportFileLabel)
      app.dom.packImportFileLabel.textContent = "选择或拖入 zip 文件";
    app.dom.packImportDropzone?.classList.remove("hidden");
    app.dom.packImportPreview?.classList.add("hidden");
    app.dom.packImportWarning?.classList.add("hidden");
    if (app.dom.packImportSetDefault) app.dom.packImportSetDefault.checked = false;
    if (app.dom.packImportOverwrite) app.dom.packImportOverwrite.checked = false;
    if (app.dom.packImportOverwriteManual) app.dom.packImportOverwriteManual.checked = false;
    if (app.dom.packImportOverwriteManual) app.dom.packImportOverwriteManual.disabled = true;
    if (!keepResult) app.status.setPackTransferResult(app.dom.packImportResult, "", "");
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
    if (!file || app.state.packImportBusy) {
      return;
    }
    if (
      !String(file.name || "")
        .toLowerCase()
        .endsWith(".zip")
    ) {
      app.status.setPackTransferResult(
        app.dom.packImportResult,
        "请选择 zip 格式的表情包。",
        "error",
      );
      app.status.addLog("单包导入失败: 文件格式不支持", true);
      return;
    }
    app.state.pendingPackImportToken = "";
    app.state.packImportBusy = true;
    app.dom.importBackupBtn.disabled = true;
    app.dom.packImportFile.disabled = true;
    app.dom.packImportDropzone.setAttribute("aria-disabled", "true");
    app.dom.packImportDropzone.setAttribute("aria-busy", "true");
    if (app.dom.packImportFileLabel) {
      app.dom.packImportFileLabel.textContent = `正在检查 ${file.name}…`;
    }
    app.dom.packImportDropzone?.classList.add("checking");
    app.status.setPackTransferResult(app.dom.packImportResult, "正在检查压缩包结构和兼容性…", "");
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
      app.dom.packImportConfirmBtn.focus();
      app.status.setPackTransferResult(
        app.dom.packImportResult,
        "检查完成，请确认导入选项。",
        "success",
      );
      app.status.addLog(`单包导入检查完成: ${data?.pack_id || file.name}`);
    } catch (error) {
      resetPackImportPreview({ keepResult: true });
      app.status.setPackTransferResult(
        app.dom.packImportResult,
        error?.message || String(error),
        "error",
      );
      app.status.addLog(`单包导入检查失败: ${error?.message || String(error)}`, true);
    } finally {
      app.state.packImportBusy = false;
      app.dom.importBackupBtn.disabled = app.state.rulesBusy;
      app.dom.packImportFile.disabled = false;
      app.dom.packImportDropzone.removeAttribute("aria-disabled");
      app.dom.packImportDropzone.removeAttribute("aria-busy");
      app.dom.packImportDropzone?.classList.remove("checking");
    }
  }

async function confirmPackImport() {
    if (app.state.packImportBusy || app.state.rulesBusy) return;
    if (!app.state.pendingPackImportToken) {
      app.status.setPackTransferResult(
        app.dom.packImportResult,
        "请先选择并检查压缩包。",
        "error",
      );
      app.status.addLog("单包导入失败: 缺少导入凭证", true);
      return;
    }
    const dirty = app.state.rulesLoaded && JSON.stringify(app.state.rules) !== app.state.savedRulesSnapshot;
    if (app.dom.packImportOverwrite?.checked || dirty) {
      const overwriteMessage = app.dom.packImportOverwrite?.checked
        ? app.dom.packImportOverwriteManual?.checked
          ? "同名表情包将被覆盖，且本机人工描述、标签和图片文字也会被替换。确定继续吗？"
          : "同名表情包及其向量将被覆盖，本机人工描述、标签和图片文字会保留。"
        : "";
      const confirmed = await window.MemeUI.confirm({
        title: "确认导入表情包",
        message: [
          overwriteMessage,
          dirty ? "导入成功后将重新加载规则，当前未保存的规则更改会丢失。" : "",
        ]
          .filter(Boolean)
          .join("\n\n"),
        confirmText: "继续导入",
        danger: true,
      });
      if (!confirmed) {
        return;
      }
    }

    app.state.packImportBusy = true;
    app.state.rulesBusy = true;
    app.status.updateRulesState("正在导入表情包…");
    app.status.setLoading(app.dom.packImportConfirmBtn, "正在导入...");
    app.dom.packImportResetBtn.disabled = true;
    app.dom.packImportSetDefault.disabled = true;
    app.dom.packImportOverwrite.disabled = true;
    app.dom.packImportOverwriteManual.disabled = true;
    app.status.setPackTransferResult(
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
      app.status.setPackTransferResult(
        app.dom.packImportResult,
        `已导入 ${data?.name || importedPackId}${vectorHint}`,
        "success",
      );
      app.status.addLog(`单包导入成功: ${importedPackId || data?.name || "未知表情包"}`);
      try {
        await app.ruleIO.refreshPacksAndRules(importedPackId);
      } catch (refreshError) {
        app.status.setPackTransferResult(
          app.dom.packImportResult,
          `表情包已导入，但设置刷新失败：${
            refreshError?.message || String(refreshError)
          }。请点击「重新加载」。`,
          "error",
        );
        app.status.addLog("表情包已导入，但设置刷新失败", true);
      }
      app.dom.packImportDropzone.focus();
    } catch (error) {
      app.status.setPackTransferResult(
        app.dom.packImportResult,
        error?.message || String(error),
        "error",
      );
      app.status.addLog(`单包导入失败: ${error?.message || String(error)}`, true);
    } finally {
      app.state.packImportBusy = false;
      app.state.rulesBusy = false;
      app.status.updateRulesState();
      app.dom.packImportResetBtn.disabled = false;
      app.dom.packImportSetDefault.disabled = false;
      app.dom.packImportOverwrite.disabled = false;
      app.dom.packImportOverwriteManual.disabled = !app.dom.packImportOverwrite.checked;
      app.status.clearLoading(app.dom.packImportConfirmBtn);
    }
  }
return { resetPackImportPreview, renderPackImportInspection, stagePackImport, confirmPackImport };
}
