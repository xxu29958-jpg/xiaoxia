
// cinema: one page runtime owns shared state and DOM references.
export function createCinema(app) {
function applyMode(mode) {
  app.values.state.mode = mode === "cinema" ? "cinema" : "standard";
  document.documentElement.dataset.uiMode = app.values.state.mode;
  const button = app.values.$("#modeToggle");
  const label = app.values.$("#modeToggleLabel");
  if (button) button.setAttribute("aria-pressed", app.values.state.mode === "cinema" ? "true" : "false");
  if (label) label.textContent = app.values.state.mode === "cinema" ? "标准模式" : "终端模式";
  try {
    window.localStorage.setItem(app.values.MODE_KEY, app.values.state.mode);
  } catch (error) {
    /* 忽略存储禁用 */
  }
}

function renderCinemaOverview(data) {
  const stats = data.stats || {};
  const memories = Array.isArray(data.memories) ? data.memories : [];
  const coreRecords = (data.core || []).map((block) => ({
    id: block.id,
    scope: "core",
    memory_type: block.kind || "fact",
    canonical_summary: block.label || "核心记忆",
    content: block.content,
    isCore: true,
  }));
  const buckets = Array.isArray(data.buckets) ? data.buckets : [];
  const records = memories.length ? memories.slice(0, 32).concat(coreRecords.slice(0, 8)) : buckets.slice(0, 24).map((bucket, index) => ({
    id: "bucket-" + index,
    scope: bucket.scope,
    target_id: bucket.target_id,
    canonical_summary: bucket.label || bucket.target_name || bucket.target_id,
    content: bucket.sample_session_id || "此档案窗口暂未提供摘要",
    memory_count: bucket.memory_count,
    updated_at_local: bucket.last_activity_at || bucket.updated_at,
    isBucket: true,
  }));
  const cinemaChannel = (memory) => {
    const scope = app.common.compact(memory.scope);
    const type = app.common.compact(memory.memory_type);
    const visibility = app.common.compact(memory.visibility);
    if (scope === "core") return "core";
    if (scope === "personal" || visibility === "bot_self" || type === "companion_note" || type === "schedule") return "personal";
    if (scope === "profile" || type === "profile" || type === "preference" || type === "user_profile") return "profile";
    if (type === "relationship" || type === "relationship_claim" || type === "relationship_phase_summary") return "relationship";
    return "session";
  };
  const channelLabels = { session: "会话存档", profile: "用户画像", relationship: "关系脉络", core: "核心记忆", personal: "Bot 生活" };
  const recordButtons = records.map((memory, index) => {
    const scope = app.common.compact(memory.scope) || "private";
    const channel = cinemaChannel(memory);
    const type = app.common.compact(memory.memory_type) || (memory.isBucket ? "archive" : "memory");
    const title = app.records.memoryTitle(memory);
    const detail = memory.isBucket
      ? (app.common.compact(memory.target_id) || "未命名窗口") + " · " + app.common.fmtInt(memory.memory_count) + " 条记录"
      : (app.values.TYPE_LABEL[type] || type) + " · " + (app.values.SCOPE_META[scope] ? app.values.SCOPE_META[scope].label : scope);
    return '<button class="terminal-record" type="button" data-terminal-index="' + index + '" data-memory="' + app.common.esc(memory.isBucket || memory.isCore ? "" : memory.id) + '" data-core="' + app.common.esc(memory.isCore ? memory.id : "") + '" data-terminal-channel="' + app.common.esc(channel) + '" data-terminal-text="' + app.common.esc(title + " " + detail) + '">' +
      '<span class="terminal-record-id mono">' + String(index + 1).padStart(3, "0") + '</span><span class="terminal-record-channel">' + app.common.esc(channelLabels[channel] || channel) + '</span><strong>' + app.common.esc(app.common.clip(title, 72)) + '</strong><span class="terminal-record-meta">' + app.common.esc(detail) + '</span><i class="terminal-record-signal">●</i></button>';
  }).join("");
  return '<section class="terminal-stage" aria-label="Rhine Lab 记忆终端">' +
    '<div class="terminal-stage-top"><span class="terminal-kicker">RHINE LAB / MEMORY ARCHIVE</span><span class="terminal-session mono">SESSION M-' + String(app.common.int(stats.total_memories)).padStart(6, "0") + ' <i></i> AUTHORIZED</span></div>' +
    '<div class="terminal-scene-wrap"><div class="cinema-scene" id="cinemaScene" aria-label="三维档案阵列"><span class="scene-loading">ARCHIVE SCENE / LOADING</span></div><div class="terminal-scanline" aria-hidden="true"></div></div>' +
    '<div class="terminal-callout"><div class="terminal-label">INTERNAL DATABASE <span>/</span> <b id="terminalCategory">记忆档案</b></div><button class="terminal-file-title" id="terminalFileTitle" type="button"><span>FILE NUMBER: </span><strong id="terminalFileId">M-001</strong><span>↗</span></button><div class="terminal-rule"><i></i></div><div class="terminal-summary"><strong id="terminalSelectedTitle">等待档案</strong><span id="terminalSelectedMeta">MEMORY CHANNEL</span></div><button class="terminal-access" id="terminalAccess" type="button">ACCESS MEMORY <span>→</span></button></div>' +
    '<div class="terminal-feed-head"><span class="terminal-label">ARCHIVE STREAM / 记录带</span><label><span>/</span><input id="terminalSearch" type="search" placeholder="检索记录" autocomplete="off" /></label><b id="terminalResultCount">' + app.common.fmtInt(records.length) + ' RECORDS</b></div>' +
    '<div class="terminal-feed" id="terminalFeed">' + (recordButtons || app.widgets.emptyState("暂无记录", "连接后会在这里展开记忆流。")) + '</div>' +
    '<div class="terminal-controls"><div class="terminal-counter"><span>ARCHIVE / SELECT</span><strong><b id="terminalSelectedNumber">01</b><i>/</i><b>' + String(Math.max(records.length, 1)).padStart(2, "0") + '</b></strong></div><div class="terminal-arrows"><button type="button" data-terminal-prev aria-label="上一个记忆">↑</button><button type="button" data-terminal-next aria-label="下一个记忆">↓</button></div><div class="terminal-column"><button type="button" data-terminal-column-prev aria-label="上一记忆通道">←</button><span><small>MEMORY CHANNEL</small><b id="terminalChannel">ALL RECORDS</b></span><button type="button" data-terminal-column-next aria-label="下一记忆通道">→</button></div><span class="terminal-hint"><kbd>←</kbd><kbd>→</kbd> 通道 <span>/</span> <kbd>↑</kbd><kbd>↓</kbd> 档案 <span>/</span> <kbd>ENTER</kbd> 读取</span></div>' +
    '<div class="terminal-marquee"><span>ARCHIVE CHANNEL CONNECTED</span><i>●</i><span>PERSONAL CONTEXT READY</span><i>●</i><span>RECALL ENGINE STANDBY</span><i>●</i><span>ARCHIVE CHANNEL CONNECTED</span></div>' +
    '</section>';
}
return { applyMode, renderCinemaOverview };
}
