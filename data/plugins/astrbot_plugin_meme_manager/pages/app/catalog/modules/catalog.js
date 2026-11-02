import { setupCatalogUpdates } from "../updates.js";
// catalog: one page runtime owns shared state and DOM references.
export function createCatalog(app) {
async function refreshInstalledSet() {
    const requestId = ++app.state.installedRequest;
    try {
      const response = await app.api.apiGet("packs");
      if (requestId !== app.state.installedRequest) return;
      const packs = Array.isArray(response?.packs) ? response.packs : [];
      app.state.installedPackIds = new Set(
        packs.map((item) => String(item.id || "").trim()),
      );
      app.state.installedStateKnown = true;
      app.dom.installedStatus.classList.add("hidden");
      app.dom.catalogFilter.disabled = false;
      try {
        const updatesResponse = await app.api.apiGet("community/updates");
        if (requestId !== app.state.installedRequest) return;
        app.state.updateStates = updatesResponse.packs || {};
      } catch (error) {
        if (requestId !== app.state.installedRequest) return;
        app.state.updateStates = {};
        app.dom.installedStatus.textContent = "更新状态读取失败，请刷新资源重试。";
        app.dom.installedStatus.classList.remove("hidden");
      }
    } catch (error) {
      if (requestId !== app.state.installedRequest) return;
      app.state.installedStateKnown = false;
      app.dom.catalogFilter.value = "all";
      app.dom.catalogFilter.disabled = true;
      app.dom.installedStatus.textContent =
        "已安装状态暂未同步，点击「刷新资源」重试。";
      app.dom.installedStatus.classList.remove("hidden");
      app.status.addLog(`刷新已安装列表失败: ${error?.message || String(error)}`, true);
    }
  }

function readPacksFromCache() {
    const packs = app.state.cachedIndex?.index?.packs;
    if (!Array.isArray(packs)) {
      return [];
    }
    return packs.filter((item) => item && typeof item === "object");
  }

function isOfficialPack(pack) {
    const packId = String(pack?.id || "")
      .trim()
      .toLowerCase();
    const tags = Array.isArray(pack?.tags)
      ? pack.tags.map((tag) =>
          String(tag || "")
            .trim()
            .toLowerCase(),
        )
      : [];
    return packId.startsWith("official-") || tags.includes("official");
  }

function readPackFormat(pack) {
    // 旧版索引用标签描述格式，而没有显式的特征字段。
    const tags = new Set(
      (Array.isArray(pack?.tags) ? pack.tags : []).map((tag) =>
        String(tag || "")
          .trim()
          .toLowerCase(),
      ),
    );
    const features =
      pack?.features && typeof pack.features === "object" ? pack.features : {};
    const protocol =
      pack?.protocol && typeof pack.protocol === "object" ? pack.protocol : {};
    const formatVersion = Number(
      pack?.format_version || protocol?.format_version || 0,
    );
    const hasSemanticMetadata = Boolean(
      features.semantic_metadata ||
      pack?.semantic_metadata ||
      tags.has("semantic") ||
      tags.has("semantic-v2") ||
      tags.has("语义包"),
    );
    const isNewFormat = Boolean(
      hasSemanticMetadata ||
      formatVersion >= 2 ||
      tags.has("v2") ||
      tags.has("new-format"),
    );

    if (isNewFormat) {
      return {
        kind: "semantic",
        badge: hasSemanticMetadata ? "新版语义包" : "新版包",
        note: hasSemanticMetadata
          ? "包含可复用语义描述；不会分发本机向量，安装后可按当前向量模型重建。"
          : "采用新版包结构，兼容语义描述与本机向量重建流程。",
      };
    }
    return {
      kind: "classic",
    };
  }

function renderCatalog() {
    if (!app.state.cachedIndex) return;
    const query = app.dom.catalogSearch.value.trim().toLocaleLowerCase();
    const filter = app.dom.catalogFilter.value;
    const allPacks = readPacksFromCache();
    const packs = allPacks.filter((pack) => {
      const isInstalled =
        app.state.installedStateKnown &&
        app.state.installedPackIds.has(String(pack.id || "").trim());
      if (filter === "installed" && !isInstalled) return false;
      if (filter === "available" && isInstalled) return false;
      if (
        filter === "updates" &&
        (!isInstalled || !app.state.updateStates[pack.id]?.available)
      )
        return false;
      const searchable = [
        pack.name,
        pack.id,
        pack.description,
        pack.maintainer,
        pack.source?.repo,
        ...(Array.isArray(pack.tags) ? pack.tags : []),
      ]
        .join(" ")
        .toLocaleLowerCase();
      return !query || searchable.includes(query);
    });
    const updatablePacks = packs.filter(
      (pack) =>
        app.state.installedStateKnown &&
        app.state.installedPackIds.has(String(pack.id || "").trim()) &&
        app.state.updateStates[pack.id]?.available,
    );
    app.dom.updatesZone.classList.toggle("hidden", !updatablePacks.length);
    app.dom.updatesPackCount.textContent = String(updatablePacks.length);
    app.dom.updatesGrid.replaceChildren();
    for (const pack of updatablePacks) {
      app.dom.updatesGrid.appendChild(
        app.cards.createPackCard(pack, { forceOfficial: isOfficialPack(pack) }),
      );
    }
    for (const [grid, count, official] of [
      [app.dom.officialGrid, app.dom.officialPackCount, true],
      [app.dom.communityGrid, app.dom.communityPackCount, false],
    ]) {
      const group = packs.filter((pack) => isOfficialPack(pack) === official);
      const total = allPacks.filter(
        (pack) => isOfficialPack(pack) === official,
      ).length;
      count.textContent =
        query || filter !== "all"
          ? `${group.length} / ${total}`
          : String(total);
      grid.replaceChildren();
      grid.classList.toggle("empty", !group.length);
      if (!group.length) {
        const message = document.createElement("p");
        message.textContent =
          total && (query || filter !== "all")
            ? "没有符合条件的资源包，试试其他关键词或安装状态。"
            : `暂无${official ? "官方" : "社区"}包。`;
        grid.appendChild(message);
      } else {
        for (const pack of group) {
          grid.appendChild(app.cards.createPackCard(pack, { forceOfficial: official }));
        }
      }
    }
  }

async function fetchIndex() {
    if (app.state.indexLoading) return;
    app.state.indexLoading = true;
    app.status.setLoading(app.dom.refreshCatalogBtn, "刷新中…");
    app.dom.catalogStatus.classList.remove("error");
    app.dom.catalogStatus.textContent = app.state.cachedIndex
      ? "正在更新资源，当前显示上次缓存。"
      : "正在获取资源列表…";
    app.dom.officialGrid.setAttribute("aria-busy", "true");
    app.dom.communityGrid.setAttribute("aria-busy", "true");
    if (!app.state.cachedIndex) {
      for (const grid of [app.dom.officialGrid, app.dom.communityGrid]) {
        grid.textContent = "正在读取资源…";
      }
    }
    const installedRefresh = refreshInstalledSet();
    try {
      const response = await app.api.apiPost("community/index/fetch", {
        index_url: app.state.FIXED_INDEX_URL,
      });
      if (!Array.isArray(response?.index?.packs)) {
        throw new Error("资源列表格式不正确，请稍后重试");
      }
      app.state.cachedIndex = {
        fetched_at: response.fetched_at,
        source_url: response.source_url,
        index: response.index,
      };
      await installedRefresh;
      await refreshInstalledSet();
      renderCatalog();
      app.dom.catalogStatus.textContent = `已同步 ${
        readPacksFromCache().length
      } 个资源包，更新于 ${new Date().toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })}`;
      app.status.addLog(`索引拉取成功，共 ${response.pack_count || 0} 个条目`);
    } catch (error) {
      await installedRefresh;
      renderCatalog();
      app.dom.catalogStatus.classList.add("error");
      app.dom.catalogStatus.textContent = app.state.cachedIndex
        ? "更新失败，正在显示上次缓存。可点击「刷新资源」重试。"
        : `资源加载失败：${
            error?.message || String(error)
          }。请点击「刷新资源」重试。`;
      if (!app.state.cachedIndex) {
        for (const grid of [app.dom.officialGrid, app.dom.communityGrid]) {
          grid.textContent = "资源暂时不可用，请刷新重试。";
        }
      }
      app.status.addLog(`索引拉取失败: ${error?.message || String(error)}`, true);
    } finally {
      app.state.indexLoading = false;
      app.status.clearLoading(app.dom.refreshCatalogBtn);
      app.dom.officialGrid.setAttribute("aria-busy", "false");
      app.dom.communityGrid.setAttribute("aria-busy", "false");
    }
  }

async function loadCachedIndex({ silentOnMissing = false } = {}) {
    try {
      const response = await app.api.apiGet("community/index/cache");
      if (!Array.isArray(response?.index?.packs)) return false;
      app.state.cachedIndex = {
        fetched_at: response.fetched_at,
        source_url: response.source_url,
        index: response.index,
      };
      await refreshInstalledSet();
      renderCatalog();
      app.dom.catalogStatus.textContent = `已读取 ${
        readPacksFromCache().length
      } 个缓存资源包，正在检查更新…`;
      app.status.addLog(`已读取缓存索引，共 ${response.pack_count || 0} 个条目`);
      return true;
    } catch (error) {
      const errorMessage = error?.message || String(error);
      const isMissingCache = errorMessage.includes("缓存不存在");
      if (!(silentOnMissing && isMissingCache)) {
        app.status.addLog(`读取缓存失败: ${errorMessage}`, true);
      }
      return false;
    }
  }
return { refreshInstalledSet, readPacksFromCache, isOfficialPack, readPackFormat, renderCatalog, fetchIndex, loadCachedIndex };
}
