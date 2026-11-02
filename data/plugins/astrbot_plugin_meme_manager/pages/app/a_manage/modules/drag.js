
// drag: one page runtime owns shared state and DOM references.
export function createDrag(app) {
function getDragItemsForEmoji(category, emoji) {
    if (app.state.selectionState.enabled && app.selection.isEmojiSelected(category, emoji)) {
      return app.clipboard.dedupeEmojiItems(Array.from(app.state.selectionState.items.values()));
    }
    return [{ category, emoji }];
  }

function getDragReadyLabel(itemCount) {
    return `${itemCount}张`;
  }

function hasActiveDragInteraction() {
    return Boolean(
      app.state.longPressState.emojiItem ||
      app.state.dragModeState.pointerId !== null ||
      app.state.dragModeState.items.length > 0,
    );
  }

function syncInteractionGuardState() {
    document.body.classList.toggle(
      "drag-session-active",
      app.state.dragModeState.pointerId !== null,
    );
  }

function updateDragHudPosition(clientX, clientY) {
    if (!app.dom.dragHud) {
      return;
    }

    const hudRect = app.dom.dragHud.getBoundingClientRect();
    const hudWidth = hudRect.width || 72;
    const hudHeight = hudRect.height || 72;
    const x = Math.min(
      window.innerWidth - hudWidth - 10,
      Math.max(10, clientX + app.state.DRAG_HUD_OFFSET_X),
    );
    const y = Math.min(
      window.innerHeight - hudHeight - 10,
      Math.max(10, clientY - app.state.DRAG_HUD_OFFSET_Y),
    );

    app.dom.dragHud.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(
      y,
    )}px, 0)`;
  }

function stopDragAutoScroll() {
    if (app.state.dragModeState.autoScrollFrameId) {
      cancelAnimationFrame(app.state.dragModeState.autoScrollFrameId);
      app.state.dragModeState.autoScrollFrameId = null;
    }
  }

function stepDragAutoScroll() {
    if (app.state.dragModeState.pointerId === null) {
      stopDragAutoScroll();
      return;
    }

    const topThreshold = 96;
    const bottomThreshold = window.innerHeight - 96;
    let deltaY = 0;

    if (app.state.dragModeState.lastClientY < topThreshold) {
      deltaY = Math.max(-18, (app.state.dragModeState.lastClientY - topThreshold) * 0.18);
    } else if (app.state.dragModeState.lastClientY > bottomThreshold) {
      deltaY = Math.min(
        18,
        (app.state.dragModeState.lastClientY - bottomThreshold) * 0.18,
      );
    }

    const hovered = document.elementFromPoint(
      app.state.dragModeState.lastClientX,
      app.state.dragModeState.lastClientY,
    );
    const overTray = hovered?.closest("#drag-target-tray");
    if (overTray) {
      const rect = app.dom.dragTargetList.getBoundingClientRect();
      const y = app.state.dragModeState.lastClientY;
      deltaY = y < rect.top + 32 ? -10 : y > rect.bottom - 32 ? 10 : 0;
    }
    if (deltaY !== 0) {
      if (overTray) app.dom.dragTargetList.scrollBy({ top: deltaY, behavior: "auto" });
      else {
        window.scrollBy({ top: deltaY, behavior: "auto" });
      }
      updateActiveDropTarget(
        app.state.dragModeState.lastClientX,
        app.state.dragModeState.lastClientY,
      );
      showDragHud({
        label: getDragReadyLabel(app.state.dragModeState.items.length),
        caption: app.state.dragModeState.activeCategory
          ? `松手后移动到 ${app.state.dragModeState.activeCategory}`
          : "拖到屏幕边缘可自动滚动",
        progress: 1,
        clientX: app.state.dragModeState.lastClientX,
        clientY: app.state.dragModeState.lastClientY,
        state: app.state.dragModeState.activeCategory ? "target" : "ready",
      });
    }

    app.state.dragModeState.autoScrollFrameId = requestAnimationFrame(stepDragAutoScroll);
  }

function ensureDragAutoScroll() {
    if (app.state.dragModeState.autoScrollFrameId) {
      return;
    }
    app.state.dragModeState.autoScrollFrameId = requestAnimationFrame(stepDragAutoScroll);
  }

function showDragHud({
    label,
    caption,
    progress = 0,
    clientX = null,
    clientY = null,
    state = "press",
  }) {
    if (!app.dom.dragHud) {
      return;
    }

    const safeProgress = Math.max(0, Math.min(progress, 1));
    app.dom.dragHud.classList.remove("hidden");
    app.dom.dragHud.classList.add("visible");
    app.dom.dragHud.dataset.state = state;
    app.dom.dragHud.style.setProperty(
      "--drag-hud-progress",
      `${safeProgress * 360}deg`,
    );
    app.dom.dragHud.setAttribute("aria-hidden", "false");

    if (app.dom.dragHudLabel) {
      app.dom.dragHudLabel.textContent = label;
    }
    if (app.dom.dragHudCaption) {
      app.dom.dragHudCaption.textContent = caption;
    }
    if (typeof clientX === "number" && typeof clientY === "number") {
      updateDragHudPosition(clientX, clientY);
    }
  }

function hideDragHud() {
    if (!app.dom.dragHud) {
      return;
    }

    app.dom.dragHud.classList.remove("visible");
    app.dom.dragHud.classList.add("hidden");
    app.dom.dragHud.dataset.state = "idle";
    app.dom.dragHud.style.setProperty("--drag-hud-progress", "0deg");
    app.dom.dragHud.style.transform = "translate3d(-9999px, -9999px, 0)";
    app.dom.dragHud.setAttribute("aria-hidden", "true");

    if (app.dom.dragHudLabel) {
      app.dom.dragHudLabel.textContent = "选择";
    }
    if (app.dom.dragHudCaption) {
      app.dom.dragHudCaption.textContent = "长按选择，滑动浏览";
    }
  }

function setLongPressProgress(progress, label) {
    if (!app.state.longPressState.emojiItem) {
      return;
    }

    showDragHud({
      label,
      caption: "长按选择，滑动浏览",
      progress,
      clientX: app.state.longPressState.currentX,
      clientY: app.state.longPressState.currentY,
      state: "press",
    });
  }

function resetLongPressVisual(emojiItem) {
    if (!emojiItem) {
      return;
    }

    emojiItem.classList.remove("long-press-active");
  }

function cancelLongPress({ preserveReady = false, keepHud = false } = {}) {
    if (app.state.longPressState.timeoutId) {
      clearTimeout(app.state.longPressState.timeoutId);
      app.state.longPressState.timeoutId = null;
    }
    if (app.state.longPressState.intervalId) {
      clearInterval(app.state.longPressState.intervalId);
      app.state.longPressState.intervalId = null;
    }
    if (app.state.longPressState.emojiItem) {
      app.state.longPressState.emojiItem.classList.remove("long-press-active");
      if (!preserveReady) {
        resetLongPressVisual(app.state.longPressState.emojiItem);
      }
    }

    app.state.longPressState.emojiItem = null;
    app.state.longPressState.pointerId = null;
    app.state.longPressState.startTime = 0;
    app.state.longPressState.startX = 0;
    app.state.longPressState.startY = 0;
    app.state.longPressState.currentX = 0;
    app.state.longPressState.currentY = 0;

    if (!keepHud && app.state.dragModeState.pointerId === null) {
      hideDragHud();
    }

    syncInteractionGuardState();
  }

function updateActiveDropTarget(clientX, clientY) {
    clearCategoryDropHighlights();
    app.state.dragModeState.activeCategory = null;

    const hoveredElement = document.elementFromPoint(clientX, clientY);
    const categoryDiv = hoveredElement?.closest(
      "[data-drop-category], .category",
    );
    if (categoryDiv?.disabled) return;
    const targetCategory =
      categoryDiv?.dataset?.dropCategory || categoryDiv?.dataset?.category;

    if (!categoryDiv || !targetCategory) {
      return;
    }

    if (!hasMoveableItemsForTarget(app.state.dragModeState.items, targetCategory)) {
      return;
    }

    app.state.dragModeState.activeCategory = targetCategory;
    categoryDiv.classList.add("category-drop-active");
  }

function updatePointerDrag(event) {
    if (
      app.state.dragModeState.pointerId === null ||
      app.state.dragModeState.pointerId !== event.pointerId ||
      app.state.dragModeState.items.length === 0
    ) {
      return;
    }

    app.state.dragModeState.isPointerDragging = true;
    app.state.dragModeState.lastClientX = event.clientX;
    app.state.dragModeState.lastClientY = event.clientY;
    updateActiveDropTarget(event.clientX, event.clientY);
    showDragHud({
      label: getDragReadyLabel(app.state.dragModeState.items.length),
      caption: app.state.dragModeState.activeCategory
        ? `松手后移动到 ${app.state.dragModeState.activeCategory}`
        : "拖到目标分类，松手即可移动",
      progress: 1,
      clientX: event.clientX,
      clientY: event.clientY,
      state: app.state.dragModeState.activeCategory ? "target" : "ready",
    });
  }

async function finishPointerDrag(event) {
    if (
      app.state.dragModeState.pointerId === null ||
      app.state.dragModeState.pointerId !== event.pointerId
    ) {
      return;
    }

    updateActiveDropTarget(event.clientX, event.clientY);
    const targetCategory = app.state.dragModeState.activeCategory;
    const dragItems = app.clipboard.dedupeEmojiItems(app.state.dragModeState.items);

    app.state.dragModeState.pointerId = null;
    app.state.dragModeState.activeCategory = null;
    app.state.dragModeState.isPointerDragging = false;
    app.state.dragModeState.lastClientX = 0;
    app.state.dragModeState.lastClientY = 0;
    stopDragAutoScroll();
    if (
      app.state.dragModeState.captureElement &&
      typeof event.pointerId === "number" &&
      typeof app.state.dragModeState.captureElement.releasePointerCapture === "function"
    ) {
      try {
        app.state.dragModeState.captureElement.releasePointerCapture(event.pointerId);
      } catch {}
    }
    app.state.dragModeState.captureElement = null;
    clearCategoryDropHighlights();
    hideDragHud();
    syncInteractionGuardState();

    if (
      targetCategory &&
      hasMoveableItemsForTarget(dragItems, targetCategory)
    ) {
      await app.move.moveEmojiItemsToCategory(targetCategory, dragItems);
      return;
    }

    clearDragMode();
  }

function clearDragMode() {
    cancelLongPress({ keepHud: true });

    stopDragAutoScroll();
    if (
      app.state.dragModeState.captureElement &&
      typeof app.state.dragModeState.pointerId === "number" &&
      typeof app.state.dragModeState.captureElement.releasePointerCapture === "function"
    ) {
      try {
        app.state.dragModeState.captureElement.releasePointerCapture(
          app.state.dragModeState.pointerId,
        );
      } catch {}
    }

    app.state.dragModeState.items = [];
    app.state.dragModeState.pointerId = null;
    app.state.dragModeState.activeCategory = null;
    app.state.dragModeState.isPointerDragging = false;
    app.state.dragModeState.captureElement = null;
    app.state.dragModeState.lastClientX = 0;
    app.state.dragModeState.lastClientY = 0;
    document.querySelectorAll(".emoji-item").forEach((emojiItem) => {
      emojiItem.classList.remove("drag-ready", "dragging");
      resetLongPressVisual(emojiItem);
    });
    clearCategoryDropHighlights();
    hideDragHud();
    app.dom.dragTargetTray.classList.add("hidden");
    app.dom.dragTargetList.replaceChildren();
    app.dom.dragHudPreview.removeAttribute("src");
    syncInteractionGuardState();
  }

function armDragMode(items, pointerContext = {}) {
    const dragItems = app.clipboard.dedupeEmojiItems(items);
    if (dragItems.length === 0) {
      return;
    }

    clearDragMode();
    app.state.dragModeState.items = dragItems;
    Object.keys(app.state.latestEmojiData)
      .sort((a, b) => a.localeCompare(b, "zh-CN"))
      .forEach((category) => {
        const target = document.createElement("button");
        target.type = "button";
        target.dataset.dropCategory = category;
        target.className = "drag-target-option";
        target.disabled = !hasMoveableItemsForTarget(dragItems, category);
        target.textContent = `${category}${target.disabled ? "（原分类）" : ""}`;
        app.dom.dragTargetList.appendChild(target);
      });
    app.dom.dragTargetTray.classList.remove("hidden");
    const preview = pointerContext.sourceElement?.querySelector("img");
    const previewUrl =
      pointerContext.sourceElement?.dataset.previewDataUrl ||
      preview?.currentSrc ||
      preview?.src;
    if (previewUrl) app.dom.dragHudPreview.src = previewUrl;
    const armedKeys = new Set(
      dragItems.map(({ category, emoji }) =>
        app.selection.createSelectionKey(category, emoji),
      ),
    );

    document.querySelectorAll(".emoji-item").forEach((emojiItem) => {
      const emojiKey = app.selection.createSelectionKey(
        emojiItem.dataset.category,
        emojiItem.dataset.emoji,
      );
      const armed = armedKeys.has(emojiKey);
      emojiItem.classList.toggle("drag-ready", armed);
      resetLongPressVisual(emojiItem);
    });

    if (
      typeof pointerContext.clientX === "number" &&
      typeof pointerContext.clientY === "number"
    ) {
      app.state.dragModeState.pointerId =
        typeof pointerContext.pointerId === "number"
          ? pointerContext.pointerId
          : null;
      app.state.dragModeState.captureElement = pointerContext.sourceElement || null;
      app.state.dragModeState.lastClientX = pointerContext.clientX;
      app.state.dragModeState.lastClientY = pointerContext.clientY;
      if (
        app.state.dragModeState.captureElement &&
        app.state.dragModeState.pointerId !== null &&
        typeof app.state.dragModeState.captureElement.setPointerCapture === "function"
      ) {
        try {
          app.state.dragModeState.captureElement.setPointerCapture(
            app.state.dragModeState.pointerId,
          );
        } catch {}
      }
      ensureDragAutoScroll();
      showDragHud({
        label: getDragReadyLabel(dragItems.length),
        caption: "拖到目标分类，松手即可移动",
        progress: 1,
        clientX: pointerContext.clientX,
        clientY: pointerContext.clientY,
        state: "ready",
      });
    }

    syncInteractionGuardState();
  }

function startLongPress(emojiItem, category, emoji, event) {
    emojiItem.dataset.suppressClick = "false";
    if (
      app.state.moveBusy ||
      event.isPrimary === false ||
      (event.pointerType === "mouse" &&
        app.state.activeManagePackId &&
        app.state.defaultManagePackId &&
        app.state.activeManagePackId !== app.state.defaultManagePackId) ||
      (event.pointerType === "mouse" && event.button !== 0) ||
      event.target.closest(".delete-btn")
    ) {
      return;
    }

    const dragItems = getDragItemsForEmoji(category, emoji);
    if (dragItems.length === 0) {
      return;
    }

    cancelLongPress();
    if (
      app.state.dragModeState.items.length > 0 &&
      !emojiItem.classList.contains("drag-ready")
    ) {
      clearDragMode();
    }

    app.state.longPressState.emojiItem = emojiItem;
    app.state.longPressState.pointerId = event.pointerId;
    app.state.longPressState.pointerType = event.pointerType;
    app.state.longPressState.startTime = performance.now();
    app.state.longPressState.startX = event.clientX;
    app.state.longPressState.startY = event.clientY;
    app.state.longPressState.currentX = event.clientX;
    app.state.longPressState.currentY = event.clientY;

    if (event.pointerType === "mouse") return;
    emojiItem.classList.add("long-press-active");
    syncInteractionGuardState();
    setLongPressProgress(0, "选择");

    app.state.longPressState.intervalId = window.setInterval(() => {
      if (!app.state.longPressState.emojiItem) {
        return;
      }

      const elapsed = performance.now() - app.state.longPressState.startTime;
      const progress = elapsed / app.state.LONG_PRESS_DURATION_MS;
      setLongPressProgress(progress, "选择");
    }, app.state.LONG_PRESS_TICK_MS);

    app.state.longPressState.timeoutId = window.setTimeout(() => {
      emojiItem.dataset.suppressClick = "true";
      cancelLongPress();
      if (!app.state.selectionState.enabled) app.selection.setSelectionMode(true);
      if (!app.selection.isEmojiSelected(category, emoji))
        app.selection.toggleEmojiSelection(category, emoji);
    }, app.state.LONG_PRESS_DURATION_MS);
  }

function finishLongPress(event) {
    if (
      !app.state.longPressState.emojiItem ||
      (typeof event.pointerId === "number" &&
        app.state.longPressState.pointerId !== null &&
        event.pointerId !== app.state.longPressState.pointerId)
    ) {
      return;
    }

    cancelLongPress();
  }

function isInternalEmojiDrag(event) {
    const dragTypes = Array.from(event.dataTransfer?.types || []);
    return dragTypes.includes("application/x-meme-emoji");
  }

function getDraggedEmojiPayload(event) {
    try {
      const rawPayload = event.dataTransfer?.getData(
        "application/x-meme-emoji",
      );
      if (!rawPayload) {
        return null;
      }
      const payload = JSON.parse(rawPayload);
      if (Array.isArray(payload?.items) && payload.items.length > 0) {
        const items = app.clipboard.dedupeEmojiItems(payload.items);
        return items.length > 0 ? { items } : null;
      }
      if (!payload?.category || !payload?.emoji) {
        return null;
      }
      return { items: [{ category: payload.category, emoji: payload.emoji }] };
    } catch {
      return null;
    }
  }

function hasMoveableItemsForTarget(items, targetCategory) {
    return app.clipboard.dedupeEmojiItems(items).some(
      (item) => item.category !== targetCategory,
    );
  }

function clearCategoryDropHighlights() {
    document
      .querySelectorAll(".category-drop-active")
      .forEach((categoryDiv) => {
        categoryDiv.classList.remove("category-drop-active");
      });
  }

function createDragProgressIndicator() {
    const indicator = document.createElement("div");
    indicator.className = "drag-progress-indicator";

    const ring = document.createElement("div");
    ring.className = "drag-progress-ring";

    const center = document.createElement("div");
    center.className = "drag-progress-center";

    const label = document.createElement("span");
    label.className = "drag-progress-label";
    label.textContent = "拖";

    center.appendChild(label);
    indicator.appendChild(ring);
    indicator.appendChild(center);

    return indicator;
  }

function bindEmojiInteractions(emojiItem, category, emoji) {
    const selectionIndicator = emojiItem.querySelector(".selection-indicator");
    if (selectionIndicator) {
      selectionIndicator.addEventListener("pointerdown", (event) => {
        event.stopPropagation();
      });
      selectionIndicator.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (!app.state.selectionState.enabled) {
          app.selection.setSelectionMode(true);
        }
        app.selection.toggleEmojiSelection(category, emoji);
      });
    }

    emojiItem.addEventListener("click", () => {
      if (emojiItem.dataset.suppressClick === "true") {
        emojiItem.dataset.suppressClick = "false";
        return;
      }
      if (emojiItem.classList.contains("emoji-load-error")) {
        app.thumbnails.retryEmojiPreview(emojiItem);
        return;
      }
      if (!app.state.selectionState.enabled) {
        void app.imagePreview.openImagePreview(
          category,
          emoji,
          emojiItem.dataset.previewDataUrl || "",
        );
        return;
      }
      app.selection.toggleEmojiSelection(category, emoji);
    });

    emojiItem.addEventListener("keydown", (event) => {
      if (event.target !== emojiItem || event.repeat || event.isComposing)
        return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        emojiItem.click();
      }
    });

    emojiItem.addEventListener("pointerdown", (event) => {
      startLongPress(emojiItem, category, emoji, event);
    });
  }
return { getDragItemsForEmoji, getDragReadyLabel, hasActiveDragInteraction, syncInteractionGuardState, updateDragHudPosition, stopDragAutoScroll, stepDragAutoScroll, ensureDragAutoScroll, showDragHud, hideDragHud, setLongPressProgress, resetLongPressVisual, cancelLongPress, updateActiveDropTarget, updatePointerDrag, finishPointerDrag, clearDragMode, armDragMode, startLongPress, finishLongPress, isInternalEmojiDrag, getDraggedEmojiPayload, hasMoveableItemsForTarget, clearCategoryDropHighlights, createDragProgressIndicator, bindEmojiInteractions };
}
