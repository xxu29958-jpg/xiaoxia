
// packCatalog: one page runtime owns shared state and DOM references.
export function createPackCatalog(app) {
function updateManagePackSemanticAppearance(packId) {
    const normalizedPackId = String(packId || "").trim();
    const pack = app.state.managePacksById.get(normalizedPackId);
    const content = document.getElementById("content");
    const knownStatuses = ["none", "partial", "complete"];
    const status = knownStatuses.includes(String(pack?.semantic_status || ""))
      ? String(pack.semantic_status)
      : "none";
    if (app.dom.packSemanticStatus) {
      app.dom.packSemanticStatus.hidden = !pack || status === "none";
    }

    knownStatuses.forEach((item) => {
      app.dom.packSemanticStatus?.classList.toggle(`semantic-${item}`, item === status);
      content?.classList.toggle(`pack-semantic-${item}`, item === status);
    });

    if (!pack || !app.dom.packSemanticStatusText) {
      app.library.applySemanticReviewData({ available: false });
      if (app.dom.packSemanticStatusText) {
        app.dom.packSemanticStatusText.textContent = "未语义化";
      }
      return;
    }

    const imageCount = Number(pack.image_count || 0);
    const completedCount = Number(pack.semantic_caption_done || 0);
    const semanticTotal = Number(pack.semantic_caption_total || 0);
    const failedCount = Number(pack.semantic_caption_failed || 0);
    if (status === "complete") {
      app.dom.packSemanticStatusText.textContent = `${imageCount} 张图片已完成语义化`;
      app.dom.packSemanticStatus.title =
        semanticTotal && semanticTotal !== imageCount
          ? `${imageCount} 张图片中有重复内容，共复用 ${semanticTotal} 条语义描述。`
          : `当前图包的 ${imageCount} 张图片均已有语义描述。`;
      return;
    }
    if (status === "partial") {
      const failureHint = failedCount > 0 ? `，${failedCount} 条失败` : "";
      app.dom.packSemanticStatusText.textContent = pack.semantic_files_changed
        ? `语义待更新，已有 ${completedCount} 条`
        : semanticTotal
          ? `语义化进度：${completedCount}/${semanticTotal}${failureHint}`
          : "语义化尚未全部完成";
      app.dom.packSemanticStatus.title = pack.semantic_files_changed
        ? "图包新增了图片，或原图片内容已被替换，需要继续语义化。"
        : "当前图包仍有图片的语义描述未完成。";
      return;
    }
    app.dom.packSemanticStatusText.textContent =
      imageCount > 0 ? "未语义化" : "图包为空，暂无语义数据";
    app.dom.packSemanticStatus.title =
      imageCount > 0
        ? "当前图包还没有可用的图片语义描述。"
        : "空图包无需语义化。";
    app.library.applySemanticReviewData({ available: false });
  }

async function refreshManagePackSummaries() {
    try {
      const response = await app.api.apiGet("packs");
      const packs = Array.isArray(response?.packs) ? response.packs : [];
      app.state.managePacksById = new Map(
        packs.map((pack) => [String(pack?.id || "").trim(), pack]),
      );
      Array.from(app.dom.managePackSelect?.options || []).forEach((option) => {
        const pack = app.state.managePacksById.get(String(option.value || "").trim());
        if (pack) {
          option.textContent = app.packTransfer.formatPackOptionLabel(pack);
        }
      });
      updateManagePackSemanticAppearance(app.state.activeManagePackId);
      await Promise.all([
        app.packTransfer.refreshPackExportCapability(app.state.activeManagePackId),
        app.vectors.refreshManagePackVectorStatus(app.state.activeManagePackId),
      ]);
      return packs;
    } catch (error) {
      console.warn("刷新图包语义状态失败:", error);
      return [];
    }
  }

function syncManagedPackQuery(managedPackId) {
    const nextUrl = new URL(window.location.href);
    const normalized = String(managedPackId || "").trim();
    if (normalized) {
      nextUrl.searchParams.set("managed_pack_id", normalized);
    } else {
      nextUrl.searchParams.delete("managed_pack_id");
    }
    window.history.replaceState(null, "", nextUrl.toString());
  }

function buildCatalogPageUrl() {
    return app.state.withCurrentAuthParams("../catalog/index.html", {
      view: "catalog",
      asset_token: window.MemeUI.navAuthToken || null,
    }).toString();
  }

async function openCatalogPage() {
    await app.state.ensureNavAuthToken();
    window.location.href = buildCatalogPageUrl();
  }

function isSingleEmptyPack(packs) {
    if (!Array.isArray(packs) || packs.length !== 1) {
      return false;
    }
    const onlyPack = packs[0] || {};
    return Number(onlyPack?.image_count || 0) === 0;
  }

async function maybeShowFirstUseCatalogGuide(packs) {
    if (app.state.firstUseCatalogGuideShown || !isSingleEmptyPack(packs)) {
      return;
    }

    app.state.firstUseCatalogGuideShown = true;
    const confirmed = await app.dialogs.showConfirm({
      title: "第一次使用？",
      description: "可以前往资源广场下载官方表情包哦。",
      confirmLabel: "前往广场",
    });
    if (!confirmed) {
      return;
    }
    await openCatalogPage();
  }

async function loadManagePackSwitcher(preferredPackId = "") {
    if (!app.dom.managePackSelect) {
      return [];
    }
    if (app.dom.retryManagePacksBtn) {
      app.dom.retryManagePacksBtn.hidden = true;
    }
    try {
      const response = await app.api.apiGet("packs");
      const packs = Array.isArray(response?.packs) ? response.packs : [];
      app.state.managePacksById = new Map(
        packs.map((pack) => [String(pack?.id || "").trim(), pack]),
      );
      app.dom.managePackSelect.innerHTML = "";

      if (!packs.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "暂无可用表情包";
        app.dom.managePackSelect.appendChild(option);
        app.dom.managePackSelect.add(new Option("＋ 创建新表情包…", "__create__"));
        app.dom.managePackSelect.disabled = false;
        app.state.activeManagePackId = "";
        void app.collection.loadCollectionReview();
        updateManagePackSemanticAppearance("");
        app.vectors.hideManagePackVectorStatus();
        await app.packTransfer.refreshPackExportCapability("");
        if (app.dom.deleteManagePackBtn) {
          app.dom.deleteManagePackBtn.disabled = true;
        }
        return packs;
      }

      let selectedPackId = "";
      app.state.defaultManagePackId = "";
      packs.forEach((pack) => {
        const option = document.createElement("option");
        option.value = String(pack.id || "").trim();
        option.textContent = app.packTransfer.formatPackOptionLabel(pack);
        if (!selectedPackId) {
          selectedPackId = option.value;
        }
        if (!app.state.defaultManagePackId && pack?.is_default) {
          app.state.defaultManagePackId = option.value;
        }
        app.dom.managePackSelect.appendChild(option);
      });

      if (!app.state.defaultManagePackId) {
        app.state.defaultManagePackId = selectedPackId;
      }
      app.dom.managePackSelect.add(new Option("＋ 创建新表情包…", "__create__"));

      app.dom.managePackSelect.disabled = false;
      if (app.dom.deleteManagePackBtn) {
        app.dom.deleteManagePackBtn.disabled = false;
      }
      const preferred = String(preferredPackId || "").trim();
      const canUsePreferred = packs.some(
        (item) => String(item?.id || "").trim() === preferred,
      );
      const canUseUrlPack = packs.some(
        (item) => String(item?.id || "").trim() === app.state.managedPackIdFromUrl,
      );
      if (canUsePreferred) {
        selectedPackId = preferred;
      } else if (canUseUrlPack) {
        selectedPackId = app.state.managedPackIdFromUrl;
      }
      app.dom.managePackSelect.value = selectedPackId;
      app.state.activeManagePackId = selectedPackId;
      void app.collection.loadCollectionReview();
      updateManagePackSemanticAppearance(selectedPackId);
      syncManagedPackQuery(selectedPackId);
      await Promise.all([
        app.packTransfer.refreshPackExportCapability(selectedPackId),
        app.vectors.refreshManagePackVectorStatus(selectedPackId),
      ]);

      await maybeShowFirstUseCatalogGuide(packs);
      return packs;
    } catch (error) {
      app.dom.managePackSelect.replaceChildren(new Option("资源包加载失败", ""));
      app.dom.managePackSelect.disabled = true;
      if (app.dom.retryManagePacksBtn) {
        app.dom.retryManagePacksBtn.hidden = false;
      }
      app.dialogs.showToast(error?.message || String(error), "error", "加载表情包失败");
      return [];
    }
  }

async function switchManagePack() {
    if (!app.dom.managePackSelect) {
      return;
    }
    const targetPackId = String(app.dom.managePackSelect.value || "").trim();
    if (
      !targetPackId ||
      targetPackId === app.state.activeManagePackId ||
      app.dom.managePackSelect.disabled
    ) {
      return;
    }

    app.dom.managePackSelect.disabled = true;
    app.imagePreview.closeImagePreview();
    app.state.lastMove = null;
    app.dom.moveResult.classList.add("hidden");
    const previousActivePackId = app.state.activeManagePackId;
    app.selection.clearSelections();
    app.state.activeManagePackId = targetPackId;
    void app.collection.loadCollectionReview();
    app.library.applySemanticReviewData({ available: false });
    updateManagePackSemanticAppearance(targetPackId);
    try {
      syncManagedPackQuery(targetPackId);
      await app.api.refreshUi({ emojis: true });
      await app.packTransfer.refreshPackExportCapability(targetPackId);
      app.dialogs.showToast(`已切换管理视图到 ${targetPackId}。`, "success", "切换成功");
      await app.vectors.maybeOfferVectorRebuild(targetPackId);
    } catch (error) {
      app.state.activeManagePackId = previousActivePackId;
      app.dom.managePackSelect.value = previousActivePackId;
      updateManagePackSemanticAppearance(previousActivePackId);
      syncManagedPackQuery(previousActivePackId);
      await Promise.all([
        app.packTransfer.refreshPackExportCapability(previousActivePackId),
        app.vectors.refreshManagePackVectorStatus(previousActivePackId),
      ]);
      app.dialogs.showToast(error?.message || String(error), "error", "切换失败");
    } finally {
      app.dom.managePackSelect.disabled = false;
      void app.collection.loadCollectionReview();
    }
  }

async function deleteCurrentManagePack() {
    if (!app.dom.managePackSelect) {
      return;
    }

    const targetPackId = String(
      app.state.activeManagePackId || app.dom.managePackSelect.value || "",
    ).trim();
    if (!targetPackId) {
      app.dialogs.showToast("请先选择要删除的表情包。", "warning", "删除失败");
      return;
    }

    const confirmed = await app.danger.showDangerConfirm({
      title: `删除表情包组「${targetPackId}」`,
      description:
        "该操作会删除整个表情包组（包括分类与图片）。删除后会自动切换到其他表情包；如果删空会自动创建一个空表情包。",
      actionLabel: "确认删除当前表情包组",
      countdown: 5,
    });
    if (!confirmed) {
      return;
    }

    app.widgets.setButtonBusy(app.dom.deleteManagePackBtn, "删除中...");
    try {
      const data = await app.api.apiPost("packs/uninstall", { pack_id: targetPackId });
      const switchedPackId = String(data?.switched_default_to || "").trim();
      await loadManagePackSwitcher(switchedPackId);
      await app.api.refreshUi({ emojis: true, syncStatus: true });
      const switchedHint = switchedPackId ? `，已切换到 ${switchedPackId}` : "";
      const createdHint = data?.auto_created_empty_pack
        ? "（已自动创建空表情包）"
        : "";
      app.dialogs.showToast(
        `已删除 ${targetPackId}${switchedHint}${createdHint}`,
        "success",
        "删除成功",
      );
    } catch (error) {
      app.dialogs.showToast(error?.message || String(error), "error", "删除失败", 4500);
    } finally {
      app.widgets.restoreButton(app.dom.deleteManagePackBtn);
    }
  }

async function installOfficialFirstPackFromHint(triggerBtn) {
    app.widgets.setButtonBusy(triggerBtn, "安装中...");
    try {
      const data = await app.api.apiPost("community/install_official_first", {
        overwrite: false,
        set_as_default: true,
      });
      const installedPackId = String(data?.pack_id || "").trim();
      await loadManagePackSwitcher(installedPackId);
      await app.api.refreshUi({ emojis: true, syncStatus: true });
      const installedName = String(
        data?.selected_pack_name ||
          data?.name ||
          installedPackId ||
          "官方表情包",
      );
      app.dialogs.showToast(
        `已安装 ${installedName}，并切换为默认表情包。`,
        "success",
        "安装成功",
      );
    } catch (error) {
      app.dialogs.showToast(error?.message || String(error), "error", "安装失败");
    } finally {
      app.widgets.restoreButton(triggerBtn);
    }
  }
return { updateManagePackSemanticAppearance, refreshManagePackSummaries, syncManagedPackQuery, buildCatalogPageUrl, openCatalogPage, isSingleEmptyPack, maybeShowFirstUseCatalogGuide, loadManagePackSwitcher, switchManagePack, deleteCurrentManagePack, installOfficialFirstPackFromHint };
}
