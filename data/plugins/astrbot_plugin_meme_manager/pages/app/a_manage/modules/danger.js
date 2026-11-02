
// danger: one page runtime owns shared state and DOM references.
export function createDanger(app) {
function resetDangerConfirmState() {
    if (app.state.dangerConfirmTimer) {
      clearInterval(app.state.dangerConfirmTimer);
      app.state.dangerConfirmTimer = null;
    }
    app.state.dangerConfirmConfig = null;
    app.state.dangerConfirmStage = "ack";
    if (app.dom.dangerModalAcknowledge) {
      app.dom.dangerModalAcknowledge.checked = false;
      app.dom.dangerModalAcknowledge.disabled = false;
    }
    if (app.dom.dangerModalStageText) {
      app.dom.dangerModalStageText.textContent =
        "请先勾选已理解，勾选后会自动开始 5 秒倒计时。";
    }
    if (app.dom.dangerModalConfirmBtn) {
      app.dom.dangerModalConfirmBtn.disabled = true;
      app.dom.dangerModalConfirmBtn.textContent = "请先勾选上方选项";
    }
  }

function closeDangerConfirm(result) {
    if (app.dom.dangerModalRoot) {
      app.dom.dangerModalRoot.classList.add("hidden");
      app.dom.dangerModalRoot.setAttribute("aria-hidden", "true");
    }
    resetDangerConfirmState();
    if (app.state.dangerConfirmResolver) {
      const resolver = app.state.dangerConfirmResolver;
      app.state.dangerConfirmResolver = null;
      resolver(result);
    }
    app.layout.unlockPageScroll();
  }

function startDangerCountdown() {
    if (app.state.dangerConfirmStage !== "ack" || !app.state.dangerConfirmConfig) {
      return;
    }

    const countdown = app.state.dangerConfirmConfig?.countdown ?? 5;
    let remaining = countdown;

    app.state.dangerConfirmStage = "countdown";
    if (app.dom.dangerModalAcknowledge) {
      app.dom.dangerModalAcknowledge.disabled = true;
    }
    if (app.dom.dangerModalStageText) {
      app.dom.dangerModalStageText.textContent = `安全等待中，还需 ${remaining} 秒，倒计时结束后才可执行。`;
    }
    if (app.dom.dangerModalConfirmBtn) {
      app.dom.dangerModalConfirmBtn.disabled = true;
      app.dom.dangerModalConfirmBtn.textContent = `等待 ${remaining} 秒`;
    }

    app.state.dangerConfirmTimer = setInterval(() => {
      remaining -= 1;
      if (remaining > 0) {
        app.dom.dangerModalStageText.textContent = `安全等待中，还需 ${remaining} 秒，倒计时结束后才可执行。`;
        app.dom.dangerModalConfirmBtn.textContent = `等待 ${remaining} 秒`;
        return;
      }

      clearInterval(app.state.dangerConfirmTimer);
      app.state.dangerConfirmTimer = null;
      app.state.dangerConfirmStage = "ready";
      app.dom.dangerModalStageText.textContent =
        "5 秒倒计时已结束，请点击下方按钮执行。";
      app.dom.dangerModalConfirmBtn.disabled = false;
      app.dom.dangerModalConfirmBtn.textContent = app.state.dangerConfirmConfig.actionLabel;
    }, 1000);
  }

function showDangerConfirm({
    title,
    description,
    actionLabel,
    countdown = 5,
  }) {
    if (
      !app.dom.dangerModalRoot ||
      !app.dom.dangerModalTitle ||
      !app.dom.dangerModalDescription ||
      !app.dom.dangerModalConfirmBtn
    ) {
      return Promise.resolve(
        confirm(`${title}\n\n${description}\n\n确认要继续执行吗？`),
      );
    }

    resetDangerConfirmState();
    app.state.dangerConfirmConfig = { actionLabel, countdown };
    app.dom.dangerModalTitle.textContent = title;
    app.dom.dangerModalDescription.textContent = description;
    if (app.dom.dangerModalStageText) {
      app.dom.dangerModalStageText.textContent = `请先勾选已理解，勾选后会自动开始 ${countdown} 秒倒计时。倒计时结束后才可执行。`;
    }
    if (app.dom.dangerModalConfirmBtn) {
      app.dom.dangerModalConfirmBtn.textContent = "请先勾选上方选项";
      app.dom.dangerModalConfirmBtn.disabled = true;
    }
    app.layout.lockPageScroll();
    app.dom.dangerModalRoot.classList.remove("hidden");
    app.dom.dangerModalRoot.setAttribute("aria-hidden", "false");

    return new Promise((resolve) => {
      app.state.dangerConfirmResolver = resolve;
    });
  }
return { resetDangerConfirmState, closeDangerConfirm, startDangerCountdown, showDangerConfirm };
}
