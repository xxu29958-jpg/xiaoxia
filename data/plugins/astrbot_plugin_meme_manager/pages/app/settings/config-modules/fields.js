import { openStorageGuide } from "../storage-guide.js";
// fields: one page runtime owns shared state and DOM references.
export function createFields(app) {
function renderFields(snapshot) {
    app.dialogs.restoreDialogCard();
    app.state.fields.clear();
    app.state.controls.clear();
    app.dom.sections.replaceChildren();
    const storagePrimary = document.createElement("div");
    storagePrimary.className = "storage-column storage-primary";
    const storageSecondary = document.createElement("div");
    storageSecondary.className = "storage-column storage-secondary";
    app.dom.sections.append(storagePrimary, storageSecondary);
    for (const group of app.state.groups) {
      const card = document.createElement("section");
      card.className = "panel config-card";
      card.id = `config-group-${group.id}`;
      const header = document.createElement("header");
      header.className = "config-card-header";
      const heading = document.createElement("h3");
      heading.id = `${card.id}-title`;
      heading.tabIndex = -1;
      card.setAttribute("aria-labelledby", heading.id);
      heading.textContent = group.title;
      if (group.icon) {
        const icon = document.createElement("i");
        icon.className = `fas ${group.icon}`;
        icon.setAttribute("aria-hidden", "true");
        heading.prepend(icon, document.createTextNode(" "));
      }
      header.append(heading);
      if (group.provider) {
        const guide = document.createElement("button");
        guide.type = "button";
        guide.className = "ghost storage-guide-trigger";
        guide.textContent = "图床配置教程";
        guide.setAttribute("aria-haspopup", "dialog");
        guide.addEventListener("click", () => openStorageGuide(group.provider));
        header.append(guide);
      }
      if (group.description) {
        const description = document.createElement("p");
        description.className = "config-card-description";
        description.textContent = group.description;
        header.append(description);
      }
      const body = document.createElement("div");
      body.className = "config-card-body";
      card.append(header, body);
      if (group.category === "storage") {
        (group.provider || group.id === "storage"
          ? storagePrimary
          : storageSecondary
        ).append(card);
      } else app.dom.sections.append(card);
    }
    for (const field of snapshot.fields) {
      const assigned = app.state.groups.find((item) =>
        item.prefixes.some((prefix) =>
          prefix.endsWith(".")
            ? field.path.startsWith(prefix)
            : field.path === prefix,
        ),
      );
      if (!assigned) throw new Error("配置分类已更新，请刷新页面后重试");
      field.category = assigned.category;
      app.state.fields.set(field.path, field);
      const row = document.createElement("div");
      row.className = `config-field${
        field.type === "bool" ? " config-switch-field" : ""
      }${["text", "list"].includes(field.type) ? " config-field-wide" : ""}`;
      row.dataset.modeSwitch = String(app.state.modePaths.includes(field.path));
      row.dataset.search = [
        app.state.labels[field.path] || field.label,
        field.hint,
        field.path,
        ...field.groups,
        assigned.title,
      ]
        .join(" ")
        .toLocaleLowerCase();
      const copy = document.createElement("div");
      const label = document.createElement("label");
      label.htmlFor = `config-${field.path.replaceAll(".", "-")}`;
      label.textContent = app.state.labels[field.path] || field.label;
      copy.append(label);
      const help = document.createElement("p");
      help.id = `${label.htmlFor}-hint`;
      help.className = "config-field-hint";
      help.textContent = app.state.hints[field.path] || field.hint;
      if (field.type === "list") help.textContent += " 每行填写一项。";
      if (field.secret)
        help.textContent = field.configured
          ? "已配置。留空保留原值，输入新值可替换。"
          : "尚未配置。保存后不会回显密钥。";
      copy.append(help);
      row.append(copy);
      const controlBox = document.createElement("div");
      controlBox.className = "config-control";
      let control;
      if (field.type === "bool") {
        control = document.createElement("input");
        control.type = "checkbox";
        control.className = "config-switch";
        control.setAttribute("role", "switch");
        control.checked = Boolean(field.value);
      } else if (
        field.options.length ||
        field.special.startsWith("select_provider") ||
        field.path === "auto_collect.target_pack_id"
      ) {
        control = document.createElement("select");
        if (field.options.length) {
          for (const option of field.options)
            control.add(new Option(app.state.optionLabels[option] || option, option));
        } else {
          let emptyLabel = "请选择模型";
          let options = app.state.providers.chat.map((provider) => [
            provider.model
              ? `${provider.id}（${provider.model}）`
              : provider.id,
            provider.id,
          ]);
          if (field.special === "select_provider_embedding") {
            emptyLabel = "自动选择可用向量模型";
            options = app.state.providers.embedding.map((provider) => [
              provider.model
                ? `${provider.id}（${provider.model}）`
                : provider.id,
              provider.id,
            ]);
          } else if (field.path === "generation.emotion.llm.provider_id")
            emptyLabel = "跟随当前对话的回复模型";
          else if (field.path === "auto_collect.target_pack_id") {
            emptyLabel = "跟随人设与会话规则";
            options = app.state.packs.map((pack) => [
              `${pack.name || pack.id} (${pack.id})`,
              pack.id,
            ]);
          }
          control.add(new Option(emptyLabel, ""));
          for (const [name, value] of options)
            control.add(new Option(name, value));
        }
        if (
          field.value &&
          ![...control.options].some((item) => item.value === field.value)
        )
          control.add(new Option(`${field.value}（当前不可用）`, field.value));
        control.value = field.value;
      } else if (["text", "list"].includes(field.type)) {
        control = document.createElement("textarea");
        control.rows = field.type === "text" ? 7 : 4;
        control.value =
          field.type === "list" ? field.value.join("\n") : field.value;
        control.spellcheck = false;
      } else {
        control = document.createElement("input");
        control.type = field.secret
          ? "password"
          : ["int", "float"].includes(field.type)
          ? "number"
          : "text";
        control.value = field.value;
        if (["int", "float"].includes(field.type)) {
          control.required = true;
          if (field.path !== "generation.emotion.max_memes_per_message") {
            control.min =
              field.bounds.min ??
              (/(top_k|\.timeout)$/.test(field.path) ? 1 : 0);
          }
          const max =
            field.bounds.max ??
            (/(min_score|min_meme_confidence|min_category_confidence)$/.test(
              field.path,
            )
              ? 1
              : null);
          if (max !== null) control.max = max;
          control.step = field.type === "int" ? "1" : "any";
        }
        if (field.secret) {
          control.autocomplete = "new-password";
          control.placeholder = field.configured
            ? "已保存，输入新值可替换"
            : "请输入";
        }
      }
      control.id = label.htmlFor;
      control.setAttribute("aria-describedby", help.id);
      control.dataset.configPath = field.path;
      controlBox.append(control);
      app.state.controls.set(field.path, control);
      if (field.secret) {
        const clear = document.createElement("button");
        clear.type = "button";
        clear.className = "ghost config-secret-clear";
        clear.textContent = "清除";
        clear.setAttribute("aria-label", `清除${label.textContent}`);
        clear.addEventListener("click", () => {
          if (app.state.changes.has(field.path) && app.state.changes.get(field.path) === "") {
            app.state.changes.delete(field.path);
            clear.textContent = "清除";
            control.placeholder = field.configured
              ? "已保存，输入新值可替换"
              : "请输入";
          } else {
            control.value = "";
            app.state.changes.set(field.path, "");
            clear.textContent = "撤销";
            control.placeholder = "保存后清除";
          }
          clear.setAttribute(
            "aria-label",
            `${clear.textContent === "撤销" ? "撤销清除" : "清除"}${
              label.textContent
            }`,
          );
          app.validation.updateState();
        });
        controlBox.append(clear);
      }
      row.append(controlBox);
      document
        .querySelector(`#config-group-${assigned.id} .config-card-body`)
        .append(row);
    }
    app.workflowView.updateView();
  }
return { renderFields };
}
