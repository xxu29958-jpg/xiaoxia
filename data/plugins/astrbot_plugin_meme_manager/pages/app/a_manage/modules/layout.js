
// layout: one page runtime owns shared state and DOM references.
export function createLayout(app) {
function lockPageScroll() {
    app.state.scrollLockCount += 1;
    if (app.state.scrollLockCount > 1) {
      return;
    }
    app.state.scrollLockSavedY = window.scrollY || 0;
    document.body.classList.add("page-scroll-locked");
    document.body.style.top = `-${app.state.scrollLockSavedY}px`;
  }

function unlockPageScroll() {
    if (app.state.scrollLockCount === 0) {
      return;
    }
    app.state.scrollLockCount -= 1;
    if (app.state.scrollLockCount > 0) {
      return;
    }
    document.body.classList.remove("page-scroll-locked");
    document.body.style.top = "";
    window.scrollTo(0, app.state.scrollLockSavedY);
  }

function isCompactViewport() {
    return window.matchMedia(app.state.MOBILE_LAYOUT_MEDIA).matches;
  }

function isConsoleVisible() {
    return isCompactViewport()
      ? document.body.classList.contains("panel-console-open")
      : !document.body.classList.contains("panel-console-hidden");
  }

function isDirectoryVisible() {
    return isCompactViewport()
      ? document.body.classList.contains("panel-directory-open")
      : !document.body.classList.contains("panel-directory-hidden");
  }

function setConsoleVisible(visible) {
    if (isCompactViewport()) {
      document.body.classList.toggle("panel-console-open", visible);
      return;
    }
    document.body.classList.toggle("panel-console-hidden", !visible);
  }

function setDirectoryVisible(visible) {
    if (isCompactViewport()) {
      document.body.classList.toggle("panel-directory-open", visible);
      return;
    }
    document.body.classList.toggle("panel-directory-hidden", !visible);
  }

function closeAllPanels() {
    setConsoleVisible(false);
    setDirectoryVisible(false);
  }

function updatePanelToggleState() {
    const consoleVisible = isConsoleVisible();
    const directoryVisible = isDirectoryVisible();

    if (app.dom.consoleToggleBtn) {
      app.dom.consoleToggleBtn.setAttribute("aria-expanded", String(consoleVisible));
      app.dom.consoleToggleBtn.setAttribute(
        "aria-label",
        consoleVisible ? "收起控制台" : "展开控制台",
      );
      app.dom.consoleToggleBtn.classList.toggle("active", consoleVisible);
    }

    if (app.dom.directoryToggleBtn) {
      app.dom.directoryToggleBtn.setAttribute(
        "aria-expanded",
        String(directoryVisible),
      );
      app.dom.directoryToggleBtn.setAttribute(
        "aria-label",
        directoryVisible ? "收起目录" : "展开目录",
      );
      app.dom.directoryToggleBtn.classList.toggle("active", directoryVisible);
    }

    if (app.dom.sidebarBackdrop) {
      const showBackdrop =
        isCompactViewport() && (consoleVisible || directoryVisible);
      app.dom.sidebarBackdrop.classList.toggle("hidden", !showBackdrop);
      app.dom.sidebarBackdrop.setAttribute("aria-hidden", String(!showBackdrop));
    }

    app.dom.leftPanel?.setAttribute("aria-hidden", String(!consoleVisible));
    app.dom.directoryPanel?.setAttribute("aria-hidden", String(!directoryVisible));
    if (app.dom.leftPanel) app.dom.leftPanel.inert = !consoleVisible;
    if (app.dom.directoryPanel) app.dom.directoryPanel.inert = !directoryVisible;
  }

function syncSidebarLayout() {
    if (isCompactViewport()) {
      document.body.classList.remove("panel-console-hidden");
      document.body.classList.remove("panel-directory-hidden");
      closeAllPanels();
      updatePanelToggleState();
      return;
    }

    document.body.classList.remove(
      "panel-console-open",
      "panel-directory-open",
    );
    if (
      !document.body.classList.contains("panel-console-hidden") &&
      !document.body.classList.contains("panel-directory-hidden")
    ) {
      setConsoleVisible(true);
      setDirectoryVisible(true);
    }
    updatePanelToggleState();
  }

function toggleConsolePanel() {
    const visible = !isConsoleVisible();
    if (visible && isCompactViewport()) {
      // 移动端抽屉互斥：打开控制台时先收起目录
      setDirectoryVisible(false);
    }
    setConsoleVisible(visible);
    updatePanelToggleState();
  }

function toggleDirectoryPanel() {
    const visible = !isDirectoryVisible();
    if (visible && isCompactViewport()) {
      // 移动端抽屉互斥：打开目录时先收起控制台
      setConsoleVisible(false);
    }
    setDirectoryVisible(visible);
    updatePanelToggleState();
  }

function formatBytes(bytes) {
    if (typeof bytes !== "number" || Number.isNaN(bytes) || bytes < 0) {
      return "未知";
    }
    if (bytes === 0) {
      return "0 B";
    }

    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let unitIndex = 0;

    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }

    const precision = unitIndex === 0 ? 0 : value >= 100 ? 0 : 1;
    return `${value.toFixed(precision)} ${units[unitIndex]}`;
  }
return { lockPageScroll, unlockPageScroll, isCompactViewport, isConsoleVisible, isDirectoryVisible, setConsoleVisible, setDirectoryVisible, closeAllPanels, updatePanelToggleState, syncSidebarLayout, toggleConsolePanel, toggleDirectoryPanel, formatBytes };
}
