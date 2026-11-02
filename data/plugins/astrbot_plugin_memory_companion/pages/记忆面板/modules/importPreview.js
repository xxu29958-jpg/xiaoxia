
// importPreview: one page runtime owns shared state and DOM references.
export function createImportPreview(app) {
function previewSummary(result) {
  if (!result || typeof result !== "object") return "";
  const stats = result.stats && typeof result.stats === "object" ? result.stats : {};
  const speakers = Array.isArray(stats.speakers) ? stats.speakers : Object.keys(stats.speakers || {});
  const pairs = [
    ["来源", app.common.compact(result.source_name)],
    ["类型", app.common.compact(result.source_kind)],
    ["消息数", app.common.int(stats.message_count ?? stats.messages ?? stats.total ?? 0)],
    ["说话人", speakers.length ? speakers.join(" / ") : "-"],
    ["时间跨度", app.common.compact(stats.first_time) && app.common.compact(stats.last_time) ? app.common.compact(stats.first_time) + " → " + app.common.compact(stats.last_time) : "-"],
    ["截断", result.truncated ? "是" : "否"],
  ].filter((row) => row[1] !== "" && row[1] !== "-" || row[0] === "截断");
  return '<dl class="kv">' + pairs.map((row) => "<dt>" + app.common.esc(row[0]) + "</dt><dd>" + app.common.esc(row[1]) + "</dd>").join("") + "</dl>";
}

function speakerConfirmHtml(result) {
  const stats = (result && result.stats && typeof result.stats === "object" ? result.stats : {});
  const speakers = Array.isArray(stats.speakers) ? stats.speakers : Object.keys(stats.speakers || {});
  const suggestions = Array.isArray(result.speaker_suggestions) ? result.speaker_suggestions : [];
  const identity = (result && result.identity_context && typeof result.identity_context === "object") ? result.identity_context : {};
  const matches = identity.matches && typeof identity.matches === "object" ? identity.matches : {};
  const botInfo = identity.bot && typeof identity.bot === "object" ? identity.bot : {};

  const rows = speakers
    .map((speaker) => {
      const suggestion = suggestions.find((item) => app.common.compact(item.speaker) === app.common.compact(speaker));
      const role = app.common.compact(suggestion && suggestion.role) || (matches[speaker] ? "bot" : "user");
      const entity = app.common.compact(suggestion && suggestion.entity_id) || app.common.compact(matches[speaker] && (matches[speaker].entity_id || matches[speaker].id)) || "";
      return (
        '<div class="acl-row" style="grid-template-columns:minmax(0,1fr) 110px minmax(0,1fr)">' +
        '<div class="acl-node"><b>' + app.common.esc(speaker) + "</b></div>" +
        '<select data-speaker="' + app.common.esc(speaker) + '" class="speaker-role" style="height:30px;padding:0 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px">' +
          '<option value="user"' + (role === "bot" ? "" : " selected") + ">用户</option>" +
          '<option value="bot"' + (role === "bot" ? " selected" : "") + ">Bot</option>" +
        "</select>" +
        '<input data-speaker-id="' + app.common.esc(speaker) + '" class="speaker-entity" type="text" value="' + app.common.esc(entity) + '" placeholder="实体 ID（可留空）" style="height:30px;padding:0 8px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px" />' +
        "</div>"
      );
    })
    .join("");

  return (
    '<div class="section-label" style="margin-top:4px"><h2>确认身份</h2><span class="section-note">必须恰好一个 Bot，至少一个用户</span></div>' +
    (rows || '<p class="section-note">未能解析出说话人。</p>') +
    '<div class="grid g3" style="margin-top:12px">' +
      '<label class="field"><span>目标用户 ID</span><input id="impUserId" type="text" placeholder="例如 QQ 号" /></label>' +
      '<label class="field"><span>目标用户名称</span><input id="impUserName" type="text" placeholder="可留空" /></label>' +
      '<label class="field"><span>目标 Bot ID</span><input id="impBotId" type="text" placeholder="多 Bot 时必填" value="' + app.common.esc(app.common.compact(botInfo.bot_id) || app.common.compact(botInfo.id)) + '" /></label>' +
      '<label class="field"><span>平台</span><input id="impPlatform" type="text" value="qq" /></label>' +
      '<label class="field"><span>会话 ID</span><input id="impSession" type="text" placeholder="留空自动推断" /></label>' +
    "</div>" +
    '<div class="pill-row" style="margin-top:12px">' +
      '<button class="btn is-primary" type="button" id="impStartBtn">开始生成记忆</button>' +
      '<button class="btn is-ghost" type="button" id="impDiscardBtn">放弃预览</button>' +
    "</div>"
  );
}
return { previewSummary, speakerConfirmHtml };
}
