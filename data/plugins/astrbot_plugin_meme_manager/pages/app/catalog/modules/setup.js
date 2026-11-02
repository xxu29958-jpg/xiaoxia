import { setupCatalogUpdates } from "../updates.js";
// setup: one page runtime owns shared state and DOM references.
export function createSetup(app) {
async function initialize0() {
await window.AstrBotPluginPage.ready();
app.state.FIXED_INDEX_URL = "https://raw.githubusercontent.com/anka-afk/astrbot-meme-pack-index/main/community-index.json";
({ applySecureNavLinks: app.state.applySecureNavLinks } = window.MemeUI);
app.dom.sourceRepoInput = document.getElementById("source-repo-input");
app.dom.sourceInstallForm = document.getElementById("source-install-form");
app.dom.sourceRefInput = document.getElementById("source-ref-input");
app.dom.sourceSubpathInput = document.getElementById("source-subpath-input");
app.dom.installSourceBtn = document.getElementById("install-source-btn");
app.dom.installDialog = document.getElementById("install-dialog");
app.dom.installDialogPackName = document.getElementById(
    "install-dialog-pack-name",
  );
app.dom.installDialogOverwriteCheckbox = document.getElementById(
    "install-overwrite-checkbox",
  );
app.dom.installDialogDefaultCheckbox = document.getElementById(
    "install-default-checkbox",
  );
app.dom.installDialogCancel = document.getElementById("install-dialog-cancel");
app.dom.installDialogConfirm = document.getElementById(
    "install-dialog-confirm",
  );
app.dom.installProgressDialog = document.getElementById(
    "install-progress-dialog",
  );
app.dom.installProgressPackName = document.getElementById(
    "install-progress-pack-name",
  );
app.dom.installProgressPhase = document.getElementById(
    "install-progress-phase",
  );
app.dom.installProgressPercent = document.getElementById(
    "install-progress-percent",
  );
app.dom.installProgressTrack = document.getElementById(
    "install-progress-track",
  );
app.dom.installProgressBar = document.getElementById("install-progress-bar");
app.dom.installProgressBytes = document.getElementById(
    "install-progress-bytes",
  );
app.dom.installProgressCancel = document.getElementById(
    "install-progress-cancel",
  );
app.dom.officialGrid = document.getElementById("official-grid");
app.dom.communityGrid = document.getElementById("community-grid");
app.dom.updatesZone = document.getElementById("updates-zone");
app.dom.updatesGrid = document.getElementById("updates-grid");
app.dom.updatesPackCount = document.getElementById("updates-pack-count");
app.dom.officialPackCount = document.getElementById("official-pack-count");
app.dom.communityPackCount = document.getElementById("community-pack-count");
app.dom.logList = document.getElementById("log-list");
app.dom.catalogSearch = document.getElementById("catalog-search");
app.dom.catalogFilter = document.getElementById("catalog-filter");
app.dom.catalogStatus = document.getElementById("catalog-status");
app.dom.installedStatus = document.getElementById("installed-status");
app.dom.refreshCatalogBtn = document.getElementById("refresh-catalog-btn");
app.dom.installTask = document.getElementById("install-task");
app.dom.installTaskName = document.getElementById("install-task-name");
app.dom.installTaskStatus = document.getElementById("install-task-status");
await app.state.applySecureNavLinks();
app.state.cachedIndex = null;
app.state.installedPackIds = new Set();
app.state.pendingInstallAction = null;
app.state.activeInstallJobId = "";
app.state.installBusy = true;
app.state.installedStateKnown = false;
app.state.indexLoading = false;
app.state.updateStates = {};
app.state.installedRequest = 0;
app.state.updates = setupCatalogUpdates({
    apiGet: app.api.apiGet,
    apiPost: app.api.apiPost,
    onBusy: app.install.setInstallBusy,
    async onDone() {
      await app.catalog.refreshInstalledSet();
      app.catalog.renderCatalog();
    },
  });
}
return { initialize0 };
}
