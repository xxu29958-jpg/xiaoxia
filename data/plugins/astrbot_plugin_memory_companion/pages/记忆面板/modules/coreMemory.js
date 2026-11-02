
// coreMemory: one page runtime owns shared state and DOM references.
export function createCoreMemory(app) {
async function initialize() {
app.values.CORE_KINDS = { rule: "规则", boundary: "边界", preference: "偏好", profile: "画像", fact: "事实", state: "稳定状态" };
app.values.CORE_SCOPES = { global: "全局 / Bot", private: "私聊用户", group: "群聊" };
app.navigation.defineView("core", {
  title: "核心记忆",
  navLabel: "核心记忆",
  eyebrow: "Library · Core",
  hint: "始终参与注入的稳定事实、规则与边界，按作用域与优先级排序",
  async load() {
    const result = await app.api.apiTry(() => app.api.apiGet("/core-memory"), { blocks: [] });
    const blocks = Array.isArray(result.blocks) ? result.blocks : [];
    blocks.sort((a, b) => app.common.int(b.priority) - app.common.int(a.priority));
    return { blocks };
  },
  render(data) {
    const blocks = data.blocks || [];
    const rows = blocks.length
      ? blocks
          .map(
            (block) =>
              '<button class="row" type="button" data-core="' + app.common.esc(block.id) + '">' +
              '<div class="row-main"><div class="row-title">' + app.common.esc(app.common.compact(block.label) || "未命名块") + "</div>" +
              '<div class="row-sub">' + app.common.esc(app.common.clip(app.common.compact(block.content), 84)) + "</div></div>" +
              '<div class="row-meta">' +
                app.widgets.badge(app.values.CORE_KINDS[app.common.compact(block.kind)] || app.common.compact(block.kind) || "事实", "gold") +
                app.widgets.badge(app.values.CORE_SCOPES[app.common.compact(block.scope)] || app.common.compact(block.scope) || "全局", "fact") +
                app.widgets.badge("P" + app.common.int(block.priority), "accent") +
                (block.enabled ? "" : app.widgets.badge("已停用", "warn")) +
              "</div></button>"
          )
          .join("")
      : app.widgets.emptyState("还没有核心记忆块", "核心记忆会在每次对话时优先注入，适合放稳定的人设规则与长期事实。");

    const form =
      '<form id="coreForm" class="config-form" autocomplete="off">' +
      '<input type="hidden" name="id" id="coreId" />' +
      '<input type="hidden" name="expected_revision" id="coreRevision" value="0" />' +
      '<div class="grid g3" style="gap:10px">' +
        '<label class="field"><span>Label</span><input name="label" id="coreLabel" type="text" maxlength="80" placeholder="例如 preferred_address" required /></label>' +
        '<label class="field"><span>类型</span><select name="kind" id="coreKind">' + Object.keys(app.values.CORE_KINDS).map((k) => '<option value="' + k + '">' + app.values.CORE_KINDS[k] + "</option>").join("") + "</select></label>" +
        '<label class="field"><span>作用域</span><select name="scope" id="coreScope">' + Object.keys(app.values.CORE_SCOPES).map((k) => '<option value="' + k + '">' + app.values.CORE_SCOPES[k] + "</option>").join("") + "</select></label>" +
        '<label class="field"><span>目标 ID</span><input name="target_id" id="coreTarget" type="text" maxlength="160" placeholder="私聊用户或群聊 ID" /></label>' +
        '<label class="field"><span>Bot ID</span><input name="bot_id" id="coreBot" type="text" maxlength="120" placeholder="留空表示全部 Bot" /></label>' +
        '<label class="field"><span>人格 ID</span><input name="persona_id" id="corePersona" type="text" maxlength="120" placeholder="留空表示全部人格" /></label>' +
      "</div>" +
      '<label class="field"><span>内容</span><textarea name="content" id="coreContent" rows="4" maxlength="4000" placeholder="要长期记住的内容" required></textarea></label>' +
      '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
        '<label class="field is-inline" style="width:auto"><span>优先级</span><input name="priority" id="corePriority" type="number" min="0" max="100" step="1" value="50" style="width:88px" /></label>' +
        '<label class="switch"><input type="checkbox" id="coreEnabled" checked /><span class="switch-track"></span><span>启用注入</span></label>' +
        '<div class="pill-row" style="margin-left:auto">' +
          '<button class="btn is-sm is-ghost" type="button" id="coreResetBtn">重置</button>' +
          '<button class="btn is-sm is-primary" type="submit">保存核心块</button>' +
        "</div>" +
      "</div>" +
      "</form>";

    return (
      '<div class="grid split-2">' +
        app.widgets.card("核心记忆块", (data.blocks || []).length + " 个 · 按优先级排序", '<div class="row-list">' + rows + "</div>") +
        app.widgets.card("编辑核心块", "留空 ID 表示新建", form) +
      "</div>"
    );
  },
  mount(node, data) {
    const blocks = (data.blocks || []).reduce((acc, block) => {
      acc[block.id] = block;
      return acc;
    }, {});

    const fill = (block) => {
      app.values.$("#coreId", node).value = block ? app.common.compact(block.id) : "";
      app.values.$("#coreRevision", node).value = block ? app.common.int(block.revision) : 0;
      app.values.$("#coreLabel", node).value = block ? app.common.compact(block.label) : "";
      app.values.$("#coreKind", node).value = block ? app.common.compact(block.kind) || "fact" : "fact";
      app.values.$("#coreScope", node).value = block ? app.common.compact(block.scope) || "global" : "global";
      app.values.$("#coreTarget", node).value = block ? app.common.compact(block.target_id) : "";
      app.values.$("#coreBot", node).value = block ? app.common.compact(block.bot_id) : "";
      app.values.$("#corePersona", node).value = block ? app.common.compact(block.persona_id) : "";
      app.values.$("#coreContent", node).value = block ? app.common.compact(block.content) : "";
      app.values.$("#corePriority", node).value = block ? app.common.int(block.priority) : 50;
      app.values.$("#coreEnabled", node).checked = block ? block.enabled !== false : true;
    };

    app.values.$$("[data-core]", node).forEach((row) => {
      row.addEventListener("click", () => fill(blocks[row.dataset.core]));
    });

    app.values.$("#coreResetBtn", node).addEventListener("click", () => fill(null));

    app.values.$("#coreForm", node).addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        id: app.values.$("#coreId", node).value.trim(),
        expected_revision: app.common.int(app.values.$("#coreRevision", node).value),
        label: app.values.$("#coreLabel", node).value.trim(),
        kind: app.values.$("#coreKind", node).value,
        scope: app.values.$("#coreScope", node).value,
        target_id: app.values.$("#coreTarget", node).value.trim(),
        bot_id: app.values.$("#coreBot", node).value.trim(),
        persona_id: app.values.$("#corePersona", node).value.trim(),
        content: app.values.$("#coreContent", node).value.trim(),
        priority: app.common.int(app.values.$("#corePriority", node).value),
        enabled: app.values.$("#coreEnabled", node).checked,
      };
      if (!payload.label || !payload.content) {
        app.shell.toast("Label 与内容都不能为空", "error");
        return;
      }
      await app.shell.withBusy("正在保存…", async () => {
        const result = await app.api.apiPost("/core-memory/upsert", payload);
        if (result && result.ok === false) {
          app.shell.toast("保存失败：" + (result.code || "未知原因"), "error");
          return;
        }
        app.pool.invalidatePool();
        app.shell.toast("核心记忆已保存", "ok");
        fill(null);
        app.navigation.refresh();
      });
    });
  },
});
}
return { initialize };
}
