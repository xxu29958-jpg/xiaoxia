import { PreviewClient } from "../../shared/preview.js";
// inbox: one page runtime owns shared state and DOM references.
export function createInbox(app) {
function renderAutoInbox(data) {
    const visible = Boolean(data?.visible);
    app.state.pendingAutoInboxCount = Math.max(0, Number(data?.count || 0));
    app.dom.autoInboxPanel.classList.toggle("hidden", !visible);
    app.dom.autoInboxCount.textContent = String(app.state.pendingAutoInboxCount);
    app.dom.autoInboxItems.replaceChildren();
    if (!visible) return;
    const items = Array.isArray(data?.items) ? data.items : [];
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "panel-hint";
      empty.textContent = "当前语义包没有等待整理的自动收集图片。";
      app.dom.autoInboxItems.append(empty);
      return;
    }
    items.slice(0, 8).forEach((item) => {
      const row = document.createElement("div");
      row.className = "auto-inbox-item";
      const category = document.createElement("strong");
      category.textContent = item.suggested_category || "needs_review";
      const source = document.createElement("span");
      const sourceLabel = item.source_kind === "group" ? "群聊" : "个人";
      source.textContent = `${sourceLabel} ${item.source_id || "未知来源"}`;
      const receivedAt = document.createElement("time");
      const date = new Date(item.received_at || "");
      receivedAt.textContent = Number.isNaN(date.getTime())
        ? ""
        : date.toLocaleString();
      row.append(category, source, receivedAt);
      app.dom.autoInboxItems.append(row);
    });
    if (app.state.pendingAutoInboxCount > 8) {
      const more = document.createElement("p");
      more.className = "panel-hint";
      more.textContent = `另有 ${app.state.pendingAutoInboxCount - 8} 张等待处理。`;
      app.dom.autoInboxItems.append(more);
    }
  }
return { renderAutoInbox };
}
