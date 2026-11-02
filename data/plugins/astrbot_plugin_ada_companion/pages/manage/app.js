import { renderConversation } from "./conversation.js";

const $ = (id) => document.getElementById(id);
let state,
  api,
  busy = false,
  version = 0;
const thumbnails = new Map();
const actions = {
  open: "打开",
  view: "查看画面",
  inspect: "读取控件",
  switch: "切换窗口",
  click: "点击",
  click_xy: "点击画面",
  type: "输入文字",
  key: "按键",
  scroll: "滚动",
  move: "移动鼠标",
  drag: "拖动",
  back: "后退",
  forward: "前进",
  wait: "等待",
  stop: "停止",
};
const outcomes = {
  observed: "已查看结果",
  failed_or_uncertain: "未确认成功",
  ok: "已完成",
  started: "已发起",
  stopped: "已停止",
  failed: "失败",
};
const categories = {
  happy: "开心",
  sad: "委屈",
  angry: "生气",
  confused: "疑惑",
  tired: "困倦",
  tease: "调侃",
  shy: "害羞",
  watch: "围观",
  ack: "赞同",
  affection: "亲近",
};
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
}
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
async function request(action, body = {}) {
  const stop = action === "desktop_stop" || action === "browser_stop";
  if (busy && !stop) return;
  if (!stop) busy = true;
  document
    .querySelectorAll("button:not(#desktop-stop):not(#browser-stop)")
    .forEach((el) => (el.disabled = true));
  try {
    const result = await api.apiPost("manage/action", { action, ...body });
    if (!result.ok) throw Error(result.error || "操作未成功");
    await refresh();
    notice(result.message);
  } catch (error) {
    notice(`没有确认成功：${error.message}`, true);
  } finally {
    if (!stop) busy = false;
    if (!busy)
      document
        .querySelectorAll("button")
        .forEach((el) => (el.disabled = false));
  }
}
async function refresh() {
  const result = await api.apiGet("manage/overview");
  state = result.data || result;
  if (!Array.isArray(state.models)) throw Error(result.error || "无法读取状态");
  $("connections").replaceChildren(
    ...state.channels_status.map((x) =>
      node(
        "p",
        `${x.channel === "qq" ? "QQ" : "微信"} · ${x.online ? "已连接" : "未连接，需检查登录"}`,
      ),
    ),
  );
  $("models").replaceChildren(
    ...state.models.map((x) => {
      const option = node("option", x.name + (x.available ? "" : " · 未配置"));
      option.value = x.id;
      option.disabled = !x.available;
      return option;
    }),
  );
  $("models").value = state.selected_model;
  $("initiative").textContent =
    `${state.enabled ? "开启" : "暂停"} · 今天 ${state.today_count}/4 次 · ${state.channels.map((x) => (x === "qq" ? "QQ" : "微信")).join("、") || "未选渠道"}`;
  $("proactive").textContent = state.enabled ? "暂停主动消息" : "恢复主动消息";
  renderConversation(state.conversation, request);
  $("gallery-status").textContent =
    state.gallery.error ||
    `${state.gallery.total} 张图片 · ${state.gallery.indexed} 张已入索引 · 今天收藏尝试 ${state.online_memes.attempts_today}/${state.online_memes.daily_limit}`;
  $("memes").textContent = state.gallery.enabled ? "暂停表情包" : "恢复表情包";
  $("memory-status").textContent =
    state.memory_connected && state.dialogue_healthy
      ? "共享记忆服务已连接"
      : "记忆服务需要检查";
  const b = state.public_browser;
  const d = state.desktop;
  $("desktop-state").textContent = d.running
    ? "正在操作"
    : d.enabled
      ? "可用，按你的指令操作"
      : "已关闭";
  $("desktop-switch").textContent = d.enabled ? "关闭桌面操作" : "开启桌面操作";
  $("desktop-window").textContent = d.last_window
    ? `最近观察：${d.last_window}`
    : "还没有观察窗口";
  $("desktop-usage").textContent =
    `最近 24 小时：${d.actions_24h}/${d.limit} 次操作。空闲 5 分钟自动释放服务。可用 Ctrl+Alt+Shift+Backspace 接管鼠标键盘。`;
  $("desktop-operations").replaceChildren(
    ...d.recent.map((x) =>
      node(
        "p",
        `${new Date(x.at * 1000).toLocaleTimeString("zh-CN")} · ${actions[x.action] || x.action} · ${outcomes[x.outcome] || x.outcome}`,
        "hint",
      ),
    ),
  );
  $("browser-state").textContent = b.running
    ? "正在访问网页"
    : b.enabled
      ? "可用，等待浏览"
      : "已关闭";
  $("browser-switch").textContent = b.enabled
    ? "关闭浏览器功能"
    : "开启浏览器功能";
  $("browser-url").textContent = b.current_url || "当前没有打开的页面";
  $("browser-usage").textContent =
    `最近 24 小时：${b.pages_24h || 0}/30 个页面，${b.actions_24h || 0}/300 次操作。截图缓存定期清理，收藏和登录状态保留。`;
  $("operations").replaceChildren(
    ...(b.recent || []).map((x) => {
      const row = node("div", "", "operation");
      row.append(
        node("span", new Date(x.at * 1000).toLocaleTimeString("zh-CN")),
        node(
          "span",
          `${actions[x.action] || x.action} · ${outcomes[x.result] || x.result}`,
        ),
        node("span", x.url),
      );
      return row;
    }),
  );
  $("bookmarks").replaceChildren(
    ...state.bookmarks.map((x) => {
      const row = node("div", "", "bookmark");
      const link = node("a", x.title || x.url);
      link.href = x.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(link, node("p", x.note, "hint"));
      return row;
    }),
  );
  if (!state.bookmarks.length)
    $("bookmarks").append(
      node("p", "还没有收藏。聊天里可以让她“收藏这个网站”。", "hint"),
    );
  await gallery();
}
async function gallery() {
  const current = ++version,
    query = $("filter").value.toLowerCase();
  const items = (state?.gallery.images || []).filter(
    (x) =>
      (!$("blocked-only").checked || x.blocked) &&
      `${x.caption} ${x.tags.join(" ")}`.toLowerCase().includes(query),
  );
  $("images").replaceChildren();
  const jobs = items.map((item) => {
    const card = node("article", "", "meme" + (item.blocked ? " blocked" : ""));
    const img = node("img");
    img.alt = item.caption || item.category;
    img.loading = "lazy";
    const button = node("button", item.blocked ? "恢复使用" : "禁用这张");
    button.onclick = () =>
      request(item.blocked ? "unblock" : "block", { digest: item.digest });
    card.append(
      img,
      node("p", item.caption),
      node(
        "span",
        item.blocked
          ? "已禁用"
          : item.recent
            ? "近期已发 · 暂不重复"
            : categories[item.category] || item.category,
        "tag",
      ),
      button,
    );
    $("images").append(card);
    return async () => {
      try {
        if (!thumbnails.has(item.digest)) {
          const r = await api.apiGet("manage/image", { digest: item.digest });
          if (!r.ok) throw Error(r.error);
          thumbnails.set(item.digest, r.src);
        }
        if (current === version) img.src = thumbnails.get(item.digest);
      } catch {
        if (current === version) img.alt = "预览失败，点击刷新重试";
      }
    };
  });
  if (!jobs.length)
    $("images").append(node("p", "没有符合条件的图片。", "empty"));
  let index = 0;
  await Promise.all(
    Array.from({ length: Math.min(4, jobs.length) }, async () => {
      while (index < jobs.length && current === version) await jobs[index++]();
    }),
  );
}
async function memory() {
  const r = await api.apiGet("manage/memory");
  if (!r.ok) throw Error(r.error || "记忆读取失败");
  $("core").replaceChildren();
  for (const item of r.blocks || []) {
    const row = node("div", "", "memory-block");
    const text = node("textarea");
    text.value = item.content;
    text.setAttribute("aria-label", item.label);
    const save = node("button", "保存这条偏好");
    save.onclick = async () => {
      save.disabled = true;
      try {
        const out = await api.apiPost("manage/memory", {
          action: "set",
          label: item.label,
          kind: item.kind,
          content: text.value,
          expected: item.content,
        });
        if (!out.ok) throw Error(out.error || "保存未完成");
        notice("核心偏好已保存，两边共用。");
        await memory();
      } catch (e) {
        notice(e.message, true);
      } finally {
        save.disabled = false;
      }
    };
    row.append(node("strong", item.label), text, save);
    $("core").append(row);
  }
  if (!r.blocks?.length)
    $("core").append(
      node("p", "暂时没有核心偏好，可在聊天里说“记住……”。", "hint"),
    );
}
$("model-save").onclick = () =>
  request("model", { provider_id: $("models").value });
$("proactive").onclick = () =>
  request("proactive", { enabled: !state.enabled });
$("memes").onclick = () =>
  request("memes", { enabled: !state.gallery.enabled });
$("reindex").onclick = () => request("reindex");
$("browser-switch").onclick = () =>
  request("browser_switch", { enabled: !state.public_browser.enabled });
$("browser-stop").onclick = () => request("browser_stop");
$("desktop-stop").onclick = () => request("desktop_stop");
$("desktop-switch").onclick = () =>
  request("desktop_switch", { enabled: !state.desktop.enabled });
$("filter").oninput = gallery;
$("blocked-only").onchange = gallery;
$("memory-load").onclick = () => memory().catch((e) => notice(e.message, true));
$("refresh").onclick = () =>
  refresh()
    .then(() => notice("状态已刷新。"))
    .catch((e) => notice(`机器人暂时不可达：${e.message}`, true));
try {
  api = window.AstrBotPluginPage;
  await api.ready();
  await refresh();
  notice("已连接。下面的设置对 QQ 和微信共同生效。");
} catch (error) {
  notice(`管理页连接失败：${error.message}。请先启动 AstrBot，再刷新。`, true);
}
