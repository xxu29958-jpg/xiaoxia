
// common: one page runtime owns shared state and DOM references.
export function createCommon(app) {
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function compact(value, fallback) {
  const text = String(value ?? "").trim();
  return text || (fallback === undefined ? "" : fallback);
}

function num(value, digits) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "-";
  const fixed = digits === undefined ? parsed.toFixed(2) : parsed.toFixed(digits);
  return fixed.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}

function int(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.round(parsed) : 0;
}

function fmtInt(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "-";
  return Math.round(parsed).toLocaleString("zh-CN");
}

function fmtTime(value) {
  const text = compact(value);
  if (!text) return "-";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString("zh-CN", {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDate(value) {
  const text = compact(value);
  if (!text) return "-";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function shortId(value) {
  const text = compact(value);
  if (!text) return "-";
  if (text.length <= 16) return text;
  return text.slice(0, 8) + "…" + text.slice(-4);
}

function clip(value, max) {
  const text = compact(value);
  if (!text) return "-";
  return text.length > max ? text.slice(0, max).trimEnd() + "…" : text;
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function arraysEqual(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length !== b.length) return false;
  return a.every((item, index) => item === b[index]);
}

function icon(name, cls) {
  const path = app.values.ICON_PATHS[name] || app.values.ICON_PATHS.box;
  return (
    '<span class="' + (cls || "nav-icon") + '" aria-hidden="true">' +
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" ' +
    'stroke-linecap="round" stroke-linejoin="round">' + path + "</svg></span>"
  );
}

async function initialize() {
"use strict";
app.values.API = "/api/plug/astrbot_plugin_memory_companion/page";
app.values.PAGE_ENDPOINT_PREFIX = "page";
app.values.TRANSPARENT_IMAGE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==";
app.values.THEME_KEY = "memory_companion_theme";
app.values.NAV_KEY = "memory_companion_nav";
app.values.MODE_KEY = "memory_companion_mode";
app.values.$ = (sel, root) => (root || document).querySelector(sel);
app.values.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
app.values.ICON_PATHS = {
  gauge: '<circle cx="8" cy="8" r="6"/><path d="M8 5.4v2.9l2 1.2"/>',
  library: '<path d="M3 3.4h3.4v11H3z"/><path d="M8 3.4h3v11H8z"/><path d="M11.6 4.6l2.8-.7v8.5l-2.8.7z"/>',
  spark: '<path d="M8 1.8l1.6 4.1 4.1 1.6-4.1 1.6L8 13.2 6.4 9.1 2.3 7.5l4.1-1.6z"/><path d="M13.2 2.2v2.2M14.3 3.3h-2.2"/>',
  link: '<path d="M6.6 9.4l2.8-2.8"/><path d="M9.2 4.4l1.1-1.1a2.6 2.6 0 013.7 3.7l-1.1 1.1"/><path d="M6.8 11.6l-1.1 1.1a2.6 2.6 0 01-3.7-3.7l1.1-1.1"/>',
  download: '<path d="M8 2.4v7.2"/><path d="M4.9 6.7L8 9.8l3.1-3.1"/><path d="M2.6 12.2h10.8"/>',
  sliders: '<path d="M3 4.6h10M3 8h10M3 11.4h10"/><circle cx="6.2" cy="4.6" r="1.5"/><circle cx="10.4" cy="8" r="1.5"/><circle cx="5.4" cy="11.4" r="1.5"/>',
  chat: '<path d="M13.4 8.4c0 2.5-2.4 4.5-5.4 4.5-.7 0-1.4-.1-2-.3L2.6 13.6l.9-2.3A4.3 4.3 0 012.6 8.4c0-2.5 2.4-4.5 5.4-4.5s5.4 2 5.4 4.5z"/>',
  users: '<circle cx="6" cy="5.6" r="2.2"/><path d="M2.2 13.2c.3-2 1.9-3.2 3.8-3.2s3.5 1.2 3.8 3.2"/><path d="M11 3.6a2.2 2.2 0 010 4.2M12 10.4c1.5.3 2.5 1.4 2.7 2.8"/>',
  user: '<circle cx="8" cy="5.6" r="2.6"/><path d="M3.2 13.4c.5-2.2 2.4-3.5 4.8-3.5s4.3 1.3 4.8 3.5"/>',
  sun: '<circle cx="8" cy="8" r="2.8"/><path d="M8 1.6v1.6M8 12.8v1.6M1.6 8h1.6M12.8 8h1.6M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1"/>',
  camera: '<path d="M2.4 5.6h2.2l1-1.6h4.8l1 1.6h2.2v7.2H2.4z"/><circle cx="8" cy="9" r="2.2"/>',
  star: '<path d="M8 2.2l1.8 3.9 4.2.5-3.1 2.9.8 4.2L8 11.6l-3.7 2.1.8-4.2L2 6.6l4.2-.5z"/>',
  scope: '<circle cx="8" cy="8" r="5.4"/><circle cx="8" cy="8" r="1.4"/><path d="M8 1.4v2M8 12.6v2M1.4 8h2M12.6 8h2"/>',
  shield: '<path d="M8 2.2l4.6 1.8v4c0 3-1.9 5-4.6 5.8C5.3 12.9 3.4 11 3.4 8v-4z"/><path d="M6.1 8l1.4 1.4 2.6-2.8"/>',
  plug: '<path d="M6 2.4v3.2M10 2.4v3.2"/><path d="M4 5.6h8v2.2a4 4 0 01-8 0z"/><path d="M8 11.8v1.8"/>',
  box: '<path d="M2.6 5.4L8 2.6l5.4 2.8v5.2L8 13.4 2.6 10.6z"/><path d="M2.6 5.4L8 8.2l5.4-2.8M8 8.2v5.2"/>',
  heart: '<path d="M8 13.2S2.6 10 2.6 6.2A2.9 2.9 0 018 4.9a2.9 2.9 0 015.4 1.3c0 3.8-5.4 7-5.4 7z"/>',
  clock: '<circle cx="8" cy="8" r="5.6"/><path d="M8 4.8V8l2.4 1.5"/>',
  tag: '<path d="M8.6 2.6H13v4.4l-6 6-4.4-4.4z"/><circle cx="10.4" cy="5.2" r="0.9"/>',
  db: '<ellipse cx="8" cy="4.2" rx="5" ry="2"/><path d="M3 4.2v7.6c0 1.1 2.2 2 5 2s5-.9 5-2V4.2"/><path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
};
}
return { esc, compact, num, int, fmtInt, fmtTime, fmtDate, shortId, clip, sleep, clamp, arraysEqual, icon, initialize };
}
