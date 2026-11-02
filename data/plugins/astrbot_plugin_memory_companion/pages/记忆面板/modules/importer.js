
// importer: one page runtime owns shared state and DOM references.
export function createImporter(app) {
async function initialize() {
app.navigation.defineView("chatimport", {
  title: "历史聊天导入",
  navLabel: "历史聊天",
  eyebrow: "Import · Chat",
  hint: "从当前 QQ 连接读取时段记录，或导入已有聊天文件，确认身份后再生成记忆",
  async load() {
    if (app.values.importState.tab === "qq" && !app.values.importState.caps) {
      app.values.importState.caps = await app.api.apiTry(() => app.api.apiGet("/conversation-import/qq/capabilities"), null);
    }
    if (app.values.importState.batchId) {
      app.values.importState.status = await app.api.apiTry(
        () => app.api.apiGet("/conversation-import/status?batch_id=" + encodeURIComponent(app.values.importState.batchId)),
        null
      );
    }
    return { caps: app.values.importState.caps, preview: app.values.importState.preview, status: app.values.importState.status, tab: app.values.importState.tab };
  },
  render(data) {
    const caps = data.caps || {};
    const adapters = Array.isArray(caps.adapters) ? caps.adapters : [];
    const available = caps.available === true;
    const adapterError = adapters.map((item) => app.common.compact(item.error)).find((value) => value) || "";

    const qqPanel =
      '<div class="config-form">' +
      '<label class="field"><span>当前 Bot 连接</span><select id="qqPlatform"' + (available ? "" : " disabled") + ">" +
        (adapters.length
          ? adapters.map((p) => '<option value="' + app.common.esc(app.common.compact(p.platform_id)) + '">' + app.common.esc(app.common.compact(p.platform_name) || app.common.compact(p.platform_id)) + "</option>").join("")
          : '<option value="">等待能力检测</option>') +
      "</select></label>" +
      '<label class="field"><span>目标好友 QQ</span><input id="qqUserId" type="text" inputmode="numeric" placeholder="输入纯数字 QQ 号" /></label>' +
      '<div class="grid g2" style="gap:10px">' +
        '<label class="field"><span>开始时间</span><input id="qqStart" type="datetime-local" /></label>' +
        '<label class="field"><span>结束时间</span><input id="qqEnd" type="datetime-local" /></label>' +
      "</div>" +
      '<div class="pill-row">' +
        '<button class="btn is-sm is-ghost" type="button" id="qqCapBtn">重新检测</button>' +
        '<button class="btn is-primary" type="button" id="qqPreviewBtn">读取并生成预览</button>' +
      "</div>" +
      (caps.available === false ? '<p class="section-note">' + app.common.esc(adapterError || "当前连接不支持读取历史") + "</p>" : "") +
      "</div>";

    const filePanel =
      '<div class="config-form">' +
      '<label class="dropzone" id="fileDrop" for="fileInput">' +
        '<span aria-hidden="true" style="font-size:20px">＋</span>' +
        "<b>拖入聊天记录，或点击选择文件</b>" +
        "<small>支持 TXT、LOG、Markdown 和 QQChatExporter 私聊 JSON，最大 8 MiB</small>" +
      "</label>" +
      '<input id="fileInput" type="file" accept=".txt,.log,.md,.json,text/plain,text/markdown,application/json" style="display:none" />' +
      '<label class="field is-inline"><span>缺失年份</span><input id="fileBaseYear" type="number" min="1970" max="2200" placeholder="通常留空" /></label>' +
      '<div class="pill-row"><button class="btn is-primary" type="button" id="filePreviewBtn" disabled>解析并预览</button></div>' +
      '<p class="section-note" id="fileMeta">尚未选择文件</p>' +
      "</div>";

    const recentPanel =
      '<div class="config-form">' +
      '<div class="pill-row"><button class="btn is-sm" type="button" id="recentRefreshBtn">刷新状态</button></div>' +
      '<div id="recentList"><p class="section-note">点击刷新读取当前导入状态。</p></div>' +
      "</div>";

    const tabs = [
      ["qq", "QQ 直接读取"],
      ["file", "文件导入"],
      ["recent", "最近任务"],
    ]
      .map((item) => '<button class="pill' + (data.tab === item[0] ? " is-active" : "") + '" type="button" data-tab="' + item[0] + '">' + app.common.esc(item[1]) + "</button>")
      .join("");

    const output = data.preview
      ? app.widgets.card(
          "预览结果",
          app.common.compact(data.preview.source_name),
          app.importPreview.previewSummary(data.preview) +
            ((data.preview.warnings || []).length
              ? '<div class="tag-row" style="margin-top:8px">' + data.preview.warnings.map((w) => app.widgets.badge(app.common.clip(w, 40), "warn")).join("") + "</div>"
              : "") +
            app.importPreview.speakerConfirmHtml(data.preview)
        )
      : app.widgets.emptyState("预览会显示在这里", "先选择 QQ 时段或聊天文件。系统只会在你确认身份后开始生成记忆。");

    const statusCard = data.status
      ? app.widgets.card("导入进度", app.common.compact(data.status.state) || "进行中", '<pre style="margin:0;font-size:11.5px;line-height:1.6;color:var(--text-2);white-space:pre-wrap;word-break:break-all">' + app.common.esc(app.microscope.jsonPreview(data.status.result || data.status)) + "</pre>" +
          (app.values.importState.batchId
            ? '<div class="pill-row" style="margin-top:12px">' +
              '<button class="btn is-sm" type="button" id="impPauseBtn">暂停</button>' +
              '<button class="btn is-sm" type="button" id="impResumeBtn">继续</button>' +
              '<button class="btn is-sm is-danger" type="button" id="impRollbackBtn">回滚批次</button>' +
              "</div>"
            : ""))
      : "";

    return (
      '<div class="grid" style="gap:16px">' +
      '<div class="section-label"><h2>选择来源</h2><span class="section-note">两种来源共用身份确认与记忆整理流程</span></div>' +
      '<div class="pill-row">' + tabs + "</div>" +
      '<div class="grid split-2">' +
        app.widgets.card(
          data.tab === "qq" ? "QQ 直接读取" : data.tab === "file" ? "文件导入" : "最近任务",
          data.tab === "qq" ? "通过 NapCat / aiocqhttp 好友历史接口读取" : data.tab === "file" ? "适合接口不可用或已有导出文件" : "查看、继续或回滚批次",
          data.tab === "qq" ? qqPanel : data.tab === "file" ? filePanel : recentPanel
        ) +
        '<div style="display:grid;gap:16px">' + output + statusCard + "</div>" +
      "</div>" +
      "</div>"
    );
  },
  mount(node) {
    app.values.$$("[data-tab]", node).forEach((button) => {
      button.addEventListener("click", () => {
        app.values.importState.tab = button.dataset.tab;
        app.navigation.go("chatimport");
      });
    });

    const qqCap = app.values.$("#qqCapBtn", node);
    if (qqCap) {
      qqCap.addEventListener("click", async () => {
        await app.shell.withBusy("正在检测能力…", async () => {
          app.values.importState.caps = await app.api.apiTry(() => app.api.apiGet("/conversation-import/qq/capabilities"), null);
          app.shell.toast("能力检测完成", "ok");
          app.navigation.go("chatimport");
        });
      });
    }

    const qqPreview = app.values.$("#qqPreviewBtn", node);
    if (qqPreview) {
      qqPreview.addEventListener("click", async () => {
        const payload = {
          platform_id: app.values.$("#qqPlatform", node).value,
          user_id: app.values.$("#qqUserId", node).value.trim(),
          start_at: app.values.$("#qqStart", node).value,
          end_at: app.values.$("#qqEnd", node).value,
        };
        if (!payload.user_id) {
          app.shell.toast("请填写目标好友 QQ", "error");
          return;
        }
        await app.shell.withBusy("正在读取历史…", async () => {
          const result = await app.api.apiPost("/conversation-import/qq/preview", payload);
          app.values.importState.preview = result.result || result;
          app.shell.toast("预览已生成", "ok");
          app.navigation.go("chatimport");
        });
      });
    }

    const fileInput = app.values.$("#fileInput", node);
    if (fileInput) {
      fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        const meta = app.values.$("#fileMeta", node);
        const btn = app.values.$("#filePreviewBtn", node);
        if (!file) return;
        meta.textContent = file.name + " · " + (file.size / 1048576).toFixed(2) + " MiB";
        btn.disabled = false;
      });
    }

    const filePreview = app.values.$("#filePreviewBtn", node);
    if (filePreview) {
      filePreview.addEventListener("click", async () => {
        const file = fileInput.files && fileInput.files[0];
        if (!file) return;
        const buffer = await file.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(buffer);
        for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
        await app.shell.withBusy("正在解析文件…", async () => {
          const result = await app.api.apiPost("/conversation-import/upload", {
            filename: file.name,
            content_base64: window.btoa(binary),
            base_year: app.common.int(app.values.$("#fileBaseYear", node).value) || 0,
          });
          app.values.importState.preview = result.result || result;
          app.shell.toast("预览已生成", "ok");
          app.navigation.go("chatimport");
        });
      });
    }

    const recentRefresh = app.values.$("#recentRefreshBtn", node);
    if (recentRefresh) {
      recentRefresh.addEventListener("click", async () => {
        await app.shell.withBusy("正在读取状态…", async () => {
          const result = await app.api.apiTry(() => app.api.apiGet("/conversation-import/status"), null);
          const list = app.values.$("#recentList", node);
          const payload = result && result.result ? result.result : result;
          list.innerHTML =
            '<pre style="margin:0;font-size:11.5px;line-height:1.6;color:var(--text-2);white-space:pre-wrap;word-break:break-all">' +
            app.common.esc(app.microscope.jsonPreview(payload)) +
            "</pre>";
        });
      });
    }

    const startBtn = app.values.$("#impStartBtn", node);
    if (startBtn) {
      startBtn.addEventListener("click", async () => {
        const speakerMap = {};
        app.values.$$(".speaker-role", node).forEach((select) => {
          const idInput = app.values.$('[data-speaker-id="' + CSS.escape(select.dataset.speaker) + '"]', node);
          speakerMap[select.dataset.speaker] = {
            role: select.value,
            entity_id: idInput ? idInput.value.trim() : "",
            display_name: select.dataset.speaker,
          };
        });
        const payload = {
          upload_id: app.common.compact(app.values.importState.preview.upload_id),
          speaker_map: speakerMap,
          user_id: app.values.$("#impUserId", node).value.trim(),
          user_name: app.values.$("#impUserName", node).value.trim(),
          bot_id: app.values.$("#impBotId", node).value.trim(),
          bot_name: "Bot",
          platform: app.values.$("#impPlatform", node).value.trim() || "qq",
          session_id: app.values.$("#impSession", node).value.trim(),
        };
        if (!payload.user_id || !payload.bot_id) {
          app.shell.toast("请填写目标用户 ID 与 Bot ID", "error");
          return;
        }
        await app.shell.withBusy("正在生成记忆…", async () => {
          const result = await app.api.apiPost("/conversation-import/start", payload);
          const body = result.result || result;
          app.values.importState.batchId = app.common.compact(body.batch_id) || app.common.compact(body.id);
          app.values.importState.preview = null;
          app.pool.invalidatePool();
          app.shell.toast("导入已启动", "ok");
          app.navigation.go("chatimport");
        });
      });
    }

    const discard = app.values.$("#impDiscardBtn", node);
    if (discard) {
      discard.addEventListener("click", () => {
        app.values.importState.preview = null;
        app.navigation.go("chatimport");
      });
    }

    const bindBatch = (id, action) => {
      const button = app.values.$(id, node);
      if (!button) return;
      button.addEventListener("click", async () => {
        await app.shell.withBusy("正在处理…", async () => {
          await app.api.apiPost("/conversation-import/" + action, { batch_id: app.values.importState.batchId });
          app.shell.toast("操作已提交", "ok");
          app.navigation.go("chatimport");
        });
      });
    };
    bindBatch("#impPauseBtn", "pause");
    bindBatch("#impResumeBtn", "resume");
    bindBatch("#impRollbackBtn", "rollback");
  },
});
}
return { initialize };
}
