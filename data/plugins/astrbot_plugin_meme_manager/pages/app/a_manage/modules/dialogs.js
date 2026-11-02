
// dialogs: one page runtime owns shared state and DOM references.
export function createDialogs(app) {
function showToast(message, type = "info", title = "提示", duration = 3200) {
    if (!app.dom.toastContainer) {
      return;
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;

    const content = document.createElement("div");
    content.className = "toast-content";

    const titleElement = document.createElement("p");
    titleElement.className = "toast-title";
    titleElement.textContent = title;

    const messageElement = document.createElement("p");
    messageElement.className = "toast-message";
    messageElement.textContent = message;

    content.appendChild(titleElement);
    content.appendChild(messageElement);
    toast.appendChild(content);
    app.dom.toastContainer.appendChild(toast);

    window.setTimeout(() => {
      toast.remove();
    }, duration);
  }

function closeConfirm(result) {
    if (app.dom.confirmModalRoot) {
      const restoreTarget = app.state.confirmRestoreFocusElement;
      app.state.confirmRestoreFocusElement = null;
      if (
        restoreTarget instanceof HTMLElement &&
        restoreTarget.isConnected &&
        !restoreTarget.hasAttribute("disabled")
      ) {
        restoreTarget.focus();
      }
      if (
        app.dom.confirmModalRoot.contains(document.activeElement) &&
        document.activeElement instanceof HTMLElement
      ) {
        document.activeElement.blur();
      }
      app.dom.confirmModalRoot.inert = true;
      app.dom.confirmModalRoot.classList.add("hidden");
      app.dom.confirmModalRoot.setAttribute("aria-hidden", "true");
    }
    if (app.dom.confirmModalConfirmBtn) {
      app.dom.confirmModalConfirmBtn.classList.remove("danger");
      app.dom.confirmModalConfirmBtn.textContent = "确认";
    }
    if (app.state.confirmResolver) {
      const resolver = app.state.confirmResolver;
      app.state.confirmResolver = null;
      resolver(result);
    }
    app.layout.unlockPageScroll();
  }

function showConfirm({
    title,
    description,
    confirmLabel = "确认",
    confirmClassName = "",
  }) {
    if (
      !app.dom.confirmModalRoot ||
      !app.dom.confirmModalTitle ||
      !app.dom.confirmModalDescription ||
      !app.dom.confirmModalConfirmBtn
    ) {
      return Promise.resolve(confirm(`${title}\n\n${description}`));
    }

    app.dom.confirmModalTitle.textContent = title;
    app.dom.confirmModalDescription.textContent = description;
    app.dom.confirmModalConfirmBtn.textContent = confirmLabel;
    app.dom.confirmModalConfirmBtn.classList.toggle(
      "danger",
      confirmClassName.includes("danger"),
    );
    const activeElement = document.activeElement;
    app.state.confirmRestoreFocusElement =
      activeElement instanceof HTMLElement &&
      !app.dom.confirmModalRoot.contains(activeElement)
        ? activeElement
        : null;
    app.layout.lockPageScroll();
    app.dom.confirmModalRoot.inert = false;
    app.dom.confirmModalRoot.classList.remove("hidden");
    app.dom.confirmModalRoot.setAttribute("aria-hidden", "false");
    window.requestAnimationFrame(() => {
      if (!app.dom.confirmModalRoot.classList.contains("hidden")) {
        (app.dom.confirmModalCancelBtn || app.dom.confirmModalConfirmBtn).focus();
      }
    });

    return new Promise((resolve) => {
      app.state.confirmResolver = resolve;
    });
  }
return { showToast, closeConfirm, showConfirm };
}
