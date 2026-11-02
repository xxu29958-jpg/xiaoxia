
// library: one page runtime owns shared state and DOM references.
export function createLibrary(app) {
function semanticReviewKey(category, filename) {
    return `${String(category || "")}\u0000${String(filename || "")}`;
  }

function semanticReviewLabel(status) {
    return (
      {
        auto_match: "自动符合",
        needs_review: "建议人工复核",
        manual_confirmed: "已人工确认",
        manual_rejected: "人工确认分类不符",
        unchecked: "尚未检查",
      }[String(status || "")] || "尚未检查"
    );
  }

function updateSemanticReviewToolbar() {
    if (!app.dom.semanticReviewStats) return;
    app.dom.semanticReviewStats
      .querySelectorAll("button[data-review-filter]")
      .forEach((button) => {
        const filter = String(button.dataset.reviewFilter || "all");
        const count =
          filter === "all"
            ? Number(app.state.semanticReviewStatistics.total || 0)
            : Number(app.state.semanticReviewStatistics[filter] || 0);
        const value = button.querySelector("span");
        if (value) value.textContent = String(count);
        button.classList.toggle(
          "active",
          filter === app.state.activeSemanticReviewFilter,
        );
        button.setAttribute(
          "aria-pressed",
          String(filter === app.state.activeSemanticReviewFilter),
        );
      });
  }

function applySemanticReviewData(payload) {
    app.state.semanticReviewAvailable = Boolean(payload?.available);
    const items =
      app.state.semanticReviewAvailable && Array.isArray(payload?.items)
        ? payload.items
        : [];
    app.state.semanticReviewByPath = new Map(
      items.map((item) => [
        semanticReviewKey(item?.category, item?.filename),
        item,
      ]),
    );
    app.state.semanticReviewStatistics = app.state.semanticReviewAvailable
      ? payload?.statistics || {}
      : {};
    if (!app.state.semanticReviewAvailable) {
      app.state.activeSemanticReviewFilter = "all";
    }
    app.dom.semanticReviewToolbar?.classList.toggle("hidden", !app.state.semanticReviewAvailable);
    updateSemanticReviewToolbar();
  }

async function fetchEmojis({ propagateError = false } = {}) {
    app.state.previews.invalidate();
    const requestSequence = ++app.state.libraryLoadSequence;
    const requestedPackId = app.state.activeManagePackId;
    document.getElementById("content").inert = true;
    app.dom.libraryLoadState.classList.remove("hidden", "error");
    app.dom.libraryLoadMessage.textContent = "正在加载表情包…";
    app.dom.libraryRetryBtn.classList.add("hidden");
    document
      .getElementById("emoji-categories")
      .setAttribute("aria-busy", "true");
    try {
      const [emojiResponse, tagDescriptions, reviewResult] = await Promise.all([
        app.api.apiGet("emoji"),
        app.api.apiGet("emotions"),
        app.api.apiGet("semantic/reviews").catch((error) => {
          console.warn("读取分类审核状态失败:", error);
          return { items: [], statistics: {} };
        }),
      ]);
      if (
        requestSequence !== app.state.libraryLoadSequence ||
        requestedPackId !== app.state.activeManagePackId
      )
        return false;
      app.drag.clearDragMode();
      app.contextMenu.closeBatchContextMenu();
      app.state.latestEmojiData = emojiResponse;
      app.state.latestTagDescriptions = tagDescriptions;
      applySemanticReviewData(reviewResult);
      app.selection.pruneSelectionState();
      displayCategories(emojiResponse, tagDescriptions);
      updateSidebar(emojiResponse, tagDescriptions);
      app.selection.updateSelectionUI();
      app.dom.libraryLoadState.classList.add("hidden");
    } catch (error) {
      if (
        requestSequence !== app.state.libraryLoadSequence ||
        requestedPackId !== app.state.activeManagePackId
      )
        return false;
      console.error("加载表情库失败：", error);
      app.dom.libraryLoadState.classList.add("error");
      app.dom.libraryLoadMessage.textContent = `表情包加载失败：${
        error?.message || "请检查连接"
      }`;
      app.dom.libraryRetryBtn.classList.remove("hidden");
      if (propagateError) throw error;
      return false;
    } finally {
      if (requestSequence === app.state.libraryLoadSequence) {
        document.getElementById("content").inert = false;
        document
          .getElementById("emoji-categories")
          .setAttribute("aria-busy", "false");
      }
    }
    return true;
  }

function displayCategories(emojiData, tagDescriptions) {
    const container = document.getElementById("emoji-categories");
    app.state.previewObserver?.disconnect();
    container.innerHTML = "";

    const categoryEntries = Object.entries(emojiData || {})
      .map(([category, emojis]) => {
        const categoryMatches = `${category} ${
          tagDescriptions?.[category] || ""
        }`
          .toLocaleLowerCase()
          .includes(app.state.librarySearch);
        const source = (Array.isArray(emojis) ? emojis : []).filter(
          (emoji) =>
            !app.state.librarySearch ||
            categoryMatches ||
            String(emoji).toLocaleLowerCase().includes(app.state.librarySearch),
        );
        if (app.state.activeSemanticReviewFilter === "all") {
          return [category, source];
        }
        return [
          category,
          source.filter((emoji) => {
            const review = app.state.semanticReviewByPath.get(
              semanticReviewKey(category, emoji),
            );
            if (app.state.activeSemanticReviewFilter === "reclassified") {
              return Boolean(review?.reclassification_status);
            }
            return (
              String(review?.category_review_status || "unchecked") ===
              app.state.activeSemanticReviewFilter
            );
          }),
        ];
      })
      .filter(
        ([, emojis]) =>
          (!app.state.librarySearch && app.state.activeSemanticReviewFilter === "all") ||
          emojis.length,
      );
    const totalEmojiCount = categoryEntries.reduce((total, [, emojis]) => {
      return total + (Array.isArray(emojis) ? emojis.length : 0);
    }, 0);
    document.getElementById("library-result-count").textContent =
      `共 ${totalEmojiCount} 张图片，分为 ${categoryEntries.length} 个分类`;

    if (!categoryEntries.length || totalEmojiCount === 0) {
      const hint = document.createElement("div");
      hint.className = "empty-pack-hint";
      hint.innerHTML =
        app.state.activeSemanticReviewFilter === "all" && !app.state.librarySearch
          ? `
        <p class="empty-pack-hint-title">当前还没有表情包内容</p>
        <p class="empty-pack-hint-meta">你可以先新建分类上传表情，或前往资源广场下载官方包；也可直接一键安装官方包。</p>
        <div class="empty-pack-hint-actions">
          <button id="empty-hint-install-official" type="button">一键安装官方包</button>
          <button id="empty-hint-create-category" type="button">新建分类</button>
          <a id="empty-hint-open-catalog" href="#">前往资源广场下载</a>
        </div>
      `
          : `<p class="empty-pack-hint-title">当前筛选条件下没有图片</p>
             <p class="empty-pack-hint-meta">试试其他名称，或清除搜索和审核筛选。</p>
             <div class="empty-pack-hint-actions"><button id="clear-library-filters" type="button">清除筛选</button></div>`;
      container.appendChild(hint);
      document
        .getElementById("clear-library-filters")
        ?.addEventListener("click", () => {
          app.state.librarySearch = "";
          app.dom.librarySearchInput.value = "";
          app.state.activeSemanticReviewFilter = "all";
          updateSemanticReviewToolbar();
          displayCategories(app.state.latestEmojiData, app.state.latestTagDescriptions);
          updateSidebar(app.state.latestEmojiData, app.state.latestTagDescriptions);
          app.dom.librarySearchInput.focus();
        });

      if (
        app.state.activeSemanticReviewFilter === "all" &&
        !app.state.librarySearch &&
        !app.state.emptyPackGuideShown
      ) {
        app.dialogs.showToast(
          "当前是空表情包，建议前往资源广场下载官方包。",
          "info",
          "提示",
        );
        app.state.emptyPackGuideShown = true;
      }

      const installOfficialBtn = document.getElementById(
        "empty-hint-install-official",
      );
      installOfficialBtn?.addEventListener("click", async () => {
        await app.packCatalog.installOfficialFirstPackFromHint(installOfficialBtn);
      });

      const createCategoryBtn = document.getElementById(
        "empty-hint-create-category",
      );
      createCategoryBtn?.addEventListener("click", () => {
        document.getElementById("add-category-btn")?.click();
      });

      const openCatalogLink = document.getElementById(
        "empty-hint-open-catalog",
      );
      if (openCatalogLink) {
        openCatalogLink.href = app.packCatalog.buildCatalogPageUrl();
      }
    }

    categoryEntries.forEach(([category, emojis]) => {
      const categoryDiv = document.createElement("div");
      categoryDiv.className = "category";
      categoryDiv.id = `category-${category}`;
      categoryDiv.dataset.category = category;

      const description = tagDescriptions[category] || `请添加描述`;
      const titleDiv = document.createElement("div");
      titleDiv.className = "category-title";
      const categorySelectedCount = app.selection.getCategorySelectedCount(category);
      const allSelectedInCategory =
        Array.isArray(emojis) &&
        emojis.length > 0 &&
        emojis.every((emoji) => app.selection.isEmojiSelected(category, emoji));
      const headerDiv = document.createElement("div");
      headerDiv.className = "category-header";

      const titleMain = document.createElement("div");
      titleMain.className = "category-title-main";

      const categoryName = document.createElement("div");
      categoryName.className = "category-name";
      categoryName.id = `category-name-${category}`;
      categoryName.textContent = category;

      const selectionSummary = document.createElement("span");
      selectionSummary.className = "category-selection-summary";
      selectionSummary.id = `category-selection-summary-${category}`;
      selectionSummary.textContent = app.state.selectionState.enabled
        ? `已选 ${categorySelectedCount} / ${emojis.length || 0}`
        : "未开启批量选择";

      titleMain.appendChild(categoryName);
      titleMain.appendChild(selectionSummary);

      const actionsDiv = document.createElement("div");
      actionsDiv.className = "category-actions";

      const editButton = app.widgets.createButton({
        className: "edit-category-btn",
        text: "编辑类别",
        onClick: () => app.categories.editCategory(category),
      });
      const toggleCategoryButton = app.widgets.createButton({
        className: "select-all-category-btn",
        text: app.state.selectionState.enabled
          ? allSelectedInCategory
            ? "取消本类"
            : "本类全选"
          : "本类选择",
        disabled: !Array.isArray(emojis) || emojis.length === 0,
        onClick: () => app.selection.toggleCategorySelection(category, emojis),
      });
      const clearCategoryButton = app.widgets.createButton({
        className: "clear-category-btn danger",
        text: "清空本类",
        onClick: () => app.mutations.clearCategory(category),
      });
      const deleteCategoryButton = app.widgets.createIconButton({
        className: "delete-category-btn icon-only-btn danger",
        iconClass: "fas fa-trash",
        title: `删除类别 ${category}`,
        ariaLabel: `删除类别 ${category}`,
        onClick: () => app.mutations.deleteCategory(category),
      });

      actionsDiv.appendChild(editButton);
      actionsDiv.appendChild(toggleCategoryButton);
      actionsDiv.appendChild(clearCategoryButton);
      actionsDiv.appendChild(deleteCategoryButton);

      headerDiv.appendChild(titleMain);
      headerDiv.appendChild(actionsDiv);

      const descriptionElement = document.createElement("p");
      descriptionElement.className = "description";
      descriptionElement.id = `category-desc-${category}`;
      descriptionElement.textContent = description;

      titleDiv.appendChild(headerDiv);
      titleDiv.appendChild(descriptionElement);
      categoryDiv.appendChild(titleDiv);

      const emojiGrid = document.createElement("div");
      emojiGrid.className = "emoji-grid";

      // emojis 是数组
      if (Array.isArray(emojis)) {
        emojis.forEach((emoji) => {
          const emojiItem = document.createElement("div");
          emojiItem.className = "emoji-item";
          emojiItem.dataset.category = category;
          emojiItem.dataset.emoji = emoji;
          emojiItem.dataset.suppressClick = "false";
          emojiItem.dataset.loading = "false";
          emojiItem.tabIndex = 0;
          emojiItem.setAttribute("role", "group");
          emojiItem.setAttribute(
            "aria-label",
            `${category} / ${emoji}，按回车预览`,
          );

          const review = app.state.semanticReviewByPath.get(
            semanticReviewKey(category, emoji),
          );
          if (app.state.semanticReviewAvailable && review) {
            const reviewStatus = String(
              review.category_review_status || "unchecked",
            );
            const reclassificationStatus = String(
              review.reclassification_status || "",
            );
            emojiItem.classList.add(`review-${reviewStatus}`);
            if (reclassificationStatus) {
              emojiItem.classList.add("review-reclassified");
            }
            emojiItem.dataset.categoryReviewStatus = reviewStatus;

            const semanticBadge = document.createElement("span");
            semanticBadge.className = "emoji-item-semantic-badge";
            const fixedCategoryTag = String(
              review.category_tag || `category:${category}`,
            );
            semanticBadge.textContent = `${fixedCategoryTag}，${
              reclassificationStatus ? "自动重分类，" : ""
            }${semanticReviewLabel(reviewStatus)}`;
            const reviewReason = String(
              review.category_review_reason || "",
            ).trim();
            semanticBadge.title = reclassificationStatus
              ? `${semanticBadge.textContent}；${String(
                  review.reclassified_from_category || "原分类",
                )} → ${String(
                  review.reclassified_to_category || category,
                )}；原因：${String(
                  review.reclassification_reason || reviewReason,
                )}`
              : reviewStatus === "needs_review" && reviewReason
                ? `${semanticBadge.textContent}；原因：${reviewReason}`
                : semanticBadge.textContent;
            emojiItem.appendChild(semanticBadge);
          }

          const selectionIndicator = document.createElement("button");
          selectionIndicator.type = "button";
          selectionIndicator.className = "selection-indicator";
          selectionIndicator.setAttribute("aria-label", "选择表情包");
          emojiItem.appendChild(selectionIndicator);

          // 删除按钮
          const deleteBtn = document.createElement("button");
          deleteBtn.className = "delete-btn";
          deleteBtn.innerHTML = "×";
          deleteBtn.setAttribute("aria-label", `删除 ${emoji}`);
          deleteBtn.onclick = (e) => {
            e.stopPropagation();
            app.mutations.deleteEmoji(category, emoji);
          };
          emojiItem.appendChild(deleteBtn);
          app.drag.bindEmojiInteractions(emojiItem, category, emoji);

          app.thumbnails.setEmojiPreviewLoading(emojiItem);
          emojiGrid.appendChild(emojiItem);
        });
      }

      if (app.state.activeSemanticReviewFilter === "all" && !app.state.librarySearch) {
        const { uploadBlock, fileInput } = app.upload.createUploadDropzone(category);

        // 筛选状态下不显示上传入口，避免把新图片误认为筛选结果。
        emojiGrid.appendChild(uploadBlock);
        emojiGrid.appendChild(fileInput);
      }

      categoryDiv.appendChild(emojiGrid);
      app.move.attachCategoryDropTarget(categoryDiv, category);
      container.appendChild(categoryDiv);
    });

    const lazyBackgrounds = container.querySelectorAll(".emoji-item");
    app.state.previewObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          entry.target.dataset.previewVisible = String(entry.isIntersecting);
          if (entry.isIntersecting) {
            void app.thumbnails.loadEmojiPreview(entry.target);
          }
        });
      },
      {
        rootMargin: "220px 0px",
        threshold: 0.01,
      },
    );

    lazyBackgrounds.forEach((item) => {
      app.state.previewObserver.observe(item);
    });

    app.selection.updateSelectionDecorations();
  }

function updateSidebar(data, tagDescriptions) {
    const sidebarList = document.getElementById("sidebar-list");
    if (!sidebarList) return;
    sidebarList.innerHTML = "";

    for (const category in data) {
      if (!document.getElementById(`category-${category}`)) continue;
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "#category-" + category;
      a.textContent = category;
      a.title = tagDescriptions?.[category] || category;
      a.addEventListener("click", () => {
        for (const link of sidebarList.querySelectorAll("a"))
          link.removeAttribute("aria-current");
        a.setAttribute("aria-current", "location");
        if (app.layout.isCompactViewport()) {
          app.layout.closeAllPanels();
          app.layout.updatePanelToggleState();
        }
      });
      li.appendChild(a);
      sidebarList.appendChild(li);
    }
  }
return { semanticReviewKey, semanticReviewLabel, updateSemanticReviewToolbar, applySemanticReviewData, fetchEmojis, displayCategories, updateSidebar };
}
