
// ruleView: one page runtime owns shared state and DOM references.
export function createRuleView(app) {
function renderRules() {
    app.dom.rulesList.innerHTML = "";

    app.state.rules.forEach((rule, index) => {
      const isDefault = rule.scope === "default";
      const wrapper = document.createElement("div");
      wrapper.className = `rule-item${isDefault ? " default" : ""}`;
      wrapper.dataset.index = String(index);
      wrapper.setAttribute("role", "group");
      wrapper.setAttribute(
        "aria-label",
        isDefault ? "默认规则" : `第 ${index + 1} 条规则`,
      );

      const titleRow = document.createElement("div");
      titleRow.className = "rule-title-row";
      const title = document.createElement("div");
      const titleText = document.createElement("strong");
      titleText.textContent = isDefault ? "默认规则" : `规则 #${index + 1}`;
      title.appendChild(titleText);
      titleRow.appendChild(title);

      if (!isDefault) {
        const dragHandle = document.createElement("button");
        dragHandle.type = "button";
        dragHandle.className = "drag-handle";
        dragHandle.innerHTML =
          '<i class="fas fa-grip-vertical" aria-hidden="true"></i>';
        dragHandle.draggable = true;
        dragHandle.title = "拖动排序，或使用方向键上移、下移";
        dragHandle.setAttribute(
          "aria-label",
          `调整第 ${index + 1} 条规则顺序，使用上下方向键`,
        );
        dragHandle.addEventListener("keydown", (event) => {
          if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
          event.preventDefault();
          app.ruleModel.moveRuleToIndex(index, index + (event.key === "ArrowUp" ? -1 : 1));
        });
        titleRow.appendChild(dragHandle);
      }

      wrapper.appendChild(titleRow);

      const grid = document.createElement("div");
      grid.className = "rule-grid";

      const scopeField = document.createElement("div");
      scopeField.className = "field-row";
      scopeField.innerHTML = `
        <label for="rule-scope-${index}">匹配类型</label>
        <select id="rule-scope-${index}" data-role="scope">
          <option value="persona" ${
            rule.scope === "persona" ? "selected" : ""
          }>指定人设</option>
          <option value="session" ${
            rule.scope === "session" ? "selected" : ""
          }>指定会话</option>
          ${isDefault ? '<option value="default" selected>默认</option>' : ""}
        </select>
      `;

      const targetField = document.createElement("div");
      targetField.className = "field-row";
      const targetListId = `target-suggestions-${index}`;
      const targetPlaceholder =
        rule.scope === "persona"
          ? "选择或填写人设 ID"
          : rule.scope === "session"
          ? "选择或填写会话 ID"
          : "适用于其他所有情况";
      const targetSuggestions = app.ruleModel.getTargetSuggestions(rule.scope);
      const targetLabel = document.createElement("label");
      targetLabel.textContent =
        rule.scope === "persona"
          ? "人设目标"
          : rule.scope === "session"
          ? "会话目标"
          : "匹配范围";
      targetLabel.htmlFor = `rule-target-${index}`;
      const targetInputElement = document.createElement("input");
      targetInputElement.dataset.role = "target";
      targetInputElement.id = `rule-target-${index}`;
      targetInputElement.type = "text";
      targetInputElement.value = String(rule.target || "");
      targetInputElement.disabled = isDefault;
      targetInputElement.placeholder = targetPlaceholder;
      targetInputElement.setAttribute("list", targetListId);
      const targetList = document.createElement("datalist");
      targetList.id = targetListId;
      for (const suggestion of targetSuggestions) {
        const option = document.createElement("option");
        option.value = suggestion;
        targetList.appendChild(option);
      }
      targetField.append(targetLabel, targetInputElement, targetList);

      const packField = document.createElement("div");
      packField.className = "field-row";
      const packLabel = document.createElement("label");
      packLabel.textContent = "使用表情包";
      packLabel.htmlFor = `rule-pack-${index}`;
      const packSelectElement = document.createElement("select");
      packSelectElement.dataset.role = "pack";
      packSelectElement.id = `rule-pack-${index}`;
      app.ruleModel.appendPackOptions(packSelectElement, rule.pack_id);
      packField.append(packLabel, packSelectElement);

      grid.appendChild(scopeField);
      grid.appendChild(targetField);
      grid.appendChild(packField);
      wrapper.appendChild(grid);

      const actions = document.createElement("div");
      actions.className = "rule-actions";
      if (!isDefault) {
        actions.innerHTML = `
          <button type="button" class="ghost compact-btn" data-action="up" ${
            index === 0 ? "disabled" : ""
          } aria-label="上移第 ${index + 1} 条规则">↑ 上移</button>
          <button type="button" class="ghost compact-btn" data-action="down" ${
            index >= app.state.rules.length - 2 ? "disabled" : ""
          } aria-label="下移第 ${index + 1} 条规则">↓ 下移</button>
          <button type="button" class="danger compact-btn" data-action="remove" aria-label="删除第 ${
            index + 1
          } 条规则">删除规则</button>
        `;
        actions
          .querySelector('[data-action="up"]')
          .addEventListener("click", () => app.ruleModel.moveRuleToIndex(index, index - 1));
        actions
          .querySelector('[data-action="down"]')
          .addEventListener("click", () => app.ruleModel.moveRuleToIndex(index, index + 1));
      }
      wrapper.appendChild(actions);

      const scopeSelect = scopeField.querySelector('select[data-role="scope"]');
      const targetInput = targetField.querySelector(
        'input[data-role="target"]',
      );
      const packSelect = packField.querySelector('select[data-role="pack"]');

      scopeSelect.disabled = isDefault;
      scopeSelect.addEventListener("change", () => {
        const selectedScope = scopeSelect.value;
        app.ruleModel.updateRuleFromInput(index, "scope", scopeSelect.value);
        if (!app.state.rules[index] || app.state.rules[index].scope === "default") {
          renderRules();
          return;
        }

        // 切换范围需要重新选择目标，但已选表情包仍有效。
        const firstSuggestion = app.ruleModel.getTargetSuggestions(selectedScope)[0] || "";
        app.state.rules[index].target = firstSuggestion;
        renderRules();
        app.dom.rulesList.children[index]
          ?.querySelector('input[data-role="target"]')
          ?.focus();
      });

      targetInput.addEventListener("input", () => {
        app.ruleModel.updateRuleFromInput(index, "target", targetInput.value);
      });

      packSelect.addEventListener("change", () => {
        app.ruleModel.updateRuleFromInput(index, "pack_id", packSelect.value);
      });

      actions
        .querySelector('[data-action="remove"]')
        ?.addEventListener("click", () => {
          app.ruleModel.removeRule(index);
        });

      wrapper.addEventListener("dragstart", (event) => {
        if (isDefault || !event.target.closest(".drag-handle")) {
          event.preventDefault();
          return;
        }
        app.state.dragRuleIndex = index;
        wrapper.classList.add("dragging");
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", String(index));
        }
      });

      wrapper.addEventListener("dragend", () => {
        app.state.dragRuleIndex = -1;
        wrapper.classList.remove("dragging");
        app.dom.rulesList
          .querySelectorAll(".rule-item.drop-target")
          .forEach((item) => item.classList.remove("drop-target"));
      });

      wrapper.addEventListener("dragover", (event) => {
        if (app.state.dragRuleIndex < 0 || isDefault) {
          return;
        }
        event.preventDefault();
        wrapper.classList.add("drop-target");
      });

      wrapper.addEventListener("dragleave", () => {
        wrapper.classList.remove("drop-target");
      });

      wrapper.addEventListener("drop", (event) => {
        event.preventDefault();
        event.stopPropagation();
        wrapper.classList.remove("drop-target");
        if (app.state.dragRuleIndex < 0 || isDefault) {
          return;
        }
        app.ruleModel.moveRuleToIndex(app.state.dragRuleIndex, index);
      });

      app.dom.rulesList.appendChild(wrapper);
    });

    const defaultIndex = app.ruleModel.findDefaultRuleIndex();
    app.dom.rulesList.ondragover = (event) => {
      if (app.state.dragRuleIndex < 0) {
        return;
      }
      event.preventDefault();
    };
    app.dom.rulesList.ondrop = (event) => {
      if (app.state.dragRuleIndex < 0) {
        return;
      }
      event.preventDefault();
      app.ruleModel.moveRuleToIndex(app.state.dragRuleIndex, Math.max(defaultIndex - 1, 0));
    };

    app.ruleModel.renderRulesValidation();
    app.status.updateRulesState();
  }
return { renderRules };
}
