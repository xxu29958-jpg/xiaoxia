
// shell: one page runtime owns shared state and DOM references.
export function createShell(app) {
function toast(message, tone) {
  const node = app.values.$("#toast");
  if (!node) return;
  node.textContent = message;
  node.dataset.tone = tone || "";
  node.classList.add("is-on");
  window.clearTimeout(app.values.toastTimer);
  app.values.toastTimer = window.setTimeout(() => node.classList.remove("is-on"), 2600);
}

function busy(on, text) {
  app.values.busyCount = Math.max(0, app.values.busyCount + (on ? 1 : -1));
  const layer = app.values.$("#busyLayer");
  if (!layer) return;
  if (text) app.values.$("#busyText").textContent = text;
  layer.classList.toggle("is-on", app.values.busyCount > 0);
}

async function withBusy(text, fn) {
  busy(true, text);
  try {
    return await fn();
  } catch (error) {
    // This boundary owns DOM event actions: keep the form and show a retryable
    // failure instead of leaving an unhandled promise and an invisible error.
    toast(error.message || "操作失败，请重试", "error");
    return null;
  } finally {
    busy(false);
  }
}

function openDrawer(title, eyebrow, html) {
  app.values.$("#drawerEyebrow").textContent = eyebrow || "记忆";
  app.values.$("#drawerTitle").textContent = title || "未选择";
  app.values.$("#drawerBody").innerHTML = html;
  app.values.$("#drawer").classList.add("is-open");
  app.values.$("#drawer").setAttribute("aria-hidden", "false");
  app.values.$("#scrim").hidden = false;
}

function closeDrawer() {
  app.values.$("#drawer").classList.remove("is-open");
  app.values.$("#drawer").setAttribute("aria-hidden", "true");
  app.values.$("#scrim").hidden = true;
}

function showInlineConfirmation(title, message, confirmLabel) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "inline-confirm-overlay";
    overlay.innerHTML =
      '<section class="inline-confirm" role="dialog" aria-modal="true" aria-labelledby="inlineConfirmTitle">' +
      '<h2 id="inlineConfirmTitle">' + app.common.esc(title || "请确认") + "</h2>" +
      '<p>' + app.common.esc(message || "确认继续此操作？") + "</p>" +
      '<div class="inline-confirm-actions"><button type="button" data-confirm-cancel>取消</button><button type="button" class="is-danger" data-confirm-ok>' +
      app.common.esc(confirmLabel || "继续") + "</button></div></section>";
    const finish = (value) => {
      overlay.remove();
      resolve(value);
    };
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target.closest("[data-confirm-cancel]")) finish(false);
      if (event.target.closest("[data-confirm-ok]")) finish(true);
    });
    document.body.appendChild(overlay);
    const ok = app.values.$("[data-confirm-ok]", overlay);
    if (ok) ok.focus();
  });
}

async function initialize() {
app.values.state = {
  view: "overview",
  group: "overview",
  theme: document.documentElement.dataset.theme || "dark",
  mode: document.documentElement.dataset.initialMode === "cinema" ? "cinema" : "standard",
  ready: false,
  stats: null,
  buckets: [],
  filters: { scope: "", q: "", visibility: "", lifecycle: "", memoryType: "" },
  selected: null,
  starmap: null,
  configSchema: null,
  configModule: "appearance",
  companion: null,
  acl: null,
  assemblyViewer: null,
};
app.values.toastTimer = 0;
app.values.assemblyViewerTicket = 0;
app.values.busyCount = 0;
}
return { toast, busy, withBusy, openDrawer, closeDrawer, showInlineConfirmation, initialize };
}
