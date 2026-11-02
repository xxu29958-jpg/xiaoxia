
// widgets: one page runtime owns shared state and DOM references.
export function createWidgets(app) {
function kpi(label, value, foot, tone) {
  return (
    '<article class="kpi"' + (tone ? ' data-tone="' + tone + '"' : "") + ">" +
    '<span class="kpi-label">' + app.common.esc(label) + "</span>" +
    '<span class="kpi-value">' + app.common.esc(value) + "</span>" +
    (foot ? '<span class="kpi-foot">' + app.common.esc(foot) + "</span>" : "") +
    "</article>"
  );
}

function badge(text, tone) {
  return '<span class="badge' + (tone ? '" data-tone="' + tone : "") + '">' + app.common.esc(text) + "</span>";
}

function emptyState(title, text) {
  return '<div class="empty"><b>' + app.common.esc(title) + "</b><span>" + app.common.esc(text) + "</span></div>";
}

function card(title, sub, bodyHtml, actionsHtml, extraClass) {
  return (
    '<section class="card ' + (extraClass || "") + '">' +
    '<div class="card-head"><div><h3>' + app.common.esc(title) + "</h3>" +
    (sub ? '<div class="card-sub">' + app.common.esc(sub) + "</div>" : "") +
    "</div>" +
    (actionsHtml ? '<div class="card-head-actions">' + actionsHtml + "</div>" : "") +
    "</div>" +
    (bodyHtml || "") +
    "</section>"
  );
}

function meter(label, value, max) {
  const pct = app.common.clamp(((Number(value) || 0) / (Number(max) || 1)) * 100, 0, 100);
  return (
    '<div class="meter"><div class="meter-head"><span>' + app.common.esc(label) + "</span><b>" + app.common.esc(app.common.num(value)) + "</b></div>" +
    '<div class="meter-track"><div class="meter-fill" style="width:' + pct.toFixed(1) + '%"></div></div></div>'
  );
}
return { kpi, badge, emptyState, card, meter };
}
