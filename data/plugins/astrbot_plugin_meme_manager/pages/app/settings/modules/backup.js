
// backup: one page runtime owns shared state and DOM references.
export function createBackup(app) {
async function exportBackup() {
    app.status.setLoading(app.dom.exportBackupBtn, "导出中...");
    app.status.setPackTransferResult(app.dom.exportResult, "正在创建备份…");
    try {
      const outputDir = String(app.dom.backupOutputDirInput.value || "").trim();
      const response = await app.api.apiPost("settings/backup/export", {
        output_dir: outputDir || undefined,
      });
      app.status.setPackTransferResult(
        app.dom.exportResult,
        `已保存到服务器：${response.archive_path || ""}`,
        "success",
      );
      app.status.addLog(`备份导出成功: ${response.archive_path || ""}`);
    } catch (error) {
      app.status.setPackTransferResult(
        app.dom.exportResult,
        `导出失败: ${error?.message || String(error)}`,
        "error",
      );
      app.status.addLog(`备份导出失败: ${error?.message || String(error)}`, true);
    } finally {
      app.status.clearLoading(app.dom.exportBackupBtn);
    }
  }

async function importBackup() {
    if (app.state.rulesBusy || app.state.packImportBusy) return;
    const file = app.dom.backupFileInput.files?.[0];
    if (!file) {
      app.status.setPackTransferResult(app.dom.importResult, "请先选择备份 zip 文件。", "error");
      app.dom.backupFileInput.focus();
      app.status.addLog("请先选择备份 zip 文件", true);
      return;
    }
    if (!file.name.toLowerCase().endsWith(".zip")) {
      app.status.setPackTransferResult(
        app.dom.importResult,
        "请选择 zip 格式的备份文件。",
        "error",
      );
      return;
    }
    const dirty = app.state.rulesLoaded && JSON.stringify(app.state.rules) !== app.state.savedRulesSnapshot;
    const confirmed = await window.MemeUI.confirm({
      title: "恢复全量备份",
      message: `将从「${file.name}」恢复表情包与运行时设置。${
        app.dom.importOverwriteCheckbox.checked
          ? "全部现有表情包和向量索引会被备份替换，备份中没有的表情包也会被移除。"
          : "保留现有表情包，仅添加缺少的表情包；运行时设置仍会从备份恢复。"
      }${
        dirty
          ? "\n\n恢复成功后将重新加载规则，当前未保存的规则更改会丢失。"
          : ""
      }`,
      confirmText: "确认恢复",
      danger: true,
    });
    if (!confirmed) return;

    app.state.rulesBusy = true;
    app.status.updateRulesState("正在恢复备份…");
    app.status.setLoading(app.dom.importBackupBtn, "导入中...");
    app.dom.backupFileInput.disabled = true;
    app.dom.importOverwriteCheckbox.disabled = true;
    app.status.setPackTransferResult(app.dom.importResult, "正在恢复备份，请不要关闭页面…");
    try {
      const bytes = await file.arrayBuffer();
      let binary = "";
      const view = new Uint8Array(bytes);
      const chunkSize = 0x8000;
      for (let offset = 0; offset < view.length; offset += chunkSize) {
        const chunk = view.subarray(offset, offset + chunkSize);
        binary += String.fromCharCode(...chunk);
      }
      const response = await app.api.apiPost("settings/backup/import", {
        overwrite: app.dom.importOverwriteCheckbox.checked,
        file_name: file.name,
        file_b64: btoa(binary),
      });
      app.status.setPackTransferResult(
        app.dom.importResult,
        `恢复成功：${response?.restored_packs ?? 0} 个表情包`,
        "success",
      );
      app.dom.backupFileInput.value = "";
      app.status.addLog(`备份导入成功，恢复 ${response?.restored_packs ?? 0} 个 pack`);
      try {
        await app.ruleIO.refreshPacksAndRules();
      } catch (refreshError) {
        app.status.setPackTransferResult(
          app.dom.importResult,
          `备份已恢复，但设置刷新失败：${
            refreshError?.message || String(refreshError)
          }。请点击「重新加载」。`,
          "error",
        );
        app.status.addLog("备份已恢复，但设置刷新失败", true);
      }
    } catch (error) {
      app.status.setPackTransferResult(
        app.dom.importResult,
        `恢复失败: ${error?.message || String(error)}`,
        "error",
      );
      app.status.addLog(`备份导入失败: ${error?.message || String(error)}`, true);
    } finally {
      app.state.rulesBusy = false;
      app.status.updateRulesState();
      app.dom.backupFileInput.disabled = false;
      app.dom.importOverwriteCheckbox.disabled = false;
      app.status.clearLoading(app.dom.importBackupBtn);
    }
  }
return { exportBackup, importBackup };
}
