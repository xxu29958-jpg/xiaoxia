
// collection: one page runtime owns shared state and DOM references.
export function createCollection(app) {
async function loadCollectionReview({ announce = false } = {}) {
    const packId = app.state.activeManagePackId;
    const sequence = ++app.dom.collectionReview.sequence;
    if (app.dom.collectionReview.packId !== packId) {
      app.dom.collectionReview.dialog.close();
      app.dom.collectionReview.open.hidden = true;
      app.dom.collectionReview.items = [];
      app.dom.collectionReview.selected.clear();
      app.dom.collectionReview.edits.clear();
      app.dom.collectionReview.previews.clear();
      app.dom.collectionReview.filter.value = "";
      app.dom.collectionReview.packId = packId;
    }
    if (!packId) return;
    if (announce) app.dom.collectionReview.status.textContent = "正在读取待审核图片…";
    try {
      const data = await app.api.apiGet("auto-collect/inbox", { pack_id: packId });
      if (
        sequence !== app.dom.collectionReview.sequence ||
        packId !== app.state.activeManagePackId
      )
        return;
      app.dom.collectionReview.items = Array.isArray(data.items) ? data.items : [];
      app.dom.collectionReview.categories = data.categories || {};
      const counters = document.getElementById("collection-review-counters");
      counters.replaceChildren();
      for (const [key, label] of Object.entries({
        collected: "已收集",
        scope: "来源不匹配",
        sampling: "抽样跳过",
        platform_filtered: "平台标记过滤",
        queue_full: "后台队列已满",
        no_categories: "目标包没有分类",
        duplicate_or_rejected: "重复或已拒收",
        cooldown: "冷却跳过",
        quota: "识图额度已用完",
        model_rejected: "模型拒绝",
        inbox_full: "待审核池已满",
        snapshot_failed: "图片读取失败",
        failed: "处理失败",
        pack_busy: "表情包忙碌",
      })) {
        const entry = document.createElement("div");
        const term = document.createElement("dt");
        const value = document.createElement("dd");
        term.textContent = label;
        value.textContent = String(Number(data.counters?.[key] || 0));
        entry.append(term, value);
        counters.append(entry);
      }
      const ids = new Set(app.dom.collectionReview.items.map((item) => item.id));
      for (const id of app.dom.collectionReview.selected)
        if (!ids.has(id)) app.dom.collectionReview.selected.delete(id);
      for (const id of app.dom.collectionReview.edits.keys())
        if (!ids.has(id)) app.dom.collectionReview.edits.delete(id);
      for (const id of app.dom.collectionReview.previews.keys())
        if (!ids.has(id)) app.dom.collectionReview.previews.delete(id);
      app.dom.collectionReview.open.hidden = !data.count;
      app.dom.collectionReview.open.textContent = `待审核表情 (${Number(
        data.count || 0,
      )})`;
      const selectedFilter = app.dom.collectionReview.filter.value;
      app.dom.collectionReview.filter.replaceChildren(new Option("全部分类", ""));
      for (const category of [
        ...new Set(
          app.dom.collectionReview.items.map(
            (item) => item.suggested_category || "needs_review",
          ),
        ),
      ].sort()) {
        app.dom.collectionReview.filter.append(new Option(category, category));
      }
      app.dom.collectionReview.filter.value = selectedFilter;
      if (app.dom.collectionReview.filter.selectedIndex < 0)
        app.dom.collectionReview.filter.value = "";
      app.dom.collectionReview.status.textContent = `共 ${app.dom.collectionReview.items.length} 张待审核图片`;
      if (app.dom.collectionReview.dialog.open) renderCollectionReview();
    } catch (error) {
      if (
        sequence !== app.dom.collectionReview.sequence ||
        packId !== app.state.activeManagePackId
      )
        return;
      app.dom.collectionReview.status.textContent = `读取失败：${
        error.message || error
      }，请点击刷新重试。`;
      if (announce)
        app.dialogs.showToast(
          error.message || String(error),
          "error",
          "读取待审核图片失败",
        );
    }
  }

function renderCollectionReview() {
    const review = app.dom.collectionReview;
    const packId = review.packId;
    const items = review.items.filter(
      (item) =>
        !review.filter.value ||
        (item.suggested_category || "needs_review") === review.filter.value,
    );
    items.sort(
      (a, b) =>
        (review.sort.value === "desc" ? -1 : 1) *
        (Number(a.category_confidence || 0) -
          Number(b.category_confidence || 0) ||
          Number(a.meme_confidence || 0) - Number(b.meme_confidence || 0)),
    );
    review.grid.replaceChildren();
    review.selectAll.checked =
      items.length > 0 && items.every((item) => review.selected.has(item.id));
    review.selectAll.indeterminate =
      !review.selectAll.checked &&
      items.some((item) => review.selected.has(item.id));
    review.more.hidden = items.length <= review.limit;
    document.getElementById("collection-review-selection").textContent =
      `当前筛选 ${items.length} 张，已选 ${review.selected.size} 张`;
    review.accept.disabled = review.discard.disabled =
      review.busy || !review.selected.size;
    if (!items.length) {
      const empty = document.createElement("p");
      empty.textContent = review.items.length
        ? "此分类暂无待审核图片。"
        : "已全部处理，新的收集结果会显示在这里。";
      review.grid.append(empty);
    }
    for (const item of items.slice(0, review.limit)) {
      const card = document.createElement("article");
      card.className = "collection-review-card";
      const label = document.createElement("label");
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = review.selected.has(item.id);
      check.disabled = review.busy;
      check.addEventListener("change", () => {
        if (check.checked) review.selected.add(item.id);
        else review.selected.delete(item.id);
        renderCollectionReview();
      });
      label.append(check, document.createTextNode("选择此图"));
      const image = document.createElement("img");
      image.alt = item.caption || "待审核表情包";
      image.loading = "lazy";
      const preview =
        review.previews.get(item.id) ||
        app.api.apiGet(
          "auto-collect/inbox/image_data",
          {
            pack_id: packId,
            id: item.id,
          },
          { isCurrent: () => packId === review.packId && review.dialog.open },
        );
      review.previews.set(item.id, preview);
      Promise.resolve(preview)
        .then((data) => {
          if (packId === review.packId && image.isConnected && data?.data_url)
            image.src = data.data_url;
        })
        .catch(() => {
          review.previews.delete(item.id);
          image.alt = "预览加载失败，请刷新重试";
        });
      const category = document.createElement("select");
      category.setAttribute("aria-label", "接收分类");
      category.disabled = review.busy;
      const suggested = item.suggested_category || "needs_review";
      for (const name of [
        ...new Set([...Object.keys(review.categories), suggested]),
      ])
        category.append(new Option(name, name));
      category.value = review.edits.get(item.id) || suggested;
      category.addEventListener("change", () =>
        review.edits.set(item.id, category.value),
      );
      const confidence = document.createElement("p");
      confidence.className = "collection-review-confidence";
      confidence.textContent = `分类把握 ${Math.round(
        Number(item.category_confidence || 0) * 100,
      )}%，表情把握 ${Math.round(Number(item.meme_confidence || 0) * 100)}%`;
      const description = document.createElement("p");
      description.textContent = item.caption || item.reason || "暂无描述";
      card.append(label, image, category, confidence, description);
      if (typeof item.near_duplicate === "string" && item.near_duplicate) {
        const warning = document.createElement("div");
        warning.className = "collection-review-warning";
        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "collection-similar-trigger";
        trigger.textContent = "已有疑似相似图片";
        trigger.setAttribute("aria-expanded", "false");
        const panel = document.createElement("div");
        panel.className = "collection-similar-preview";
        panel.hidden = true;
        const similarImage = document.createElement("img");
        similarImage.alt = "已有的相似图片";
        const caption = document.createElement("p");
        caption.textContent = item.near_duplicate;
        const status = document.createElement("p");
        status.setAttribute("role", "status");
        panel.append(similarImage, caption, status);
        let pinned = false;
        const showSimilar = async () => {
          panel.hidden = false;
          trigger.setAttribute("aria-expanded", "true");
          const parts = item.near_duplicate.replace(/\\/g, "/").split("/");
          const filename = parts.pop();
          if (
            !filename ||
            !parts.length ||
            parts.some((part) => !part || part === ".." || part === ".")
          ) {
            status.textContent = "相似图片路径无效";
            return;
          }
          const key = `similar:${item.near_duplicate}`;
          status.textContent = "正在加载…";
          try {
            const request =
              review.previews.get(key) ||
              app.api.apiGet(
                "meme_image_data",
                {
                  managed_pack_id: packId,
                  category: parts.join("/"),
                  filename,
                  size: "preview",
                },
                {
                  priority: true,
                  isCurrent: () =>
                    packId === review.packId && review.dialog.open,
                },
              );
            review.previews.set(key, request);
            const data = await request;
            if (packId !== review.packId || !similarImage.isConnected) return;
            if (!data?.data_url) throw new Error("Missing preview");
            similarImage.src = data.data_url;
            status.textContent = "";
          } catch {
            review.previews.delete(key);
            status.textContent = "图片可能已移动或删除，移开后重试。";
          }
        };
        warning.addEventListener("pointerenter", showSimilar);
        trigger.addEventListener("focus", showSimilar);
        trigger.addEventListener("click", () => {
          pinned = !pinned;
          if (pinned) void showSimilar();
          else {
            panel.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
          }
        });
        warning.addEventListener("pointerleave", () => {
          if (!pinned) {
            panel.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
          }
        });
        warning.addEventListener("focusout", (event) => {
          if (!warning.contains(event.relatedTarget)) {
            pinned = false;
            panel.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
          }
        });
        warning.append(trigger, panel);
        card.append(warning);
      }
      review.grid.append(card);
    }
  }

async function submitCollectionReview(action) {
    const review = app.dom.collectionReview;
    if (
      review.busy ||
      !review.selected.size ||
      review.packId !== app.state.activeManagePackId
    )
      return;
    const packId = review.packId;
    const ids = [...review.selected];
    const body =
      action === "accept"
        ? {
            pack_id: packId,
            items: ids.map((id) => ({
              id,
              category:
                review.edits.get(id) ||
                review.items.find((item) => item.id === id)
                  ?.suggested_category ||
                "needs_review",
            })),
          }
        : {
            pack_id: packId,
            ids,
            remember_rejection: document.getElementById(
              "collection-review-remember",
            ).checked,
          };
    review.busy = true;
    review.dialog
      .querySelectorAll("button, input, select")
      .forEach((element) => (element.disabled = true));
    app.dom.managePackSelect.disabled = true;
    review.status.textContent =
      action === "accept" ? "正在接收所选图片…" : "正在丢弃所选图片…";
    try {
      const result = await app.api.apiPost(`auto-collect/inbox/${action}`, body);
      const failed = Number(result.failed || 0);
      const message =
        action === "accept"
          ? `已接收 ${Number(result.imported || 0)} 张，重复 ${Number(
              result.duplicates || 0,
            )} 张${failed ? `，失败 ${failed} 张，可重试` : ""}`
          : `已丢弃 ${Number(result.discarded || 0)} 张`;
      app.dialogs.showToast(message, failed ? "warning" : "success", "审核结果");
      await loadCollectionReview({ announce: true });
      review.status.textContent = message;
      if (action === "accept" && packId === app.state.activeManagePackId) {
        try {
          await app.api.refreshUi({ emojis: true, syncStatus: true });
        } catch (error) {
          review.status.textContent = `${message}；图库刷新失败，请刷新页面：${
            error.message || error
          }`;
        }
      }
    } catch (error) {
      review.status.textContent = `操作失败：${
        error.message || error
      }。选择已保留，可重试。`;
      app.dialogs.showToast(error.message || String(error), "error", "审核失败");
    } finally {
      review.busy = false;
      review.dialog
        .querySelectorAll("button, input, select")
        .forEach((element) => (element.disabled = false));
      app.dom.managePackSelect.disabled = false;
      renderCollectionReview();
    }
  }

async function initialize1() {
app.dom.collectionReview = {
    dialog: document.getElementById("collection-review-dialog"),
    open: document.getElementById("collection-review-open"),
    grid: document.getElementById("collection-review-grid"),
    filter: document.getElementById("collection-review-filter"),
    sort: document.getElementById("collection-review-sort"),
    status: document.getElementById("collection-review-status"),
    selectAll: document.getElementById("collection-review-select-all"),
    accept: document.getElementById("collection-review-accept"),
    discard: document.getElementById("collection-review-discard"),
    more: document.getElementById("collection-review-more"),
    items: [],
    categories: {},
    selected: new Set(),
    edits: new Map(),
    previews: new Map(),
    packId: "",
    sequence: 0,
    limit: 24,
    busy: false,
  };
app.dom.collectionReview.open.addEventListener("click", async () => {
    document.getElementById("collection-review-pack").textContent =
      app.dom.managePackSelect.selectedOptions[0]?.textContent || app.state.activeManagePackId;
    app.dom.collectionReview.limit = 24;
    app.dom.collectionReview.dialog.showModal();
    renderCollectionReview();
    await loadCollectionReview({ announce: true });
  });
document
    .getElementById("collection-review-close")
    .addEventListener("click", () => app.dom.collectionReview.dialog.close());
app.dom.collectionReview.dialog.addEventListener("cancel", (event) => {
    if (app.dom.collectionReview.busy) event.preventDefault();
  });
app.dom.collectionReview.dialog.addEventListener("close", () => {
    if (!app.dom.collectionReview.open.hidden) app.dom.collectionReview.open.focus();
  });
document
    .getElementById("collection-review-refresh")
    .addEventListener("click", () => {
      void loadCollectionReview({ announce: true });
    });
for (const control of [app.dom.collectionReview.filter, app.dom.collectionReview.sort])
    control.addEventListener("change", () => {
      app.dom.collectionReview.limit = 24;
      renderCollectionReview();
    });
app.dom.collectionReview.selectAll.addEventListener("change", () => {
    for (const item of app.dom.collectionReview.items.filter(
      (item) =>
        !app.dom.collectionReview.filter.value ||
        (item.suggested_category || "needs_review") ===
          app.dom.collectionReview.filter.value,
    )) {
      if (app.dom.collectionReview.selectAll.checked)
        app.dom.collectionReview.selected.add(item.id);
      else app.dom.collectionReview.selected.delete(item.id);
    }
    renderCollectionReview();
  });
app.dom.collectionReview.more.addEventListener("click", () => {
    app.dom.collectionReview.limit += 24;
    renderCollectionReview();
  });
app.dom.collectionReview.accept.addEventListener("click", () => {
    void submitCollectionReview("accept");
  });
app.dom.collectionReview.discard.addEventListener("click", () => {
    void submitCollectionReview("discard");
  });
}
return { loadCollectionReview, renderCollectionReview, submitCollectionReview, initialize1 };
}
