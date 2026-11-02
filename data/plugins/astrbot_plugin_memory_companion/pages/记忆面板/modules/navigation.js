
// navigation: one page runtime owns shared state and DOM references.
export function createNavigation(app) {
function defineView(id, config) {
  app.values.VIEWS[id] = Object.assign({ id }, config);
}

function findGroupOf(viewId) {
  for (const group of app.values.NAV) {
    if (group.views.includes(viewId)) return group.id;
  }
  return "overview";
}

function renderRail() {
  const host = app.values.$("#railNav");
  host.innerHTML = app.values.NAV.map((group) => {
    const isOpen = group.id === app.values.state.group;
    const groupIcon = app.common.icon(group.icon);
    const subs = group.views
      .map((viewId) => {
        const view = app.values.VIEWS[viewId];
        if (!view || view.hidden) return "";
        const active = app.values.state.view === viewId ? " is-active" : "";
        return (
          '<button class="nav-sub' + active + '" type="button" data-view="' + viewId + '">' +
          app.common.esc(view.navLabel || view.title) +
          "</button>"
        );
      })
      .join("");
    const hasSubs = subs.trim().length > 0;
    const item =
      '<button class="nav-item' + (group.id === app.values.state.group ? " is-active" : "") + '" type="button" ' +
      'data-group="' + group.id + '" data-view="' + group.views[0] + '">' +
      groupIcon +
      '<span class="nav-label">' + app.common.esc(group.label) + "</span>" +
      (hasSubs
        ? '<span class="nav-chevron" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5L10.5 8 6 12.5"/></svg></span>'
        : "") +
      "</button>";
    return (
      '<div class="nav-group' + (isOpen ? " is-open" : "") + '" data-group-wrap="' + group.id + '">' +
      item +
      (hasSubs ? '<div class="nav-subs"><div class="nav-subs-inner">' + subs + "</div></div>" : "") +
      "</div>"
    );
  }).join("");
}

function setRailStatus(kind, text) {
  const node = app.values.$("#railStatus");
  if (!node) return;
  node.className = "rail-status" + (kind ? " is-" + kind : "");
  const label = app.values.$("#railStatusText");
  if (label) label.textContent = text;
}

async function go(viewId, options) {
  const view = app.values.VIEWS[viewId];
  if (!view || view.hidden) return;
  const opts = options || {};
  app.values.state.view = viewId;
  app.values.state.group = findGroupOf(viewId);
  try {
    window.localStorage.setItem(app.values.NAV_KEY, viewId);
  } catch (error) {
    /* 忽略 */
  }

  const cinemaOverview = app.values.state.mode === "cinema" && viewId === "overview";
  app.values.$("#viewEyebrow").textContent = cinemaOverview ? "MEMORY COMPANION / ARCHIVE OS" : (view.eyebrow || "Memory Companion");
  app.values.$("#viewTitle").textContent = cinemaOverview ? "记忆终端" : view.title;
  app.values.$("#viewHint").textContent = cinemaOverview ? "档案阵列、通道筛选与实时记忆状态" : (view.hint || "");
  document.title = (cinemaOverview ? "记忆终端" : view.title) + " · 记忆面板";
  renderRail();

  const token = app.values.renderToken + 1;
  app.values.renderToken = token;
  const host = app.values.$("#content");
  host.innerHTML = '<div class="skeleton" style="height:220px;border-radius:13px"></div>';

  try {
    const payload = await view.load(opts);
    if (token !== app.values.renderToken) return;
    host.innerHTML = "";
    const node = document.createElement("div");
    node.innerHTML = view.render(payload, opts);
    host.appendChild(node);
    if (typeof view.mount === "function") view.mount(node, payload, opts);
  } catch (error) {
    if (token !== app.values.renderToken) return;
    host.innerHTML =
      '<div class="empty"><b>这个视图没能加载出来</b><span>' + app.common.esc(error.message || "未知错误") + "</span></div>";
  }
}

function refresh() {
  go(app.values.state.view, { silent: true });
}

async function initialize() {
app.values.NAV = [
  { id: "overview", label: "总览", icon: "gauge", views: ["overview"] },
  {
    id: "library",
    label: "记忆库",
    icon: "library",
    views: ["navigate", "inspect", "core", "botlife"],
  },
  {
    id: "insight",
    label: "洞察",
    icon: "spark",
    views: ["starmap", "microscope", "synergy"],
  },
  {
    id: "bridge",
    label: "联动",
    icon: "link",
    views: ["companion", "external"],
  },
  {
    id: "import",
    label: "导入",
    icon: "download",
    views: ["chatimport", "migrate"],
  },
  {
    id: "settings",
    label: "设置",
    icon: "sliders",
    views: ["config", "acl"],
  },
];
app.values.VIEWS = {};
app.values.renderToken = 0;
}
return { defineView, findGroupOf, renderRail, setRailStatus, go, refresh, initialize };
}
