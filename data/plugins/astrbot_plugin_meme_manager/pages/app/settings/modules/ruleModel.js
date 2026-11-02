
// ruleModel: one page runtime owns shared state and DOM references.
export function createRuleModel(app) {
function ensureDefaultRuleAtEnd(defaultPackId = "") {
    const normalRules = app.state.rules.filter((rule) => rule.scope !== "default");
    let defaultRule = app.state.rules.find((rule) => rule.scope === "default");
    if (!defaultRule) {
      defaultRule = {
        id: "default",
        scope: "default",
        pack_id: defaultPackId || app.state.installedPacks[0]?.id || "",
      };
    }
    app.state.rules = [...normalRules, defaultRule];
  }

function findDefaultRuleIndex() {
    return app.state.rules.findIndex((rule) => rule.scope === "default");
  }

function appendPackOptions(select, selectedPackId = "") {
    if (
      !app.state.installedPacks.some((pack) => String(pack.id) === String(selectedPackId))
    ) {
      const missingOption = document.createElement("option");
      missingOption.value = String(selectedPackId || "");
      missingOption.textContent = selectedPackId
        ? `表情包不存在（${selectedPackId}）`
        : "请选择表情包";
      missingOption.selected = true;
      select.appendChild(missingOption);
    }
    for (const pack of app.state.installedPacks) {
      const packId = String(pack.id || "");
      const option = document.createElement("option");
      option.value = packId;
      option.selected = packId === String(selectedPackId);
      option.textContent = `${pack.name || packId} (${packId})`;
      select.appendChild(option);
    }
  }

function getTargetSuggestions(scope) {
    if (scope === "persona") {
      return app.state.personaTargets
        .map((item) => String(item.id || "").trim())
        .filter(Boolean);
    }
    if (scope === "session") {
      return app.state.sessionTargets
        .map((item) => String(item || "").trim())
        .filter(Boolean);
    }
    return [];
  }

function updateRuleFromInput(index, key, value) {
    if (!app.state.rules[index]) {
      return;
    }
    if (
      key === "scope" &&
      String(value || "") === "default" &&
      String(app.state.rules[index].scope || "") !== "default"
    ) {
      return;
    }
    app.state.rules[index][key] = value;
    renderRulesValidation();
    app.status.updateRulesState();
  }

function moveRuleToIndex(fromIndex, toIndex) {
    if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0) {
      return;
    }

    const defaultIndex = findDefaultRuleIndex();
    if (defaultIndex < 0) {
      return;
    }
    if (fromIndex >= defaultIndex) {
      return;
    }
    if (toIndex >= defaultIndex) {
      toIndex = defaultIndex - 1;
    }
    if (toIndex < 0) {
      toIndex = 0;
    }

    const cloned = [...app.state.rules];
    const [item] = cloned.splice(fromIndex, 1);
    cloned.splice(toIndex, 0, item);
    app.state.rules = cloned;
    ensureDefaultRuleAtEnd();
    app.ruleView.renderRules();
    const movedRule = app.dom.rulesList.children[toIndex];
    movedRule?.querySelector(".drag-handle")?.focus();
    app.status.updateRulesState(`规则已移至第 ${toIndex + 1} 位，记得保存更改。`);
  }

function removeRule(index) {
    if (!app.state.rules[index] || app.state.rules[index].scope === "default") {
      return;
    }
    app.state.rules.splice(index, 1);
    app.ruleView.renderRules();
    const nextRule = app.dom.rulesList.children[Math.min(index, app.state.rules.length - 1)];
    nextRule
      ?.querySelector("button:not(:disabled), select:not(:disabled)")
      ?.focus();
  }

function getClientValidationErrors() {
    const errors = [];
    const idSet = new Set();
    const scopeTargetSet = new Set();
    let defaultCount = 0;

    app.state.rules.forEach((rule, index) => {
      const position = `第 ${index + 1} 条`;
      const id = String(rule.id || "").trim();
      const scope = String(rule.scope || "").trim();
      const packId = String(rule.pack_id || "").trim();
      const target = String(rule.target || "").trim();

      if (!id) {
        errors.push(`${position} 缺少规则标识，请删除后重新添加`);
      } else if (idSet.has(id)) {
        errors.push(`${position} 的规则标识重复，请删除后重新添加`);
      } else {
        idSet.add(id);
      }

      if (!["persona", "session", "default"].includes(scope)) {
        errors.push(`${position} 的匹配类型无效: ${scope || "(空)"}`);
      }
      if (!packId) {
        errors.push(`${position} 尚未选择表情包`);
      } else if (!app.state.installedPacks.some((pack) => String(pack.id) === packId)) {
        errors.push(`${position} 的表情包已不存在，请重新选择`);
      }

      if (scope === "default") {
        defaultCount += 1;
      }

      if (scope === "persona" || scope === "session") {
        if (!target) {
          errors.push(
            `${position} 尚未填写${scope === "persona" ? "人设" : "会话"}目标`,
          );
        } else {
          const key = `${scope}::${target}`;
          if (scopeTargetSet.has(key)) {
            errors.push(
              `${position} 与前面的规则重复匹配${
                scope === "persona" ? "人设" : "会话"
              }「${target}」`,
            );
          } else {
            scopeTargetSet.add(key);
          }
        }
      }
    });

    if (defaultCount !== 1) {
      errors.push("必须且仅能存在一条默认规则");
    }
    if (app.state.rules.length && app.state.rules[app.state.rules.length - 1]?.scope !== "default") {
      errors.push("默认规则必须位于最后");
    }

    return errors;
  }

function renderRulesValidation() {
    const errors = getClientValidationErrors();
    if (!errors.length) {
      app.dom.rulesValidation.classList.add("hidden");
      app.dom.rulesValidation.textContent = "";
      return true;
    }

    app.dom.rulesValidation.classList.remove("hidden");
    app.dom.rulesValidation.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = "规则存在问题，请先修复：";
    const errorList = document.createElement("ul");
    for (const error of errors) {
      const item = document.createElement("li");
      item.textContent = error;
      errorList.appendChild(item);
    }
    app.dom.rulesValidation.append(heading, errorList);
    return false;
  }
return { ensureDefaultRuleAtEnd, findDefaultRuleIndex, appendPackOptions, getTargetSuggestions, updateRuleFromInput, moveRuleToIndex, removeRule, getClientValidationErrors, renderRulesValidation };
}
