import { openStorageGuide } from "../storage-guide.js";
// workflowView: one page runtime owns shared state and DOM references.
export function createWorkflowView(app) {
function updateView() {
    const query = app.dom.search.value.trim().toLocaleLowerCase();
    const semantic = Boolean(
      app.state.changes.get(app.state.modePaths[0]) ?? app.state.fields.get(app.state.modePaths[0])?.value,
    );
    const auxiliary = Boolean(
      app.state.changes.get(app.state.modePaths[1]) ?? app.state.fields.get(app.state.modePaths[1])?.value,
    );
    const mode = semantic
      ? auxiliary
        ? "semantic-auxiliary"
        : "semantic-tool"
      : auxiliary
      ? "category-auxiliary"
      : "category-native";
    const savedSemantic = Boolean(app.state.fields.get(app.state.modePaths[0])?.value);
    const savedAuxiliary = Boolean(app.state.fields.get(app.state.modePaths[1])?.value);
    const savedMode = savedSemantic
      ? savedAuxiliary
        ? "semantic-auxiliary"
        : "semantic-tool"
      : savedAuxiliary
      ? "category-auxiliary"
      : "category-native";
    if (app.state.dialogCard && query) app.dialogs.restoreDialogCard();
    app.dom.modePanel.hidden =
      !app.state.loaded ||
      (query
        ? ![
            "选图模式 原生 分类 标签 语义 检索 tool 情感 辅助 模型",
            ...app.state.modePaths,
            ...app.state.modePaths.map((path) => app.state.fields.get(path)?.label || ""),
          ]
            .join(" ")
            .toLocaleLowerCase()
            .includes(query)
        : app.state.active !== "models");
    for (const input of app.dom.modeInputs) {
      input.checked = input.value === mode;
      input.closest("label").classList.toggle("selected", input.checked);
    }
    app.dom.workflow.hidden = app.dom.modePanel.hidden;
    app.dom.sections.hidden = !query && app.state.active === "models";
    app.dom.collectionWorkflow.hidden =
      !app.state.loaded || Boolean(query) || app.state.active !== "collect";
    const collectionEnabled =
      app.state.changes.get("auto_collect.enabled") ??
      app.state.fields.get("auto_collect.enabled")?.value;
    const manualReview =
      app.state.changes.get("auto_collect.manual_review") ??
      app.state.fields.get("auto_collect.manual_review")?.value;
    document.getElementById(
      "collection-workflow-state",
    ).textContent = `当前选择：${collectionEnabled ? "开启" : "关闭"}`;
    document.getElementById("collection-workflow-result").textContent = `${
      collectionEnabled ? "" : "自动收集处于关闭状态，以上展示启用后的流程。"
    }${
      manualReview
        ? "当前选择：进入目标包的待审核列表 → 在表情包管理页预览、修正分类并接收。分类置信度不足时保留建议供你判断。"
        : "当前选择：新收集图片自动入包；分类置信度不足时归入 needs_review（待分类）。已有待审核图片仍需处理，目标包忙碌时新图片也会暂留待审核。"
    }`;
    const selectedModeName = app.dom.modeInputs
      .find((input) => input.checked)
      .closest("label")
      .querySelector("strong").textContent;
    document.getElementById(
      "mode-workflow-title",
    ).textContent = `${selectedModeName}流程`;
    if (app.dom.workflowSteps.dataset.mode !== mode) {
      app.dom.workflowSteps.replaceChildren();
      app.dom.workflowSteps.dataset.mode = mode;
      const selectionGroups = app.state.workspaceModes[mode].primary.filter(
        (id) =>
          ![
            "matching",
            "category-matching",
            "semantic",
            "semantic-models",
          ].includes(id),
      );
      const retrievalGroups = app.state.workspaceModes[mode].primary.filter((id) =>
        [
          "matching",
          "category-matching",
          "semantic",
          "semantic-models",
        ].includes(id),
      );
      const linkedGroups = new Set([
        ...app.state.workspaceModes[mode].primary,
        ...app.state.workspaceModes[mode].fallback,
        ...app.state.workspaceCommon,
        ...app.state.workspaceCollection,
        ...app.state.workspaceStorage,
      ]);
      const otherGroups = app.state.groups
        .filter((group) => !linkedGroups.has(group.id))
        .map((group) => group.id);
      const branches = [
        [
          ["自动收集", app.state.workspaceCollection],
          ["表情包使用规则", ["@rules"]],
        ],
        [["触发与数量", ["appearance", "quantity-guidance"]]],
        [
          ["当前模式", selectionGroups],
          ["其他模式的设置", otherGroups],
        ],
        [
          ["匹配与检索", retrievalGroups],
          ["不可用时的回退", app.state.workspaceModes[mode].fallback],
          ["图片来源与存储", app.state.workspaceStorage],
        ],
        [["发送方式", ["delivery"]]],
      ];
      for (const [index, [title, description]] of app.state.workflows[mode].entries()) {
        const item = document.createElement("li");
        const row = document.createElement("div");
        row.className = "mode-workflow-row";
        const node = document.createElement("div");
        node.className = "workflow-node";
        const label = document.createElement("strong");
        label.textContent = title;
        const detail = document.createElement("span");
        detail.textContent = description;
        node.append(label, detail);
        const branchList = document.createElement("div");
        branchList.className = "mode-workflow-branches";
        for (const [headingText, targetIds] of branches[index]) {
          if (!targetIds.length) continue;
          const branch = document.createElement("details");
          branch.className = "mode-workflow-branch";
          branch.open = [
            "触发与数量",
            "当前模式",
            "匹配与检索",
            "不可用时的回退",
            "发送方式",
            "表情包使用规则",
          ].includes(headingText);
          const heading = document.createElement("summary");
          heading.textContent = headingText;
          const links = document.createElement("div");
          links.className = "collection-workflow-links";
          for (const target of targetIds) {
            const extra = target.startsWith("@");
            const group = extra
              ? { title: "编辑使用规则" }
              : app.state.groups.find((item) => item.id === target);
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ghost";
            if (extra) button.dataset.workflowExtra = target.slice(1);
            else button.dataset.workflowTarget = target;
            button.textContent = group.title;
            links.append(button);
          }
          branch.append(heading, links);
          branchList.append(branch);
        }
        row.append(node, branchList);
        item.append(row);
        app.dom.workflowSteps.append(item);
      }
    }
    const modeName = app.dom.modeInputs
      .find((input) => input.value === savedMode)
      .closest("label")
      .querySelector("strong").textContent;
    document.getElementById(
      "selection-mode-status",
    ).textContent = `已保存：${modeName}${
      mode !== savedMode ? "，已选择新模式，待保存" : ""
    }${app.state.applicationFailed ? "，应用失败，请重新加载插件" : ""}`;
    document.getElementById("selection-mode-readiness").hidden = !semantic;
    const defaultPack = app.state.packs.find((pack) => pack.is_default);
    let readiness = "暂未获取到默认表情包的就绪状态，可前往语义化页面检查。";
    if (defaultPack && app.state.semanticStatus) {
      const name = defaultPack.name || defaultPack.id;
      readiness =
        app.state.semanticStatus.semantic_caption_complete && app.state.semanticStatus.index_ready
          ? `默认表情包「${name}」的语义数据与索引已就绪。`
          : `默认表情包「${name}」的语义数据或索引未就绪，使用该包的会话将回退到${
              auxiliary ? "辅助模型分类标签" : "原生分类标签"
            }。`;
    }
    if (app.state.changes.has("semantic.embedding_provider_id"))
      readiness = "向量模型已修改，保存后将重新检查默认表情包的索引状态。";
    document.getElementById("selection-mode-readiness-text").textContent =
      readiness;
    document.getElementById("selection-mode-detail").textContent = auxiliary
      ? "选图会额外调用辅助模型，默认复用当前对话的回复模型。切换模式会保留已填写的参数。"
      : semantic
      ? "需要回复模型支持 Tool 调用；工具不可用时回退到原生分类标签。"
      : "无需额外选图模型。可在「提示词模板」与「标签与匹配」中调整分类选图，在「表情发送」中调整公共发送设置。";
    app.dom.resetPrompts.hidden =
      Boolean(query) ||
      !(
        app.state.active === "prompts" ||
        (app.state.active === "models" &&
          ["prompts", "category-example", "reply-example"].includes(
            app.state.dialogCard?.id?.replace("config-group-", ""),
          ))
      );
    app.dom.configDialogReset.hidden = !(
      app.state.dialogCard &&
      ["prompts", "category-example", "reply-example"].includes(
        app.state.dialogCard.id.replace("config-group-", ""),
      )
    );
    const configVisible = Boolean(query) || Boolean(app.state.categories[app.state.active]);
    for (const panel of app.dom.panels) {
      panel.hidden = configVisible
        ? panel.dataset.settingsPanel !== "config"
        : panel.dataset.settingsPanel !== app.state.active;
      if (panel === app.dom.rulesPanel && app.dom.rulesDialog.open) panel.hidden = false;
    }
    for (const tab of app.dom.tabs) {
      if (!query && tab.dataset.settingsTab === app.state.active)
        tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
    const title = query
      ? ["搜索结果", "按名称或说明查找全部配置，也可搜索尚未启用的服务。"]
      : app.state.categories[app.state.active];
    if (title) {
      document.getElementById("config-section-title").textContent = title[0];
      document.getElementById("config-section-description").textContent =
        title[1];
    }
    const selectedProvider =
      app.state.changes.get("storage.provider") ?? app.state.fields.get("storage.provider")?.value;
    app.dom.sections.classList.toggle("storage-layout", app.state.active === "storage" && !query);
    let visibleCount = app.dom.modePanel.hidden ? 0 : 1;
    for (const group of app.state.groups) {
      const card = document.getElementById(`config-group-${group.id}`);
      if (!card) continue;
      let matches = 0;
      for (const row of card.querySelectorAll(".config-field")) {
        row.hidden =
          row.dataset.modeSwitch === "true" ||
          (Boolean(query) && !row.dataset.search.includes(query));
        if (!row.hidden) matches++;
      }
      card.hidden = query
        ? !matches
        : group.category !== app.state.active ||
          Boolean(group.provider && group.provider !== selectedProvider);
      if (!query && group.modes && !group.modes.includes(mode))
        card.hidden = true;
      if (card === app.state.dialogCard) card.hidden = false;
      card.querySelector(".config-mode-inactive")?.remove();
      if (!card.hidden && group.modes && !group.modes.includes(mode)) {
        const inactive = document.createElement("p");
        inactive.className = "config-mode-inactive config-card-description";
        inactive.textContent = app.state.workspaceModes[mode].fallback.includes(group.id)
          ? "当前模式仅在回退到分类选图时使用这些设置。"
          : "当前选图模式不使用这些设置；参数会保留。";
        card.querySelector("header").append(inactive);
      }
      if (!card.hidden) visibleCount += matches;
    }
    for (const column of app.dom.sections.querySelectorAll(".storage-column")) {
      column.hidden = ![...column.children].some((card) => !card.hidden);
    }
    document.getElementById("config-search-empty").hidden =
      !app.state.loaded || visibleCount > 0;
  }
return { updateView };
}
