
// overview: one page runtime owns shared state and DOM references.
export function createOverview(app) {
async function initialize() {
app.navigation.defineView("overview", {
  title: "总览",
  navLabel: "总览",
  eyebrow: "Overview",
  hint: "记忆库规模、范围分布与联动状态的整体快照",
  async load() {
    const [statsPayload, buckets, core, caps, coord, memories, personal] = await Promise.all([
      app.api.apiGet("/stats"),
      app.api.apiTry(() => app.api.apiGet("/buckets?limit=160"), { buckets: [] }),
      app.api.apiTry(() => app.api.apiGet("/core-memory"), { blocks: [] }),
      app.api.apiTry(() => app.api.apiGet("/capabilities/bot-personal"), {}),
      app.api.apiTry(() => app.api.apiGet("/coordination/status"), { status: {} }),
      app.api.apiTry(() => app.api.apiGet("/memories?limit=80"), { memories: [] }),
      app.api.apiTry(() => app.api.apiGet("/companion/personal-memory?limit=1"), null),
    ]);
    const stats = (statsPayload && statsPayload.stats) || {};
    app.values.state.stats = stats;
    app.values.state.buckets = Array.isArray(buckets.buckets) ? buckets.buckets : [];
    return { stats, buckets: app.values.state.buckets, core: core.blocks || [], caps, personal, coord: coord.status || {}, memories: memories.memories || [] };
  },
  render(data) {
    if (app.values.state.mode === "cinema") return app.cinema.renderCinemaOverview(data);
    const stats = data.stats || {};
    const byScope = stats.by_scope || {};
    const progress = stats.summary_progress || {};
    const storageMb = (app.common.int(stats.memory_storage_bytes) / 1048576).toFixed(1);
    const privateCount = app.common.int(byScope.private);
    const groupCount = app.common.int(byScope.group);

    const kpis = [
      app.widgets.kpi("记忆总量", app.common.fmtInt(stats.total_memories), storageMb + " MB 存储", "accent"),
      app.widgets.kpi("私聊记忆", app.common.fmtInt(privateCount), "一对一会话", "private"),
      app.widgets.kpi("群聊记忆", app.common.fmtInt(groupCount), "群窗口沉淀", "group"),
      app.widgets.kpi("用户档案", app.common.fmtInt(stats.identities), "已识别身份", "relation"),
      app.widgets.kpi("核心记忆", app.common.fmtInt((data.core || []).length), "强制注入块", "gold"),
      app.widgets.kpi("开放线程", app.common.fmtInt(stats.open_threads), "跨窗口续接", "fact"),
      app.widgets.kpi("原文已记录", app.common.fmtInt(progress.raw_events), "原始会话事件", "group"),
      app.widgets.kpi("会话记忆已生成", app.common.fmtInt(progress.conversation_memories), "不含待审核候选", "fact"),
      app.widgets.kpi("待处理批次", app.common.fmtInt(progress.pending_batches), "排队或延迟重试", "accent"),
      app.widgets.kpi("待修复批次", app.common.fmtInt(progress.quarantined_batches), "本批事件已冻结，需人工复核释放", "gold"),
      app.widgets.kpi("已处理，无新增记忆", app.common.fmtInt(progress.no_memory_batches), "原文保留，不重复总结", "private"),
    ].join("");

    const entries = [
      ["navigate", "记忆导航", "按私聊 / 群聊 / 个人等范围进入", "scope"],
      ["inspect", "记忆检视", "逐条查看并修正记忆参数", "db"],
      ["starmap", "知识星图", "以星系模型浏览关联记忆", "star"],
      ["microscope", "记忆显微镜", "模拟一次真实召回", "scope"],
      ["botlife", "Bot 日程与相册", "Bot 自身的生活记录", "camera"],
      ["config", "模块配置", "按模块调整检索与注入策略", "sliders"],
    ]
      .map(
        (item) =>
          '<button class="row" type="button" data-goto="' + item[0] + '">' +
          app.common.icon(item[3], "scope-icon") +
          '<div class="row-main"><div class="row-title">' + app.common.esc(item[1]) + "</div>" +
          '<div class="row-sub">' + app.common.esc(item[2]) + "</div></div>" +
          '<div class="row-meta"><span class="badge">进入 ›</span></div></button>'
      )
      .join("");

    const recent = (data.buckets || [])
      .slice()
      .sort((a, b) => String(b.last_activity_at || b.updated_at || "").localeCompare(String(a.last_activity_at || a.updated_at || "")))
      .slice(0, 8)
      .map((bucket) => {
        const scope = app.common.compact(bucket.scope);
        const meta = app.values.SCOPE_META[scope] || { label: scope || "未分类", tone: "" };
        return (
          '<button class="row" type="button" data-goto="inspect" data-scope="' + app.common.esc(scope) + '" data-target="' + app.common.esc(app.common.compact(bucket.target_id)) + '">' +
          '<div class="row-main"><div class="row-title">' + app.common.esc(app.common.compact(bucket.label) || app.common.compact(bucket.target_name) || app.common.compact(bucket.target_id) || "未命名窗口") + "</div>" +
          '<div class="row-sub">' + app.common.esc(app.common.compact(bucket.sample_session_id) || app.common.compact(bucket.target_id) || "-") + "</div></div>" +
          '<div class="row-meta">' + app.widgets.badge(meta.label, meta.tone) + app.widgets.badge(app.common.fmtInt(bucket.memory_count) + " 条") + "</div></button>"
        );
      })
      .join("");

    const bridges = [];
    const coordStatus = data.coord || {};
    const personalStatus = app.diagnostics.companionStatus(data.caps, data.personal);
    const bridgeHealth = app.common.compact(coordStatus.bridge && coordStatus.bridge.health) || "unknown";
    /* 联动要分三态，别把「没装」画成「坏了」：没装是与用户无关的中性灰点，
       装了但桥接降级才是需要去看的黄点，全红留给真出错。 */
    bridges.push([
      !personalStatus.installed ? "is-off" : bridgeHealth === "ready" ? "is-ok" : bridgeHealth === "degraded" ? "is-warn" : "is-bad",
      "陪伴插件联动",
      !personalStatus.installed
        ? "未安装陪伴插件"
        : bridgeHealth === "ready" ? "已连接" : app.diagnostics.bridgeReasonText(coordStatus.bridge && coordStatus.bridge.reason_code) || "未连接",
    ]);
    bridges.push([
      !personalStatus.installed ? "is-off" : personalStatus.available ? "is-ok" : "is-warn",
      "Bot 个人记忆",
      !personalStatus.installed
        ? "装上陪伴插件后可用"
        : personalStatus.available
          ? (personalStatus.dailyPlanEnabled ? "日程已启用" : "日程未启用") + " · " + (personalStatus.detailEnabled ? "细化已启用" : "细化未启用")
          : personalStatus.reason || "不可用",
    ]);
    const injectionLogs = Number(stats.injection_logs) || 0;
    bridges.push([
      injectionLogs > 0 ? "is-ok" : "is-off",
      "外部写入接口",
      injectionLogs > 0 ? injectionLogs + " 条注入日志" : "暂无注入日志",
    ]);
    const linkageNote = personalStatus.installed
      ? ""
      : '<p class="section-note">未检测到 astrbot_plugin_private_companion。<b>记忆功能不受影响</b>，'
        + '装上陪伴插件后这里会显示它的日程、相册与表达权威归属。'
        + '<button class="btn is-sm" type="button" data-goto="companion" style="margin-left:8px">查看联动页</button></p>';

    return (
      '<div class="grid" style="gap:16px">' +
      '<div class="kpi-row">' + kpis + "</div>" +
      '<div class="grid split-2">' +
        app.widgets.card("功能入口", "六个常用工作区", '<div class="row-list">' + entries + "</div>") +
        '<div style="display:grid;gap:16px">' +
          app.widgets.card("最近活跃范围", (data.buckets || []).length + " 个窗口", '<div class="row-list">' + (recent || app.widgets.emptyState("暂无范围", "还没有可展示的记忆窗口。")) + "</div>") +
          app.widgets.card(
            "联动状态",
            "陪伴插件与外部接口",
            '<div class="row-list" style="gap:6px">' +
              bridges.map((item) => '<div class="link-item ' + item[0] + '"><span class="link-dot"></span><b>' + app.common.esc(item[1]) + "</b><span>" + app.common.esc(item[2]) + "</span></div>").join("") +
              "</div>" + linkageNote
          ) +
        "</div>" +
      "</div>" +
      '<div class="grid g4">' +
        app.widgets.kpi("稳定记忆", app.common.fmtInt(stats.stable_memories), "lifecycle=stable_memory", "fact") +
        app.widgets.kpi("时间线事件", app.common.fmtInt(stats.timeline_events), "结构化事件流", "group") +
        app.widgets.kpi("关系边", app.common.fmtInt(stats.relationships), "实体间关系", "relation") +
        app.widgets.kpi("ACL 规则", app.common.fmtInt(stats.acl_rules), "跨窗口读写授权", "gold") +
      "</div>" +
      "</div>"
    );
  },
  mount(node) {
    const sceneRoot = app.values.$("#cinemaScene", node);
    if (sceneRoot) {
      const mountScene = () => window.MemoryCinema3D && window.MemoryCinema3D.mountArchiveScene(sceneRoot);
      if (window.MemoryCinema3D) mountScene();
      else window.addEventListener("memory-cinema-ready", mountScene, { once: true });
      if (!window.MemoryCinema3D) sceneRoot.dataset.waitingForScene = "true";
      /* Static module loading is rewritten by AstrBot to include its asset token. */
      window.setTimeout(() => {
        if (!window.MemoryCinema3D && sceneRoot.isConnected) sceneRoot.innerHTML = '<span class="scene-loading">ARCHIVE SCENE / UNAVAILABLE</span>';
      }, 8000);
    }
    const records = app.values.$$(".terminal-record", node);
    const search = app.values.$("#terminalSearch", node);
    const resultCount = app.values.$("#terminalResultCount", node);
    const channels = ["all", "session", "profile", "relationship", "core", "personal"];
    let selectedIndex = 0;
    let channelIndex = 0;
    const select = (index, focus = false) => {
      if (!records.length) return;
      selectedIndex = (index + records.length) % records.length;
      const record = records[selectedIndex];
      records.forEach((item) => item.classList.toggle("is-selected", item === record));
      if (sceneRoot) sceneRoot.dataset.selectedIndex = String(selectedIndex);
      const title = app.values.$("#terminalSelectedTitle", node);
      const meta = app.values.$("#terminalSelectedMeta", node);
      const id = app.values.$("#terminalFileId", node);
      const number = app.values.$("#terminalSelectedNumber", node);
      const category = app.values.$("#terminalCategory", node);
      const channel = app.values.$("#terminalChannel", node);
      if (title) title.textContent = app.values.$("strong", record)?.textContent || "等待档案";
      if (meta) meta.textContent = app.values.$(".terminal-record-meta", record)?.textContent || "MEMORY CHANNEL";
      if (id) id.textContent = "M-" + String(selectedIndex + 1).padStart(3, "0");
      if (number) number.textContent = String(selectedIndex + 1).padStart(2, "0");
      if (category) category.textContent = app.values.$(".terminal-record-channel", record)?.textContent || "记忆档案";
      if (channel) channel.textContent = app.values.$(".terminal-record-channel", record)?.textContent || "ALL RECORDS";
      if (focus) record.focus({ preventScroll: true });
    };
    const applySearch = () => {
      const query = app.common.compact(search?.value).toLowerCase();
      let visible = 0;
      records.forEach((record) => {
        const shown = !query || app.common.compact(record.dataset.terminalText).toLowerCase().includes(query);
        record.hidden = !shown;
        if (shown) visible += 1;
      });
      if (resultCount) resultCount.textContent = visible + " RECORDS";
    };
    search?.addEventListener("input", applySearch);
    records.forEach((record, index) => record.addEventListener("click", () => {
      select(index);
      if (record.dataset.memory) app.viewer.openMemory(record.dataset.memory);
      else if (record.dataset.core) app.navigation.go("core");
      else app.navigation.go("inspect");
    }));
    app.values.$("#terminalFileTitle", node)?.addEventListener("click", () => records[selectedIndex]?.click());
    app.values.$("#terminalAccess", node)?.addEventListener("click", () => records[selectedIndex]?.click());
    app.values.$("[data-terminal-prev]", node)?.addEventListener("click", () => select(selectedIndex - 1, true));
    app.values.$("[data-terminal-next]", node)?.addEventListener("click", () => select(selectedIndex + 1, true));
    app.values.$("[data-terminal-column-prev]", node)?.addEventListener("click", () => {
      channelIndex = (channelIndex + channels.length - 1) % channels.length;
      const target = channels[channelIndex];
      const index = target === "all" ? 0 : records.findIndex((record) => record.dataset.terminalChannel === target);
      if (index >= 0) select(index, true);
    });
    app.values.$("[data-terminal-column-next]", node)?.addEventListener("click", () => {
      channelIndex = (channelIndex + 1) % channels.length;
      const target = channels[channelIndex];
      const index = target === "all" ? 0 : records.findIndex((record) => record.dataset.terminalChannel === target);
      if (index >= 0) select(index, true);
    });
    applySearch();
    select(0);
  },
});
}
return { initialize };
}
