
// botLife: one page runtime owns shared state and DOM references.
export function createBotLife(app) {
async function initialize() {
app.values.botLifeDate = "";
app.navigation.defineView("botlife", {
  title: "Bot 日程与相册",
  navLabel: "Bot 日程与相册",
  eyebrow: "Library · Bot Self",
  hint: "Bot 自己的一天：日程安排、每日状态、相册、主观记忆与细化片段",
  async load(options) {
    const opts = options || {};
    if (opts.date) app.values.botLifeDate = opts.date;
    const params = app.values.botLifeDate ? "?date=" + encodeURIComponent(app.values.botLifeDate) + "&limit=80" : "?limit=80";
    const result = await app.api.apiTry(() => app.api.apiGet("/companion/personal-memory" + params), { available: false });
    if (result.available === false) {
      const caps = await app.api.apiTry(() => app.api.apiGet("/capabilities/bot-personal"), null);
      return { available: false, reason: app.common.compact(result.reason) || "未检测到主动陪伴插件", caps };
    }
    if (result.selected_date) app.values.botLifeDate = result.selected_date;
    return Object.assign({ available: true }, result);
  },
  render(data) {
    if (!data.available) {
      return (
        '<div class="grid g3">' +
        app.widgets.card(
          "Bot 个人记忆不可用",
          "需要主动陪伴插件",
          app.widgets.emptyState("未检测到陪伴插件", app.common.compact(data.reason) || "安装并启用 astrbot_plugin_private_companion 后，这里会显示 Bot 的日程、相册与主观记忆。")
        ) +
        "</div>"
      );
    }
    const snap = data.snapshot || {};
    const plan = snap.plan || {};
    const items = Array.isArray(plan.items) ? plan.items : [];
    const current = snap.current_item || {};
    const dailyState = snap.daily_state || {};
    const album = Array.isArray(snap.album) ? snap.album : [];
    const subjective = Array.isArray(snap.subjective_memories) ? snap.subjective_memories : [];
    const details = Array.isArray(snap.details) ? snap.details : [];
    const dates = Array.isArray(data.dates) ? data.dates : [];

    const datePills = dates
      .slice(0, 14)
      .map((d) => '<button class="pill' + (d === data.selected_date ? " is-active" : "") + '" type="button" data-date="' + app.common.esc(d) + '">' + app.common.esc(app.common.fmtDate(d)) + "</button>")
      .join("");

    const schedule = items.length
      ? '<div class="schedule-list">' +
        items
          .map((item) => {
            const isNow = app.common.compact(item.time) && app.common.compact(current.time) === app.common.compact(item.time);
            return (
              '<div class="schedule-item' + (isNow ? " is-now" : "") + '">' +
              '<div class="schedule-time">' + app.common.esc(app.common.compact(item.time) || "--:--") + "</div>" +
              "<div><div class=\"schedule-title\">" + app.common.esc(app.common.compact(item.activity) || "未安排") +
              (app.common.compact(item.mood) ? " · " + app.common.esc(item.mood) : "") + "</div>" +
              (app.common.compact(item.message_seed) ? '<div class="schedule-note">' + app.common.esc(app.common.clip(item.message_seed, 80)) + "</div>" : "") +
              "</div></div>"
            );
          })
          .join("") +
        "</div>"
      : app.widgets.emptyState("这一天还没有日程", "陪伴插件未生成当日计划，或该日期尚无记录。");

    const stateRows = [
      ["日期", app.common.compact(dailyState.date) || data.selected_date],
      ["精力", app.common.compact(dailyState.energy)],
      ["心情倾向", app.common.compact(dailyState.mood_bias)],
      ["睡眠", app.common.compact(dailyState.sleep)],
      ["天气", app.common.compact(dailyState.weather)],
      ["备注", app.common.compact(dailyState.note)],
    ].filter((row) => row[1]);

    const albumHtml = album.length
      ? '<div class="album-grid">' +
        album
          .map(
            (photo) =>
              photo.exists
                ? '<figure class="album-shot" data-cap="' + app.common.esc(app.common.compact(photo.title) + " · " + app.common.compact(photo.generated_at)) + '" style="margin:0">' +
                  '<img src="' + app.values.TRANSPARENT_IMAGE + '" data-album-image-src="' + app.common.esc(app.common.compact(photo.image_data_url) || app.common.compact(photo.url)) + '" alt="' + app.common.esc(app.common.compact(photo.title)) + '" loading="lazy" />' +
                  '<figcaption class="shot-cap"><b>' + app.common.esc(app.common.compact(photo.title)) + "</b><span>" + app.common.esc(app.common.compact(photo.generated_at) || app.common.compact(photo.date)) + "</span></figcaption></figure>"
                : '<div class="album-shot is-missing">' + app.common.esc(app.common.compact(photo.error) || "图片不可用") + "</div>"
          )
          .join("") +
        "</div>"
      : app.widgets.emptyState("相册还是空的", "Bot 生成每日穿搭图或生活分享图后，会出现在这里。");

    const subjectiveHtml = subjective.length
      ? '<div class="row-list">' +
        subjective
          .map(
            (item) =>
              '<div class="row" style="cursor:default;align-items:flex-start"><div class="row-main">' +
              '<div class="row-title">' + app.common.esc(app.common.compact(item.summary) || "主观记忆") + "</div>" +
              '<div class="row-sub" style="white-space:normal">' + app.common.esc(app.common.clip(app.common.compact(item.body), 150)) + "</div>" +
              (Array.isArray(item.tags) && item.tags.length ? '<div class="tag-row" style="margin-top:6px">' + item.tags.map((tag) => app.widgets.badge(tag, "personal")).join("") + "</div>" : "") +
              "</div></div>"
          )
          .join("") +
        "</div>"
      : app.widgets.emptyState("暂无主观记忆", "Bot 的日记与梦境碎片会汇总在这里。");

    const detailsHtml = details.length
      ? '<div class="row-list">' +
        details
          .map(
            (item) =>
              '<div class="row" style="cursor:default;align-items:flex-start"><div class="row-main">' +
              '<div class="row-title">' + app.common.esc(app.common.compact(item.time) || app.common.compact(item.key) || "片段") + " · " + app.common.esc(app.common.compact(item.status) || "进行中") + "</div>" +
              '<div class="row-sub" style="white-space:normal">' + app.common.esc(app.common.clip(app.common.compact(item.summary) || (item.today_events || []).join(" / "), 150)) + "</div>" +
              "</div></div>"
          )
          .join("") +
        "</div>"
      : app.widgets.emptyState("暂无细化片段", "启用陪伴插件的细化增强后，会按时间段记录 Bot 的具体经历。");

    return (
      '<div class="grid" style="gap:16px">' +
      '<div class="section-label"><h2>' + app.common.esc(app.common.compact(snap.bot_name) || "Bot") + " 的这一天</h2>" +
        '<span class="section-note">' + app.common.esc(app.common.compact(data.selected_date) || "") + (app.common.compact(plan.source) ? " · 来源 " + app.common.compact(plan.source) : "") + "</span></div>" +
      '<div class="pill-row">' + (datePills || '<span class="badge">仅此一天</span>') + "</div>" +
      '<div class="grid split-2">' +
        app.widgets.card("当日日程", items.length + " 个时段" + (app.common.compact(current.activity) ? " · 当前：" + app.common.compact(current.activity) : ""), schedule) +
        '<div style="display:grid;gap:16px">' +
          app.widgets.card("每日状态", "Bot 自身状态快照", stateRows.length ? '<dl class="kv">' + stateRows.map((row) => "<dt>" + app.common.esc(row[0]) + "</dt><dd>" + app.common.esc(row[1]) + "</dd>").join("") + "</dl>" : app.widgets.emptyState("暂无状态", "陪伴插件尚未写入当日状态。")) +
          app.widgets.card("相册", album.length + " 张", albumHtml) +
        "</div>" +
      "</div>" +
      '<div class="grid g2">' +
        app.widgets.card("主观记忆", "日记 · 梦境 · 感受", subjectiveHtml) +
        app.widgets.card("细化片段", details.length + " 段", detailsHtml) +
      "</div>" +
      "</div>"
    );
  },
  mount(node) {
    app.photos.hydratePersonalAlbumImages(node);
    app.values.$$("[data-date]", node).forEach((button) => {
      button.addEventListener("click", () => app.navigation.go("botlife", { date: button.dataset.date }));
    });
    app.values.$$(".album-shot:not(.is-missing)", node).forEach((figure) => {
      figure.addEventListener("click", () => {
        const image = app.values.$("img", figure);
        const source = image && image.dataset.loaded === "1" ? image.currentSrc || image.src : "";
        if (source) app.photos.openLightbox(source, figure.dataset.cap);
      });
    });
  },
});
}
return { initialize };
}
