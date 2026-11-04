const reasons = {
  starting: "等待下一次检查",
  ready: "可以检查是否有值得分享的内容",
  sent: "最近一次已发送",
  skipped: "最近一次没有合适内容，保持安静",
  quiet_hours: "免打扰时段",
  recent_conversation: "刚聊过，暂不打扰",
  cooldown: "距离上次聊天还不够久",
  awaiting_reply: "已连续主动发过两次，等你回复",
  daily_limit: "今天已到次数上限",
  paused: "主动消息已暂停",
  conversation_closed: "尊重你刚才的离开或休息安排",
  context_changed: "聊天或渠道已变化，取消旧候选",
  material_changed: "浏览素材已变化，取消旧候选",
  delivery_uncertain: "上次发送未确认成功，不会自动重发",
  channel_unavailable: "聊天渠道未连接",
  error: "上次检查失败，稍后再检查",
};

function element(tag, text) {
  const el = document.createElement(tag);
  el.textContent = text;
  return el;
}

export function renderConversation(data = {}, action) {
  const status = document.getElementById("conversation-state");
  const reason = data.last_check?.reason;
  status.textContent = reasons[reason] || "等待下一次主动聊天检查";
  if (data.topic_review?.reason === "review_unavailable")
    status.textContent += "；话题复核暂时不可用，保留原话题";
  const topics = document.getElementById("topics");
  topics.replaceChildren();
  for (const topic of data.topics || []) {
    const row = element("div", "");
    row.className = "bookmark";
    const button = element("button", topic.kind === "goal" ? "结束目标" : "不再追问");
    button.onclick = () =>
      action("topic_close", { id: topic.id, expected: topic });
    row.append(
      element("strong", (topic.kind === "goal" ? "目标 · " : "") + topic.title),
      element("p", topic.content),
      button,
    );
    topics.append(row);
  }
  if (data.working_state?.ok === false)
    topics.replaceChildren(element("p", "后续事项暂时无法读取，请稍后刷新。"));
  else if (!topics.children.length)
    topics.append(element("p", "没有待续话题或进行中的目标。"));
  if (data.working_state?.next_offset != null)
    topics.append(element("p", `当前显示 ${(data.topics || []).length} 项，共 ${data.working_state.total} 项；其余可在聊天中按名称查找。`));
  const materials = document.getElementById("discoveries");
  materials.replaceChildren();
  for (const material of data.materials || []) {
    const row = element("div", "");
    row.className = "bookmark";
    const title = element("a", material.title || material.url);
    if (/^https?:\/\//i.test(material.url)) {
      title.href = material.url;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
    }
    row.append(title);
    if (material.source_quote)
      row.append(element("p", "网页摘录：" + material.source_quote));
    if (material.personal_comment)
      row.append(element("p", "随手感想：" + material.personal_comment));
    if (material.next_step)
      row.append(element("p", "想接着看：" + material.next_step));
    const dismiss = element("button", "暂不主动分享");
    dismiss.onclick = () => action("material_dismiss", { key: material.key });
    row.append(dismiss);
    materials.append(row);
  }
  if (!materials.children.length)
    materials.append(
      element(
        "p",
        data.materials_error ||
          "没有新的浏览素材；可以先一起逛逛、记一条随手记。",
      ),
    );
}
