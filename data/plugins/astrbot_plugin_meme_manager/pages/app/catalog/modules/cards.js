import { setupCatalogUpdates } from "../updates.js";
// cards: one page runtime owns shared state and DOM references.
export function createCards(app) {
function createPackCard(pack, { forceOfficial = false } = {}) {
    const format = app.catalog.readPackFormat(pack);
    const card = document.createElement("article");
    card.className = `pack-card ${format.kind}-pack${
      forceOfficial ? " official" : ""
    }`;

    const isInstalled =
      app.state.installedStateKnown && app.state.installedPackIds.has(String(pack.id || "").trim());
    const updateState = app.state.updateStates[pack.id] || {};
    const tags = Array.isArray(pack.tags) ? pack.tags : [];

    const titleRow = document.createElement("div");
    titleRow.className = "pack-title-row";

    const titleWrap = document.createElement("div");
    const title = document.createElement("h3");
    title.className = "pack-title";
    title.textContent = pack.name || pack.id || "未命名";

    const id = document.createElement("p");
    id.className = "pack-id";
    id.textContent = `ID: ${pack.id || "-"}`;
    titleWrap.appendChild(title);
    titleWrap.appendChild(id);

    const canUpdate = isInstalled && updateState.available;
    const installBtn = document.createElement("button");
    installBtn.type = "button";
    installBtn.textContent = canUpdate
      ? "更新"
      : isInstalled
        ? "已安装"
        : "安装";
    installBtn.className = canUpdate
      ? "pack-update"
      : `pack-install${isInstalled ? " ghost" : ""}`;
    installBtn.dataset.installed = String(isInstalled);
    installBtn.disabled = canUpdate
      ? false
      : isInstalled || app.state.installBusy || !app.state.installedStateKnown;
    installBtn.setAttribute(
      "aria-label",
      `${installBtn.textContent} ${pack.name || pack.id || "未命名资源包"}`,
    );
    installBtn.addEventListener("click", () => {
      if (canUpdate) {
        void app.state.updates.open(pack, updateState);
        return;
      }
      app.install.openInstallDialog(pack.name || pack.id || "未命名", async (options) => {
        await app.install.installByPack(pack, installBtn, options);
      });
    });

    titleRow.appendChild(titleWrap);
    titleRow.appendChild(installBtn);

    const tagRow = document.createElement("div");
    tagRow.className = "tag-row";

    if (format.kind === "semantic") {
      const formatTag = document.createElement("span");
      formatTag.className = "tag format-tag semantic";
      const formatTagIcon = document.createElement("i");
      formatTagIcon.className = "fas fa-wand-magic-sparkles";
      const formatTagText = document.createElement("span");
      formatTagText.textContent = format.badge;
      formatTag.append(formatTagIcon, formatTagText);
      tagRow.appendChild(formatTag);
    }

    const verifyTag = document.createElement("span");
    verifyTag.className = `tag ${pack.verified ? "verified" : "unverified"}`;
    verifyTag.textContent = pack.verified ? "已验证" : "未验证";
    tagRow.appendChild(verifyTag);

    if (forceOfficial) {
      const officialTag = document.createElement("span");
      officialTag.className = "tag verified";
      officialTag.textContent = "官方";
      tagRow.appendChild(officialTag);
    }

    if (isInstalled) {
      const installedTag = document.createElement("span");
      installedTag.className = "tag installed";
      installedTag.textContent = updateState.available
        ? `可更新：${updateState.current_version} → ${updateState.latest_version}`
        : "已安装";
      tagRow.appendChild(installedTag);
    }

    for (const tag of tags.slice(0, 4)) {
      const span = document.createElement("span");
      span.className = "tag";
      span.textContent = String(tag);
      tagRow.appendChild(span);
    }

    const desc = document.createElement("p");
    desc.className = "pack-desc";
    desc.textContent = pack.description || "暂无描述";

    let formatNote = null;
    if (format.kind === "semantic") {
      formatNote = document.createElement("p");
      formatNote.className = "pack-format-note semantic";
      const formatNoteIcon = document.createElement("i");
      formatNoteIcon.className = "fas fa-cubes-stacked";
      const formatNoteText = document.createElement("span");
      formatNoteText.textContent = format.note;
      formatNote.append(formatNoteIcon, formatNoteText);
    }

    const meta = document.createElement("div");
    meta.className = "pack-meta";
    const maintainerMeta = document.createElement("span");
    maintainerMeta.textContent = `维护者: ${pack.maintainer || "未知"}`;
    const licenseMeta = document.createElement("span");
    licenseMeta.textContent = `协议: ${pack.license || "未知"}`;
    const sourceMeta = document.createElement("span");
    sourceMeta.textContent = `来源: ${pack.source?.repo || "-"}@${
      pack.source?.ref || "-"
    }`;
    meta.append(maintainerMeta, licenseMeta, sourceMeta);

    card.appendChild(app.covers.createPackCover(pack));
    card.appendChild(titleRow);
    card.appendChild(tagRow);
    card.appendChild(desc);
    if (formatNote) {
      card.appendChild(formatNote);
    }
    card.appendChild(meta);
    if (isInstalled) {
      const actions = document.createElement("div");
      actions.className = "pack-update-actions";
      if (updateState.backups?.length) {
        const restore = document.createElement("button");
        restore.type = "button";
        restore.className = "pack-update ghost";
        restore.textContent = "恢复备份";
        restore.disabled = app.state.installBusy;
        restore.addEventListener(
          "click",
          () => void app.state.updates.open(pack, updateState, true),
        );
        actions.append(restore);
      }
      if (actions.children.length) card.append(actions);
    }
    return card;
  }
return { createPackCard };
}
