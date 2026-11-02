
// boot: one page runtime owns shared state and DOM references.
export function createBoot(app) {
function bindShell() {
  app.values.$("#themeToggle").addEventListener("click", () => {
    app.appearance.applyTheme(app.values.state.theme === "dark" ? "light" : "dark");
  });

  window.addEventListener("mc:theme", () => {
    if (app.values.galaxyInstance) app.values.galaxyInstance.buildStarfield();
  });

  app.values.$("#drawerClose").addEventListener("click", app.shell.closeDrawer);
  app.values.$("#scrim").addEventListener("click", app.shell.closeDrawer);
  app.values.$("#assemblyViewerClose")?.addEventListener("click", app.viewer.closeAssemblyViewer);
  app.values.$("#assemblyViewerRetry")?.addEventListener("click", () => {
    const id = app.values.$("#assemblyViewerFile")?.textContent.replace(/^FILE\s+|\s+\/.*$/g, "").trim();
    if (id) app.viewer.openAssemblyViewer(id);
  });
  app.values.$("#assemblyExplode")?.addEventListener("click", () => app.values.state.assemblyViewer?.setExploded(true));
  app.values.$("#assemblyAssemble")?.addEventListener("click", () => app.values.state.assemblyViewer?.setExploded(false));
  app.values.$("#assemblyReset")?.addEventListener("click", () => app.values.state.assemblyViewer?.reset());

  app.values.$("#refreshBtn").addEventListener("click", () => {
    app.pool.invalidatePool();
    app.navigation.refresh();
  });

  const modeToggle = app.values.$("#modeToggle");
  if (modeToggle) {
    modeToggle.addEventListener("click", () => {
      app.cinema.applyMode(app.values.state.mode === "cinema" ? "standard" : "cinema");
      app.shell.toast(app.values.state.mode === "cinema" ? "已进入终端模式" : "已切回标准模式", "ok");
      app.navigation.go(app.values.state.mode === "cinema" ? "overview" : app.values.state.view);
    });
  }

  const search = app.values.$("#globalSearch");
  search.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const value = search.value.trim();
    if (!value) return;
    app.values.state.filters = { scope: "", q: value, visibility: "", lifecycle: "", memoryType: "", target: "" };
    app.navigation.go("inspect");
  });

  app.values.$("#railNav").addEventListener("click", (event) => {
    const item = event.target.closest("[data-view]");
    if (!item) return;
    app.navigation.go(item.dataset.view);
  });

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-goto]");
    if (!target) return;
    const view = target.dataset.goto;
    const filter = app.common.compact(target.dataset.filter);
    const options = {};
    if (app.common.compact(target.dataset.scope)) options.scope = target.dataset.scope;
    if (app.common.compact(target.dataset.target)) options.target = target.dataset.target;
    if (filter) {
      if (filter.indexOf("scope:") === 0) {
        app.values.state.filters = { scope: filter.slice(6), q: "", visibility: "", lifecycle: "", memoryType: "", target: "" };
      } else if (filter === "profile") {
        app.values.state.filters = { scope: "profile", q: "", visibility: "", lifecycle: "", memoryType: "", target: "" };
      } else if (filter.indexOf("external:") === 0) {
        app.values.state.filters = { scope: "", q: "", visibility: "", lifecycle: "", memoryType: "", target: "" };
      }
    }
    app.navigation.go(view, options);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !app.values.$("#assemblyViewer")?.hidden) {
      event.preventDefault();
      app.viewer.closeAssemblyViewer();
      return;
    }
    if (app.values.state.mode === "cinema" && app.values.state.view === "overview") {
      const searchBox = app.values.$("#terminalSearch");
      if (event.key === "/" && document.activeElement !== searchBox) {
        event.preventDefault();
        searchBox && searchBox.focus();
        return;
      }
      const prevColumn = app.values.$("[data-terminal-column-prev]");
      const nextColumn = app.values.$("[data-terminal-column-next]");
      const prevRecord = app.values.$("[data-terminal-prev]");
      const nextRecord = app.values.$("[data-terminal-next]");
      const records = app.values.$$("#terminalFeed .terminal-record:not([hidden])");
      if (event.key === "ArrowLeft" && prevColumn) {
        event.preventDefault();
        prevColumn.click();
        return;
      }
      if (event.key === "ArrowRight" && nextColumn) {
        event.preventDefault();
        nextColumn.click();
        return;
      }
      if (event.key === "ArrowUp" && prevRecord) {
        event.preventDefault();
        prevRecord.click();
        return;
      }
      if (event.key === "ArrowDown" && nextRecord) {
        event.preventDefault();
        nextRecord.click();
        return;
      }
      if (event.key === "Enter" && document.activeElement?.classList.contains("terminal-record")) {
        event.preventDefault();
        document.activeElement.click();
        return;
      }
    }
    if (event.key === "Escape") {
      app.shell.closeDrawer();
      const box = app.values.$(".lightbox");
      if (box) box.remove();
    }
  });
}

async function boot() {
  bindShell();
  app.appearance.applyTheme(app.values.state.theme);
  app.cinema.applyMode(app.values.state.mode);
  app.navigation.renderRail();
  app.navigation.setRailStatus("loading", "正在连接…");

  const initial = document.documentElement.dataset.initialNav;
  const target = app.values.state.mode === "cinema" ? "overview" : (initial && app.values.VIEWS[initial] ? initial : "overview");

  try {
    await app.api.apiGet("/stats");
    app.navigation.setRailStatus("ok", "已连接");
  } catch (error) {
    app.navigation.setRailStatus("bad", "连接失败");
  }

  app.values.state.ready = true;
  await app.navigation.go(target);
  // 配色放最后：不阻塞首屏，失败也不影响面板可用
  app.appearance.syncPalette();
}

async function initialize() {
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
}
return { bindShell, boot, initialize };
}
