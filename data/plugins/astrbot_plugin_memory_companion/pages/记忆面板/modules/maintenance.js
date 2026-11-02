
// maintenance: one page runtime owns shared state and DOM references.
export function createMaintenance(app) {
async function initialize() {
app.values.PRESET_LABELS = { light: "轻量", standard: "标准", companion: "陪伴" };
app.values.migrateOutput = null;
app.navigation.defineView("migrate", {
  title: "维护与迁移",
  navLabel: "维护与迁移",
  eyebrow: "Import · Maintenance",
  hint: "运行预设、运维诊断、可移植数据、LivingMemory 迁移与可回滚审计",
  async load() {
    const preset = await app.api.apiTry(() => app.api.apiGet("/operations/preset"), { preset: {} });
    return { preset: preset.preset || {}, output: app.values.migrateOutput };
  },
  render(data) {
    const preset = data.preset || {};
    const current = app.common.compact(preset.preset) || app.common.compact(preset.current) || "-";

    const presets =
      '<div class="tool-block"><b>1. 运行预设</b>' +
      "<p>轻量关闭外部检索调用；标准平衡功能与成本；陪伴提高关系与连续性记忆预算。已有 Provider 选择不会被覆盖。</p>" +
      '<div class="tool-actions">' +
      Object.keys(app.values.PRESET_LABELS)
        .map((key) => '<button class="btn is-sm' + (current === key ? " is-primary" : "") + '" type="button" data-preset="' + key + '">' + app.values.PRESET_LABELS[key] + "</button>")
        .join("") +
      '<span class="badge" style="margin-left:auto">当前：' + app.common.esc(app.values.PRESET_LABELS[current] || current) + "</span>" +
      "</div></div>";

    const tools = [
      {
        title: "2. 运维诊断",
        desc: "查看检索路径、缓存命中率、模型 Token 与耗时。",
        action: "diagnosticsBtn",
        label: "生成诊断",
      },
      {
        title: "3. 可移植数据",
        desc: "仅用于本插件导出的 UTF-8 JSONL，包含记忆、身份、关系、时间线与 ACL。",
        input: "portablePath",
        placeholder: "导入时填写 JSONL 路径；导出无需填写",
        actions: [
          ["portableExportBtn", "导出"],
          ["portablePreviewBtn", "预览"],
          ["portableImportBtn", "导入"],
        ],
      },
      {
        title: "4. LivingMemory 导入",
        desc: "先预览可导入数量和跳过原因，再执行导入。当前策略只导入完整摘要。",
        input: "lmPath",
        placeholder: "LivingMemory 数据库路径，可留空自动扫描",
        actions: [
          ["lmPreviewBtn", "预览导入"],
          ["lmRunBtn", "执行导入"],
        ],
      },
      {
        title: "5. 内容修复",
        desc: "修复早期导入后只剩旧库编号的记忆。",
        input: "lmRepairPath",
        placeholder: "LivingMemory 数据库路径，可留空自动扫描",
        actions: [["lmRepairBtn", "修复内容"]],
      },
      {
        title: "6. 维护检查",
        desc: "修复索引、刷新统计并整理数据库状态。",
        actions: [["maintenanceBtn", "运行维护"]],
      },
      {
        title: "7. 汇总批次释放",
        desc: "被证据门禁隔离的批次会永远持有其时间线事件，那些原文不会再进入任何后续批次。填批次 ID 后由人工复核决定：重新排队（事件交还，可被新批次消费）或放弃（承认丢失，不再悬空）。",
        input: "releaseBatchId",
        placeholder: "summary_batch_... 批次 ID",
        actions: [
          ["releaseRetryBtn", "重新排队"],
          ["releaseDiscardBtn", "放弃并关闭"],
        ],
      },
      {
        title: "8. 可回滚记忆审计",
        desc: "先生成证据约束的预览，再明确应用；已应用批次可回滚。",
        audits: true,
        actions: [
          ["auditPreviewBtn", "生成预览"],
          ["auditStatusBtn", "读取状态"],
          ["auditApplyBtn", "应用"],
          ["auditRollbackBtn", "回滚"],
        ],
      },
      {
        title: "9. 危险清理",
        desc: "清空会先备份数据库，再删除记忆、权限规则、时间线、关系、身份与注入日志。",
        danger: true,
        actions: [["clearAllBtn", "清空全部记忆"]],
      },
    ]
      .map((tool) => {
        const input = tool.input
          ? '<input id="' + tool.input + '" type="text" placeholder="' + app.common.esc(tool.placeholder || "") + '" style="height:30px;padding:0 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px" />'
          : "";
        const audits = tool.audits
          ? '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
            '<input id="auditLimit" type="number" min="0" max="100" value="0" style="width:92px;height:30px;padding:0 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px" />' +
            '<input id="auditBatch" type="text" placeholder="audit_..." style="flex:1;min-width:150px;height:30px;padding:0 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px" />' +
            "</div>"
          : "";
        return (
          '<div class="tool-block"><b>' + app.common.esc(tool.title) + "</b><p>" + app.common.esc(tool.desc) + "</p>" +
          input + audits +
          '<div class="tool-actions">' +
          (tool.actions || [])
            .map((row) => '<button class="btn is-sm' + (tool.danger ? " is-danger" : "") + '" type="button" id="' + row[0] + '">' + app.common.esc(row[1]) + "</button>")
            .join("") +
          "</div></div>"
        );
      })
      .join("");

    return (
      '<div class="grid split-2">' +
      app.widgets.card("维护工具", "按顺序执行更稳妥", presets + tools) +
      app.widgets.card(
        "输出",
        "最近一次操作结果",
        data.output
          ? '<pre style="margin:0;font-size:11.5px;line-height:1.6;color:var(--text-2);white-space:pre-wrap;word-break:break-all">' + app.common.esc(app.microscope.jsonPreview(data.output)) + "</pre>"
          : app.widgets.emptyState("还没有输出", "执行任一操作后，结果会显示在这里。")
      ) +
      "</div>"
    );
  },
  mount(node) {
    const run = async (label, fn) => {
      await app.shell.withBusy(label + "…", async () => {
        try {
          app.values.migrateOutput = await fn();
          app.shell.toast(label + "完成", "ok");
        } catch (error) {
          app.values.migrateOutput = { error: error.message };
          app.shell.toast(label + "失败：" + error.message, "error");
        }
        app.navigation.go("migrate");
      });
    };

    app.values.$$("[data-preset]", node).forEach((button) => {
      button.addEventListener("click", () => run("应用预设", () => app.api.apiPost("/operations/preset", { preset: button.dataset.preset })));
    });

    const bind = (id, fn, label) => {
      const button = app.values.$(id, node);
      if (button) button.addEventListener("click", () => run(label, fn));
    };

    bind("#diagnosticsBtn", () => app.api.apiGet("/operations/diagnostics"), "生成诊断");
    bind("#portableExportBtn", () => app.api.apiPost("/data/export", {}), "导出数据");
    bind("#portablePreviewBtn", () => {
      const path = app.values.$("#portablePath", node).value.trim();
      if (!path) throw new Error("请先填写 JSONL 路径");
      return app.api.apiGet("/data/import/preview?path=" + encodeURIComponent(path));
    }, "预览导入");
    bind("#portableImportBtn", () => {
      const path = app.values.$("#portablePath", node).value.trim();
      if (!path) throw new Error("请先填写 JSONL 路径");
      return app.api.apiPost("/data/import/run", { path });
    }, "导入数据");
    bind("#lmPreviewBtn", () => {
      const input = app.values.$("#lmPath", node);
      const path = input ? input.value.trim() : "";
      return app.api.apiGet("/import/livingmemory/preview?path=" + encodeURIComponent(path));
    }, "预览迁移");
    bind("#lmRunBtn", () => app.api.apiPost("/import/livingmemory/run", { path: app.values.$("#lmPath", node).value.trim() }), "执行迁移");
    bind("#lmRepairBtn", () => app.api.apiPost("/maintenance/repair_livingmemory_content", { path: app.values.$("#lmRepairPath", node).value.trim() }), "修复内容");
    bind("#maintenanceBtn", () => app.api.apiPost("/maintenance", {}), "运行维护");
    bind("#releaseRetryBtn", () => {
      const batchId = app.values.$("#releaseBatchId", node).value.trim();
      if (!batchId) throw new Error("请先填写批次 ID");
      return app.api.apiPost("/maintenance/release_summary_batch", { batch_id: batchId, mode: "retry" });
    }, "释放批次事件");
    bind("#releaseDiscardBtn", async () => {
      const batchId = app.values.$("#releaseBatchId", node).value.trim();
      if (!batchId) throw new Error("请先填写批次 ID");
      const confirmed = await app.shell.showInlineConfirmation(
        "放弃该批次的事件",
        "这些原始消息将被标记为已总结，等于承认它们不会进入长期记忆。确定继续？",
        "放弃"
      );
      if (!confirmed) throw new Error("已取消");
      return app.api.apiPost("/maintenance/release_summary_batch", { batch_id: batchId, mode: "discard" });
    }, "放弃批次事件");
    bind("#auditPreviewBtn", () => app.api.apiPost("/maintenance/audit/preview", { limit: app.common.int(app.values.$("#auditLimit", node).value) }), "生成审计预览");
    bind("#auditStatusBtn", () => app.api.apiGet("/maintenance/audit/status?batch_id=" + encodeURIComponent(app.values.$("#auditBatch", node).value.trim())), "读取审计状态");
    bind("#auditApplyBtn", () => app.api.apiPost("/maintenance/audit/apply", { batch_id: app.values.$("#auditBatch", node).value.trim(), confirm: "应用" }), "应用审计");
    bind("#auditRollbackBtn", () => app.api.apiPost("/maintenance/audit/rollback", { batch_id: app.values.$("#auditBatch", node).value.trim(), confirm: "回滚" }), "回滚审计");
    bind("#clearAllBtn", async () => {
      const confirmed = await app.shell.showInlineConfirmation("清空全部记忆", "将清空全部记忆数据（会先备份数据库）。确定继续？", "清空");
      if (!confirmed) throw new Error("已取消");
      return app.api.apiPost("/maintenance/clear_all", { confirm: "清空" });
    }, "清空记忆");
  },
});
}
return { initialize };
}
