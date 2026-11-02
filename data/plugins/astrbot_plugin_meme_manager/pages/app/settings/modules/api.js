
// api: one page runtime owns shared state and DOM references.
export function createApi(app) {
async function apiGet(endpoint, params = {}) {
    return window.AstrBotPluginPage.apiGet(endpoint, params);
  }

async function apiPost(endpoint, body = {}) {
    return window.AstrBotPluginPage.apiPost(endpoint, body);
  }
return { apiGet, apiPost };
}
