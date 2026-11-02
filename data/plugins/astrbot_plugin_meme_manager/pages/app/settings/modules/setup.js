
// setup: one page runtime owns shared state and DOM references.
export function createSetup(app) {
async function initialize0() {
await window.AstrBotPluginPage.ready();
({ applySecureNavLinks: app.state.applySecureNavLinks } = window.MemeUI);
await app.state.applySecureNavLinks();
app.dom.rulesList = document.getElementById("rules-list");
app.dom.addRuleBtn = document.getElementById("add-rule-btn");
app.dom.reloadRulesBtn = document.getElementById("reload-rules-btn");
app.dom.saveRulesBtn = document.getElementById("save-rules-btn");
app.dom.rulesValidation = document.getElementById("rules-validation");
app.dom.rulesSaveStatus = document.getElementById("rules-save-status");
app.dom.backupOutputDirInput = document.getElementById(
    "backup-output-dir-input",
  );
app.dom.exportBackupBtn = document.getElementById("export-backup-btn");
app.dom.exportResult = document.getElementById("export-result");
app.dom.backupFileInput = document.getElementById("backup-file-input");
app.dom.importOverwriteCheckbox = document.getElementById(
    "import-overwrite-checkbox",
  );
app.dom.importBackupBtn = document.getElementById("import-backup-btn");
app.dom.importResult = document.getElementById("import-result");
app.dom.transferPackSelect = document.getElementById("transfer-pack-select");
app.dom.transferCurrentPack = document.getElementById("transfer-current-pack");
app.dom.exportModeInputs = Array.from(
    document.querySelectorAll('input[name="export-mode"]'),
  );
app.dom.exportModeBackup = document.getElementById("export-mode-backup");
app.dom.vectorBackupHint = document.getElementById("vector-backup-hint");
app.dom.exportPackDownloadBtn = document.getElementById(
    "export-pack-download-btn",
  );
app.dom.exportPackResult = document.getElementById("export-pack-result");
app.dom.packImportDropzone = document.getElementById("pack-import-dropzone");
app.dom.packImportFile = document.getElementById("pack-import-file");
app.dom.packImportFileLabel = document.getElementById("pack-import-file-label");
app.dom.packImportPreview = document.getElementById("pack-import-preview");
app.dom.packImportPreviewName = document.getElementById(
    "pack-import-preview-name",
  );
app.dom.packImportPreviewFormat = document.getElementById(
    "pack-import-preview-format",
  );
app.dom.packImportImageCount = document.getElementById(
    "pack-import-image-count",
  );
app.dom.packImportCategoryCount = document.getElementById(
    "pack-import-category-count",
  );
app.dom.packImportSemanticCount = document.getElementById(
    "pack-import-semantic-count",
  );
app.dom.packImportVectorState = document.getElementById(
    "pack-import-vector-state",
  );
app.dom.packImportWarning = document.getElementById("pack-import-warning");
app.dom.packImportSetDefault = document.getElementById(
    "pack-import-set-default",
  );
app.dom.packImportOverwrite = document.getElementById("pack-import-overwrite");
app.dom.packImportOverwriteManual = document.getElementById(
    "pack-import-overwrite-manual",
  );
app.dom.packImportResetBtn = document.getElementById("pack-import-reset-btn");
app.dom.packImportConfirmBtn = document.getElementById(
    "pack-import-confirm-btn",
  );
app.dom.packImportResult = document.getElementById("pack-import-result");
app.dom.logList = document.getElementById("log-list");
app.state.installedPacks = [];
app.state.rules = [];
app.state.savedRulesSnapshot = "";
app.state.rulesLoaded = false;
app.state.rulesBusy = false;
app.state.allowPageLeave = false;
app.state.dragRuleIndex = -1;
app.state.personaTargets = [];
app.state.sessionTargets = [];
app.state.migrationPacksById = new Map();
app.state.activeTransferPackId = "";
app.state.pendingPackImportToken = "";
app.state.exportCapabilityRequestId = 0;
app.state.packImportBusy = false;
}
return { initialize0 };
}
