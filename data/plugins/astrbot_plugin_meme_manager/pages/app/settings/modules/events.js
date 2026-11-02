
// events: one page runtime owns shared state and DOM references.
export function createEvents(app) {
async function initialize1() {
app.dom.addRuleBtn.addEventListener("click", () => {
    app.state.rules.splice(Math.max(app.state.rules.length - 1, 0), 0, app.ruleIO.buildNewRule("persona"));
    app.ruleView.renderRules();
    const targetInput = app.dom.rulesList.children[
      Math.max(app.state.rules.length - 2, 0)
    ]?.querySelector('input[data-role="target"]');
    targetInput?.focus();
  });
app.dom.reloadRulesBtn.addEventListener("click", async () => {
    if (app.state.rulesBusy) return;
    if (app.state.rulesLoaded && JSON.stringify(app.state.rules) !== app.state.savedRulesSnapshot) {
      const confirmed = await window.MemeUI.confirm({
        title: "重新加载规则",
        message:
          "当前有未保存的更改。重新加载会丢弃这些更改，恢复到上次保存的规则。",
        confirmText: "丢弃并重新加载",
        danger: true,
      });
      if (!confirmed) return;
    }
    app.state.rulesBusy = true;
    app.status.updateRulesState("正在重新加载规则…");
    app.status.setLoading(app.dom.reloadRulesBtn, "加载中...");
    let reloadError = "";
    try {
      await app.ruleIO.refreshPacksAndRules();
      app.status.addLog("规则已重新加载");
    } catch (error) {
      reloadError = `加载失败：${error?.message || String(error)}。请重试。`;
      app.status.addLog(`重新加载失败: ${error?.message || String(error)}`, true);
    } finally {
      app.state.rulesBusy = false;
      app.status.clearLoading(app.dom.reloadRulesBtn);
      app.status.updateRulesState(
        reloadError || "规则已重新加载",
        reloadError ? "error" : "success",
      );
    }
  });
app.dom.saveRulesBtn.addEventListener("click", () => {
    void app.ruleIO.saveRules();
  });
app.dom.exportBackupBtn.addEventListener("click", () => {
    void app.backup.exportBackup();
  });
app.dom.importBackupBtn.addEventListener("click", () => {
    void app.backup.importBackup();
  });
app.dom.transferPackSelect?.addEventListener("change", () => {
    app.state.activeTransferPackId = String(app.dom.transferPackSelect.value || "").trim();
    void app.export.refreshPackExportCapability(app.state.activeTransferPackId);
  });
app.dom.exportModeInputs.forEach((input) => {
    input.addEventListener("change", app.export.updateExportModeAppearance);
  });
app.dom.exportPackDownloadBtn?.addEventListener("click", () => {
    void app.export.downloadCurrentPack();
  });
app.dom.packImportFile?.addEventListener("change", (event) => {
    const file = event.target?.files?.[0];
    void app.import.stagePackImport(file);
  });
["dragenter", "dragover"].forEach((eventName) => {
    app.dom.packImportDropzone?.addEventListener(eventName, (event) => {
      event.preventDefault();
      app.dom.packImportDropzone.classList.add("dragover");
    });
  });
["dragleave", "drop"].forEach((eventName) => {
    app.dom.packImportDropzone?.addEventListener(eventName, (event) => {
      event.preventDefault();
      app.dom.packImportDropzone.classList.remove("dragover");
    });
  });
app.dom.packImportDropzone?.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    void app.import.stagePackImport(file);
  });
app.dom.packImportResetBtn?.addEventListener("click", () => {
    if (app.state.packImportBusy) return;
    app.import.resetPackImportPreview();
    app.dom.packImportDropzone.focus();
  });
app.dom.packImportOverwrite?.addEventListener("change", () => {
    app.dom.packImportOverwriteManual.disabled = !app.dom.packImportOverwrite.checked;
    if (!app.dom.packImportOverwrite.checked) app.dom.packImportOverwriteManual.checked = false;
  });
app.dom.packImportDropzone?.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && !app.state.packImportBusy) {
      event.preventDefault();
      app.dom.packImportFile.click();
    }
  });
window.addEventListener("beforeunload", (event) => {
    if (
      !app.state.allowPageLeave &&
      (app.state.packImportBusy ||
        window.MemeSettings?.dirty ||
        window.MemeSettings?.busy ||
        Boolean(app.dom.saveRulesBtn.dataset.originalHtml) ||
        Boolean(app.dom.importBackupBtn.dataset.originalHtml) ||
        (app.state.rulesLoaded && JSON.stringify(app.state.rules) !== app.state.savedRulesSnapshot))
    ) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
document.addEventListener(
    "click",
    async (event) => {
      const link = event.target.closest("a[href]");
      if (
        !link ||
        link.target === "_blank" ||
        link.hasAttribute("download") ||
        event.button !== 0 ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const destination = new URL(link.href, window.location.href);
      if (
        destination.origin === window.location.origin &&
        destination.pathname === window.location.pathname &&
        destination.search === window.location.search &&
        destination.hash
      )
        return;
      const rulesDirty =
        app.state.rulesLoaded && JSON.stringify(app.state.rules) !== app.state.savedRulesSnapshot;
      if (!rulesDirty && !window.MemeSettings?.dirty) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (window.MemeSettings?.busy) {
        window.MemeUI.toast("设置正在处理，请稍候再离开。", "info");
        return;
      }
      const confirmed = await window.MemeUI.confirm({
        title: "离开设置页面",
        message: "设置还有未保存的更改。离开后这些更改会丢失。",
        confirmText: "丢弃更改并离开",
        danger: true,
      });
      if (confirmed) {
        app.state.allowPageLeave = true;
        window.location.assign(link.href);
      }
    },
    true,
  );
app.dom.packImportConfirmBtn?.addEventListener("click", () => {
    void app.import.confirmPackImport();
  });
app.export.updateExportModeAppearance();
app.import.resetPackImportPreview();
app.state.rulesBusy = true;
app.status.updateRulesState();
app.state.initialError = "";
try {
    await app.ruleIO.refreshPacksAndRules();
    app.status.addLog("设置中心已就绪");
  } catch (error) {
    app.state.initialError = `加载失败：${
      error?.message || String(error)
    }。请点击「重新加载」重试。`;
    app.dom.rulesList.replaceChildren();
    const placeholder = document.createElement("p");
    placeholder.className = "rules-placeholder";
    placeholder.textContent = "暂时无法读取规则。你的已保存设置未受影响。";
    app.dom.rulesList.appendChild(placeholder);
    app.status.setPackTransferResult(
      app.dom.transferCurrentPack,
      "暂时无法读取表情包，请重新加载。",
      "error",
    );
    app.dom.exportPackDownloadBtn.disabled = true;
    app.status.addLog(`初始化失败: ${error?.message || String(error)}`, true);
  } finally {
    app.state.rulesBusy = false;
    app.status.updateRulesState(app.state.initialError, app.state.initialError ? "error" : "");
  }
}
return { initialize1 };
}
