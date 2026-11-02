
// export: one page runtime owns shared state and DOM references.
export function createExport(app) {
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

function syncTransferPackOptions(preferredPackId = "") {
    if (!app.dom.transferPackSelect) {
      return "";
    }
    app.dom.transferPackSelect.innerHTML = "";
    app.dom.transferPackSelect.disabled = app.state.installedPacks.length === 0;
    if (!app.state.installedPacks.length) {
      const option = document.createElement("option");
      option.textContent = "暂无表情包";
      option.value = "";
      app.dom.transferPackSelect.appendChild(option);
    }
    app.state.installedPacks.forEach((pack) => {
      const packId = String(pack?.id || "").trim();
      const packName = String(pack?.name || packId || "未命名");
      const count = Number(pack?.image_count || 0);
      const option = document.createElement("option");
      option.value = packId;
      option.textContent = `${packName} (${count} 张)`;
      app.dom.transferPackSelect.appendChild(option);
    });

    const candidateIds = new Set(
      app.state.installedPacks.map((item) => String(item?.id || "").trim()),
    );
    const nextPackId =
      (preferredPackId && candidateIds.has(String(preferredPackId).trim())
        ? String(preferredPackId).trim()
        : candidateIds.has(app.state.activeTransferPackId)
        ? app.state.activeTransferPackId
        : String(app.state.installedPacks[0]?.id || "").trim()) || "";

    app.state.activeTransferPackId = nextPackId;
    app.dom.transferPackSelect.value = nextPackId;
    return nextPackId;
  }

async function refreshPackExportCapability(packId = app.state.activeTransferPackId) {
    const normalizedPackId = String(packId || "").trim();
    const requestId = ++app.state.exportCapabilityRequestId;
    const pack = app.state.migrationPacksById.get(normalizedPackId);

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
    if (app.dom.vectorBackupHint) {
      app.dom.vectorBackupHint.textContent = "正在检查当前表情包的向量状态…";
    }
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
      app.status.addLog(`读取单包导出能力失败: ${error?.message || String(error)}`, true);
    } finally {
      if (requestId === app.state.exportCapabilityRequestId) {
        updateExportModeAppearance();
      }
    }
  }

async function downloadCurrentPack() {
    const packId = String(app.state.activeTransferPackId || "").trim();
    if (!packId) {
      app.status.setPackTransferResult(
        app.dom.exportPackResult,
        "当前没有可导出的表情包。",
        "error",
      );
      app.status.addLog("当前没有可导出的表情包", true);
      return;
    }
    const mode = selectedExportMode();
    app.status.setLoading(app.dom.exportPackDownloadBtn, "正在生成压缩包...");
    app.dom.transferPackSelect.disabled = true;
    app.dom.exportModeInputs.forEach((input) => {
      input.disabled = true;
    });
    app.status.setPackTransferResult(
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
      app.status.setPackTransferResult(
        app.dom.exportPackResult,
        `${label}已生成，并已开始下载。`,
        "success",
      );
      app.status.addLog(`单包导出成功: ${packId} (${label})`);
    } catch (error) {
      app.status.setPackTransferResult(
        app.dom.exportPackResult,
        error?.message || String(error),
        "error",
      );
      app.status.addLog(`单包导出失败: ${error?.message || String(error)}`, true);
    } finally {
      app.status.clearLoading(app.dom.exportPackDownloadBtn);
      app.dom.transferPackSelect.disabled = app.state.installedPacks.length === 0;
      document.getElementById("export-mode-share").disabled = false;
      await refreshPackExportCapability(app.state.activeTransferPackId);
    }
  }
return { selectedExportMode, updateExportModeAppearance, syncTransferPackOptions, refreshPackExportCapability, downloadCurrentPack };
}
