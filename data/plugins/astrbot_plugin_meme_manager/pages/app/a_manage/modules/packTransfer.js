
// packTransfer: one page runtime owns shared state and DOM references.
export function createPackTransfer(app) {
function formatPackOptionLabel(pack) {
    const name = String(pack?.name || pack?.id || "未命名");
    const id = String(pack?.id || "").trim();
    const semanticStatus = String(pack?.semantic_status || "none");
    const semanticLabel =
      semanticStatus === "complete"
        ? "已语义化"
        : semanticStatus === "partial"
          ? "部分语义化"
          : "";
    return `${name} (${id})${semanticLabel ? `（${semanticLabel}）` : ""}`;
  }

function setPackTransferResult(element, message = "", type = "") {
    if (!element) {
      return;
    }
    element.textContent = String(message || "");
    element.classList.toggle("success", type === "success");
    element.classList.toggle("error", type === "error");
  }

function selectedExportMode() {
    return app.dom.exportModeInputs.find((input) => input.checked)?.value || "share";
  }

function updateExportModeAppearance() {
    app.dom.exportModeInputs.forEach((input) => {
      const option = input.closest(".export-mode-option");
      option?.classList.toggle("selected", input.checked);
      option?.classList.toggle("disabled", input.disabled);
    });
    if (app.dom.exportPackDownloadBtn) {
      app.dom.exportPackDownloadBtn.innerHTML =
        selectedExportMode() === "backup"
          ? '<i class="fas fa-download icon"></i>下载自用备份'
          : '<i class="fas fa-download icon"></i>下载分享版';
    }
  }

async function refreshPackExportCapability(packId = app.state.activeManagePackId) {
    const normalizedPackId = String(packId || "").trim();
    const requestId = ++app.state.exportCapabilityRequestId;
    const pack = app.state.managePacksById.get(normalizedPackId);
    if (app.dom.transferCurrentPack) {
      app.dom.transferCurrentPack.textContent = pack
        ? `当前：${pack.name || pack.id}（${Number(pack.image_count || 0)} 张）`
        : normalizedPackId
          ? `当前：${normalizedPackId}`
          : "暂无可导出的表情包";
    }
    if (!normalizedPackId) {
      if (app.dom.exportPackDownloadBtn) app.dom.exportPackDownloadBtn.disabled = true;
      if (app.dom.exportModeBackup) app.dom.exportModeBackup.disabled = true;
      if (app.dom.vectorBackupHint)
        app.dom.vectorBackupHint.textContent = "当前没有可导出的表情包。";
      updateExportModeAppearance();
      return;
    }

    if (app.dom.exportPackDownloadBtn) app.dom.exportPackDownloadBtn.disabled = false;
    if (app.dom.exportModeBackup) {
      if (app.dom.exportModeBackup.checked) {
        const shareInput = document.getElementById("export-mode-share");
        if (shareInput) shareInput.checked = true;
      }
      app.dom.exportModeBackup.disabled = true;
    }
    if (app.dom.vectorBackupHint)
      app.dom.vectorBackupHint.textContent = "正在检查当前表情包的向量状态…";
    updateExportModeAppearance();
    try {
      const status = await app.api.apiGet("packs/export/status", {
        pack_id: normalizedPackId,
      });
      if (requestId !== app.state.exportCapabilityRequestId) {
        return;
      }
      const available = Boolean(status?.vector_backup_available);
      if (app.dom.exportModeBackup) app.dom.exportModeBackup.disabled = !available;
      if (!available && app.dom.exportModeBackup?.checked) {
        const shareInput = document.getElementById("export-mode-share");
        if (shareInput) shareInput.checked = true;
      }
      if (app.dom.vectorBackupHint) {
        const modelHint = [
          String(status?.embedding_model || "").trim(),
          Number(status?.embedding_dimension || 0)
            ? `${Number(status.embedding_dimension)} 维`
            : "",
        ]
          .filter(Boolean)
          .join("；");
        app.dom.vectorBackupHint.textContent = available
          ? `包含完整本机向量${
              modelHint ? `（${modelHint}）` : ""
            }，适合迁回相同模型环境。`
          : "当前没有完整向量；完成语义化并建立索引后才可导出。";
      }
    } catch (error) {
      if (requestId !== app.state.exportCapabilityRequestId) {
        return;
      }
      if (app.dom.exportModeBackup) app.dom.exportModeBackup.disabled = true;
      if (app.dom.vectorBackupHint) {
        app.dom.vectorBackupHint.textContent = "暂时无法读取向量状态，请稍后重试。";
      }
    } finally {
      if (requestId === app.state.exportCapabilityRequestId) {
        updateExportModeAppearance();
      }
    }
  }

async function downloadCurrentPack() {
    const packId = String(app.state.activeManagePackId || "").trim();
    if (!packId) {
      app.dialogs.showToast("当前没有可导出的表情包。", "warning", "无法导出");
      return;
    }
    const mode = selectedExportMode();
    app.widgets.setButtonBusy(app.dom.exportPackDownloadBtn, "正在生成压缩包…");
    setPackTransferResult(
      app.dom.exportPackResult,
      "正在整理文件，请不要关闭页面。",
      "",
    );
    try {
      await window.AstrBotPluginPage.download("packs/export/download", {
        pack_id: packId,
        mode,
      });
      const label = mode === "backup" ? "带向量自用备份" : "无向量分享版";
      setPackTransferResult(
        app.dom.exportPackResult,
        `${label}已生成，并已开始下载。`,
        "success",
      );
      app.dialogs.showToast(`${label}已开始下载。`, "success", "导出成功");
    } catch (error) {
      setPackTransferResult(
        app.dom.exportPackResult,
        error?.message || String(error),
        "error",
      );
      app.dialogs.showToast(error?.message || String(error), "error", "导出失败");
    } finally {
      app.widgets.restoreButton(app.dom.exportPackDownloadBtn);
      updateExportModeAppearance();
    }
  }
return { formatPackOptionLabel, setPackTransferResult, selectedExportMode, updateExportModeAppearance, refreshPackExportCapability, downloadCurrentPack };
}
