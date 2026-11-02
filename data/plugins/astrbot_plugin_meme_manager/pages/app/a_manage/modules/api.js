import { PreviewClient } from "../../shared/preview.js";

// api: one page runtime owns shared state and DOM references.
export function createApi(app) {
async function apiGet(endpoint, params = {}, previewOptions = {}) {
    const mergedParams = { ...params };
    const managedPackId = String(
      app.state.activeManagePackId || app.dom.managePackSelect?.value || "",
    ).trim();
    if (
      managedPackId &&
      [
        "emoji",
        "emotions",
        "meme_image",
        "meme_image_data",
        "meme_image_semantic",
        "semantic/reviews",
        "img_host/sync/status",
        "img_host/sync/task_status",
      ].includes(endpoint)
    ) {
      mergedParams.managed_pack_id = managedPackId;
    }
    if (
      ["meme_image_data", "auto-collect/inbox/image_data"].includes(endpoint)
    ) {
      return app.state.previews.get(endpoint, mergedParams, previewOptions);
    }
    return await window.AstrBotPluginPage.apiGet(endpoint, mergedParams);
  }

async function apiPost(endpoint, body = {}) {
    const mergedBody = { ...body };
    const selectedPackId = String(
      app.state.activeManagePackId || app.dom.managePackSelect?.value || "",
    ).trim();
    if (
      selectedPackId &&
      app.state.defaultManagePackId &&
      selectedPackId !== app.state.defaultManagePackId &&
      ["emoji/", "category/"].some((prefix) => endpoint.startsWith(prefix))
    ) {
      throw new Error(
        "当前为管理视图模式，仅支持浏览。请切回默认管理包后再执行编辑操作。",
      );
    }
    if (selectedPackId && endpoint.startsWith("img_host/sync/")) {
      mergedBody.managed_pack_id = selectedPackId;
    }
    if (selectedPackId && endpoint.startsWith("semantic/")) {
      mergedBody.pack_id = selectedPackId;
    }
    const result = await window.AstrBotPluginPage.apiPost(endpoint, mergedBody);
    app.state.previews.invalidate();
    return result;
  }

async function parseResponsePayload(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }

    const text = await response.text();
    return {
      message:
        text.startsWith("<!DOCTYPE") || text.startsWith("<html")
          ? "服务器返回了错误页面，请联系管理员"
          : text,
    };
  }

async function requestJson(
    url,
    options = {},
    { defaultErrorMessage = "请求失败" } = {},
  ) {
    const response = await fetch(url, options);
    const payload = await parseResponsePayload(response).catch(() => ({}));

    if (!response.ok) {
      const error = new Error(
        payload.message || payload.error || defaultErrorMessage,
      );
      error.status = response.status;
      error.code = payload.code || null;
      error.payload = payload;
      throw error;
    }

    return payload;
  }

async function refreshUi({
    emojis = false,
    syncStatus = false,
    imgHostStatus = false,
  } = {}) {
    if (emojis) {
      await Promise.all([
        app.library.fetchEmojis({ propagateError: true }),
        app.packCatalog.refreshManagePackSummaries(),
      ]);
    }
    if (syncStatus) {
      await app.sync.checkSyncStatus(false);
    }
    if (imgHostStatus) {
      await app.sync.checkImgHostSyncStatus(false);
    }
  }

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

async function initialize0() {
await window.AstrBotPluginPage.ready();
window.AstrBotPluginPage.getContext();
app.state.previews = new PreviewClient(window.AstrBotPluginPage);
({ withCurrentAuthParams: app.state.withCurrentAuthParams, ensureNavAuthToken: app.state.ensureNavAuthToken, applySecureNavLinks: app.state.applySecureNavLinks } = window.MemeUI);
await app.state.applySecureNavLinks();
app.state.managedPackIdFromUrl = String(
    new URLSearchParams(window.location.search).get("managed_pack_id") || "",
  ).trim();
}

async function initialize3() {
app.state.scrollLockCount = 0;
app.state.scrollLockSavedY = 0;
}
return { apiGet, apiPost, parseResponsePayload, requestJson, refreshUi, sleep, initialize0, initialize3 };
}
