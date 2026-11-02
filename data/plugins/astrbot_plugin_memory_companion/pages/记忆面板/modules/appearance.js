
// appearance: one page runtime owns shared state and DOM references.
export function createAppearance(app) {
function applyPalette(key) {
  const value = app.common.compact(key).toLowerCase();
  if (app.values.PALETTE_KEYS.has(value)) {
    document.documentElement.dataset.palette = value;
    return value;
  }
  delete document.documentElement.dataset.palette;
  return "";
}

async function syncPalette() {
  const payload = await app.api.apiTry(() => app.api.apiGet("/ui-preferences"), null);
  if (!payload) return;
  applyPalette(payload.palette);
}

function applyTheme(theme) {
  app.values.state.theme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = app.values.state.theme;
  const label = app.values.$("#themeToggleLabel");
  if (label) label.textContent = app.values.state.theme === "dark" ? "深色" : "浅色";
  try {
    window.localStorage.setItem(app.values.THEME_KEY, app.values.state.theme);
  } catch (error) {
    /* 忽略存储禁用 */
  }
  window.dispatchEvent(new CustomEvent("mc:theme", { detail: app.values.state.theme }));
}

async function initialize() {
app.values.PALETTE_KEYS = new Set([
  "huangbaiyou", "tianpiao", "haitianxia", "yingying", "oubi", "qingming",
  "zipu", "shanlan", "qielan", "tuihong", "congqing", "yuebai", "mocan", "gupiao",
]);
}
return { applyPalette, syncPalette, applyTheme, initialize };
}
