
// widgets: one page runtime owns shared state and DOM references.
export function createWidgets(app) {
function createButton({
    className = "",
    text = "",
    disabled = false,
    onClick = null,
  }) {
    const button = document.createElement("button");
    button.type = "button";
    if (className) {
      button.className = className;
    }
    button.textContent = text;
    button.disabled = disabled;
    if (onClick) {
      button.addEventListener("click", onClick);
    }
    return button;
  }

function createIconButton({
    className = "",
    iconClass = "",
    title = "",
    ariaLabel = "",
    onClick = null,
  }) {
    const button = document.createElement("button");
    button.type = "button";
    if (className) {
      button.className = className;
    }
    if (title) {
      button.title = title;
    }
    if (ariaLabel) {
      button.setAttribute("aria-label", ariaLabel);
    }

    if (iconClass) {
      const icon = document.createElement("i");
      icon.className = iconClass;
      button.appendChild(icon);
    }

    if (onClick) {
      button.addEventListener("click", onClick);
    }

    return button;
  }

function setButtonBusy(button, busyText) {
    if (!button) return;
    if (!button.dataset.originalHtml) {
      button.dataset.originalHtml = button.innerHTML;
    }
    button.disabled = true;
    button.textContent = busyText;
  }

function restoreButton(button) {
    if (!button) return;
    button.disabled = false;
    if (button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
    }
  }
return { createButton, createIconButton, setButtonBusy, restoreButton };
}
