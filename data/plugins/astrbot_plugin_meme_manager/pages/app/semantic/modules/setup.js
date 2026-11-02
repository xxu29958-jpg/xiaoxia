import { PreviewClient } from "../../shared/preview.js";
// setup: one page runtime owns shared state and DOM references.
export function createSetup(app) {
async function initialize0() {
app.dom.toastContainer = document.querySelector("#toast-container");
app.dom.notice = document.querySelector("#notice");
app.dom.otherTasksWarning = document.querySelector("#other-tasks-warning");
app.dom.packSelect = document.querySelector("#pack");
app.dom.statusBox = document.querySelector("#status");
app.dom.statusError = document.querySelector("#status-error");
app.dom.statusRetry = document.querySelector("#status-retry");
app.dom.statusRefreshLabel = document.querySelector("#status-refresh-label");
app.dom.taskState = document.querySelector("#task-state");
app.dom.taskProgress = document.querySelector("#task-progress");
app.dom.taskProgressLabel = document.querySelector("#task-progress-label");
app.dom.taskGuidance = document.querySelector("#task-guidance");
app.dom.recordsBox = document.querySelector("#items");
app.dom.recordCount = document.querySelector("#record-count");
app.dom.recordsPrev = document.querySelector("#records-prev");
app.dom.recordsNext = document.querySelector("#records-next");
app.dom.recordsPage = document.querySelector("#records-page");
app.dom.recordsFilter = document.querySelector("#records-filter");
app.dom.concurrencyInput = document.querySelector("#concurrency");
app.dom.concurrencyHint = document.querySelector("#concurrency-hint");
app.dom.taskTimer = document.querySelector("#task-timer");
app.dom.imagePreviewMask = document.querySelector("#image-preview-mask");
app.dom.imagePreviewTitle = document.querySelector("#image-preview-title");
app.dom.imagePreviewClose = document.querySelector("#image-preview-close");
app.dom.imagePreviewImg = document.querySelector("#image-preview-img");
app.dom.imagePreviewLoading = document.querySelector("#image-preview-loading");
app.dom.imagePreviewRetry = document.querySelector("#image-preview-retry");
app.dom.autoInboxPanel = document.querySelector("#auto-inbox-panel");
app.dom.autoInboxCount = document.querySelector("#auto-inbox-count");
app.dom.autoInboxItems = document.querySelector("#auto-inbox-items");
app.dom.autoInboxSemanticize = document.querySelector(
    "#auto-inbox-semanticize",
  );
app.dom.buttons = Array.from(document.querySelectorAll("button[data-action]"));
app.state.requestRunning = false;
app.state.embeddingReady = false;
app.state.visionReady = false;
app.state.captionComplete = false;
app.state.toastTimer = null;
app.state.lastReportedError = "";
app.state.lastActionError = "";
app.state.packsLoaded = false;
app.state.statusLoading = false;
app.state.lastRecordsSnapshot = "";
app.state.recordsCurrentPage = 1;
app.state.recordsPageSize = 20;
app.state.recordsTotalPages = 1;
app.state.recordsStatus = "all";
app.state.elapsedSeconds = 0;
app.state.timerRunning = false;
app.state.timerUpdatedAt = Date.now();
app.state.concurrencyDirty = false;
app.state.latestStatus = null;
app.state.statusRequestSequence = 0;
app.state.previewCache = new Map();
app.state.previewRequests = new Map();
app.state.visibleRecordPaths = new Set();
app.state.activePreviewKey = "";
app.state.activePreviewItem = null;
app.state.previewVersion = 0;
app.state.pendingAutoInboxCount = 0;
}
return { initialize0 };
}
