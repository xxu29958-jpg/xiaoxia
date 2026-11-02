
// api: one page runtime owns shared state and DOM references.
export function createApi(app) {
function getBridge() {
  if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
  try {
    if (window.parent && window.parent !== window && window.parent.AstrBotPluginPage) {
      return window.parent.AstrBotPluginPage;
    }
  } catch (error) {
    return null;
  }
  return null;
}

function canUseHttpFallback() {
  return ["http:", "https:"].includes(window.location.protocol) && typeof window.fetch === "function";
}

async function waitForBridge() {
  for (let i = 0; i < 24; i += 1) {
    const bridge = getBridge();
    if (bridge && typeof bridge.apiGet === "function" && typeof bridge.apiPost === "function") return bridge;
    await app.common.sleep(80);
  }
  return null;
}

async function apiGet(path) {
  return apiRequest(path, { method: "GET" });
}

async function apiPost(path, payload) {
  return apiRequest(path, { method: "POST", body: payload || {} });
}

async function apiRequest(path, options) {
  const method = (options.method || "GET").toUpperCase();
  const httpAvailable = canUseHttpFallback();
  const bridge = getBridge() || await waitForBridge();
  let data;
  if (bridge && typeof bridge.apiGet === "function" && typeof bridge.apiPost === "function") {
    data = await bridgeRequest(bridge, path, method, options.body);
  } else if (httpAvailable) {
    data = await httpRequest(path, method, options.body);
  } else {
    throw new Error("未检测到 AstrBot 页面桥接，且当前页面不能使用同源 Web API");
  }
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch (error) {
      throw new Error(data);
    }
  }
  if (data && data.status === "error") {
    throw new Error(data.message || data.error || "请求失败");
  }
  if (!data || data.success === false) throw new Error(data && data.error ? data.error : "请求失败");
  return guardPayload(data.data !== undefined ? data.data : data, path);
}

async function bridgeRequest(bridge, path, method, body) {
  const url = new URL(String(path || ""), "https://astrbot-plugin-page.local/");
  const endpoint = (app.values.PAGE_ENDPOINT_PREFIX + "/" + url.pathname.replace(/^\/+/, ""))
    .replace(/\/{2,}/g, "/");
  if (method === "GET") {
    const params = Object.fromEntries(url.searchParams.entries());
    return bridge.apiGet(endpoint, Object.keys(params).length ? params : undefined);
  }
  return bridge.apiPost(endpoint, body || {});
}

async function httpRequest(path, method, body) {
  const response = await fetch(app.values.API + path, {
    method,
    cache: "no-store",
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    if (!response.ok) throw new Error("请求失败（HTTP " + response.status + "）");
    throw new Error("页面 API 返回了无效 JSON");
  }
  if (!response.ok || (data && data.success === false)) {
    const error = new Error((data && data.error) || "请求失败（HTTP " + response.status + "）");
    error.status = response.status;
    throw error;
  }
  return data;
}

async function apiTry(fn, fallback) {
  try {
    return await fn();
  } catch (error) {
    return fallback;
  }
}

function guardPayload(payload, path) {
  if (typeof window === "undefined" || window.__MC_FIELD_GUARD__ !== true) return payload;
  if (payload === null || typeof payload !== "object") return payload;
  const endpoint = String(path || "").split("?")[0];
  return new Proxy(payload, {
    get(target, key, receiver) {
      if (typeof key === "string" && !(key in target)) {
        const marker = endpoint + " · " + key;
        if (!app.values.guardedFieldReads.has(marker)) {
          app.values.guardedFieldReads.add(marker);
          console.error(
            "[记忆面板] 读取了响应中不存在的字段：" + marker +
            "；字段归属以 page_api.ENDPOINT_FIELD_CONTRACT 为准。"
          );
          if (typeof window.__MC_FIELD_REPORT__ === "function") window.__MC_FIELD_REPORT__(marker);
        }
      }
      return Reflect.get(target, key, receiver);
    },
  });
}

async function initialize() {
app.values.guardedFieldReads = new Set();
}
return { getBridge, canUseHttpFallback, waitForBridge, apiGet, apiPost, apiRequest, bridgeRequest, httpRequest, apiTry, guardPayload, initialize };
}
