
// permissions: one page runtime owns shared state and DOM references.
export function createPermissions(app) {
function winKey(scope, id) {
  return app.common.compact(scope) + ":" + app.common.compact(id);
}

function winName(win) {
  return app.common.compact(win.label) || app.common.compact(win.target_name) || app.common.compact(win.id) || "未命名窗口";
}

function winColor(scope) {
  return app.common.compact(scope) === "private" ? "var(--c-private)" : app.common.compact(scope) === "group" ? "var(--c-group)" : "var(--text-3)";
}

function winTone(scope) {
  return app.common.compact(scope) === "private" ? "private" : app.common.compact(scope) === "group" ? "group" : "";
}

async function initialize() {
app.values.ACL_MODE_LABELS = { whitelist: "白名单", blacklist: "黑名单" };
app.values.SCOPE_SWITCHES = [
  ["private_capture_enabled", "私聊捕获"],
  ["group_capture_enabled", "群聊捕获"],
  ["private_recall_enabled", "私聊召回"],
  ["group_recall_enabled", "群聊召回"],
  ["private_topology_enabled", "私聊参与拓扑"],
  ["group_topology_enabled", "群聊参与拓扑"],
];
app.values.ACL_MAX_MATRIX = 8;
app.values.ACL_MAX_EXPANDED = 16;
app.values.aclState = { query: "", onlyLinked: false, selected: "", expanded: false };
app.navigation.defineView("acl", {
  title: "权限拓扑",
  navLabel: "权限拓扑",
  eyebrow: "Settings · ACL",
  hint: "谁可以读到哪个窗口的记忆 —— 授权矩阵、单个窗口策略与全局范围开关",
  async load() {
    const result = await app.api.apiTry(() => app.api.apiGet("/acl/matrix"), null);
    if (!result) return { error: "权限矩阵读取失败" };
    return result;
  },
  render(data) {
    if (data.error) return app.widgets.card("权限拓扑", "", app.widgets.emptyState("读取失败", data.error));

    const windows = Array.isArray(data.windows) ? data.windows : [];
    const rules = Array.isArray(data.rules) ? data.rules : [];
    const policies = Array.isArray(data.policies) ? data.policies : [];
    const scopeControl = data.scope_control || {};

    const ruleMap = {};
    rules.forEach((rule) => {
      ruleMap[winKey(rule.owner_scope, rule.owner_id) + ">" + winKey(rule.reader_scope, rule.reader_id)] = rule;
    });
    const policyMap = {};
    policies.forEach((policy) => {
      policyMap[winKey(policy.window_scope, policy.window_id)] = policy;
    });

    const outDeg = {};
    const inDeg = {};
    rules.forEach((rule) => {
      const owner = winKey(rule.owner_scope, rule.owner_id);
      const reader = winKey(rule.reader_scope, rule.reader_id);
      outDeg[owner] = (outDeg[owner] || 0) + 1;
      inDeg[reader] = (inDeg[reader] || 0) + 1;
    });

    const query = app.values.aclState.query.trim().toLowerCase();
    let candidates = windows.slice();
    if (app.values.aclState.onlyLinked) {
      candidates = candidates.filter(
        (win) => (outDeg[winKey(win.scope, win.id)] || 0) + (inDeg[winKey(win.scope, win.id)] || 0) > 0
      );
    }
    if (query) {
      candidates = candidates.filter((win) => (winName(win) + " " + app.common.compact(win.id)).toLowerCase().indexOf(query) >= 0);
    }
    candidates.sort((a, b) => app.common.int(b.memory_count) - app.common.int(a.memory_count));

    const cap = app.values.aclState.expanded ? app.values.ACL_MAX_EXPANDED : app.values.ACL_MAX_MATRIX;
    const shown = candidates.slice(0, cap);

    if (!shown.some((win) => winKey(win.scope, win.id) === app.values.aclState.selected)) {
      const ranked = shown.slice().sort((a, b) => {
        const ka = winKey(a.scope, a.id);
        const kb = winKey(b.scope, b.id);
        const diff = (outDeg[kb] || 0) + (inDeg[kb] || 0) - ((outDeg[ka] || 0) + (inDeg[ka] || 0));
        return diff !== 0 ? diff : app.common.int(b.memory_count) - app.common.int(a.memory_count);
      });
      app.values.aclState.selected = ranked.length ? winKey(ranked[0].scope, ranked[0].id) : "";
    }
    const selKey = app.values.aclState.selected;
    const selected = shown.find((win) => winKey(win.scope, win.id) === selKey) || null;

    /* ---------- 矩阵 ---------- */
    let matrix = "";
    if (!shown.length) {
      matrix = app.widgets.emptyState("没有可展示的窗口", "调整筛选条件，或先在记忆库里产生一些窗口。");
    } else {
      const cols = "grid-template-columns: 138px repeat(" + shown.length + ", 68px)";
      const headRow =
        '<div class="mx-corner">被读方 ＼ 读取方</div>' +
        shown
          .map(
            (win, index) =>
              '<div class="mx-ch" data-c="' + index + '"><b>' + app.common.esc(app.common.shortId(win.id)) + "</b><span>" +
              app.common.esc(app.common.clip(winName(win), 8)) + "</span></div>"
          )
          .join("");
      const bodyRows = shown
        .map((owner, r) => {
          const ownerKey = winKey(owner.scope, owner.id);
          const rowHead =
            '<button class="mx-rh' + (ownerKey === selKey ? " is-sel" : "") + '" type="button" data-r="' + r +
            '" data-pick="' + app.common.esc(ownerKey) + '"><i style="background:' + winColor(owner.scope) + '"></i><b>' +
            app.common.esc(winName(owner)) + "</b></button>";
          const cells = shown
            .map((reader, c) => {
              if (r === c) return '<div class="mx-cell" data-state="self">自身</div>';
              const rule = ruleMap[ownerKey + ">" + winKey(reader.scope, reader.id)];
              const state = rule ? (rule.effect === "deny" ? "deny" : "allow") : "";
              const glyph =
                state === "allow"
                  ? '<span class="dot"></span>'
                  : state === "deny"
                  ? '<span class="cross">×</span>'
                  : "";
              return (
                '<button class="mx-cell" type="button" data-state="' + state + '" data-r="' + r + '" data-c="' + c + '"' +
                ' data-owner-scope="' + app.common.esc(owner.scope) + '" data-owner-id="' + app.common.esc(owner.id) + '"' +
                ' data-reader-scope="' + app.common.esc(reader.scope) + '" data-reader-id="' + app.common.esc(reader.id) + '"' +
                (rule ? ' data-rule="' + app.common.esc(rule.id) + '"' : "") +
                ' aria-label="' + app.common.esc(winName(owner) + " 授权给 " + winName(reader)) + '">' + glyph + "</button>"
              );
            })
            .join("");
          return rowHead + cells;
        })
        .join("");
      matrix =
        '<div class="mx-scroll"><div class="mx" id="mxGrid" style="' + cols + '">' + headRow + bodyRows + "</div>" +
        '<div class="mx-tip" id="mxTip"></div></div>';
    }

    /* ---------- 授权密度 ---------- */
    const density = candidates
      .slice(0, 12)
      .map((win) => {
        const key = winKey(win.scope, win.id);
        return (
          '<button class="density-chip' + (key === selKey ? " is-sel" : "") + '" type="button" data-pick="' + app.common.esc(key) + '">' +
          app.common.esc(winName(win)) + "<em>出 " + app.common.int(outDeg[key]) + " · 入 " + app.common.int(inDeg[key]) + "</em></button>"
        );
      })
      .join("");

    /* ---------- 选中窗口 ---------- */
    let windowCard = "";
    if (selected) {
      const key = winKey(selected.scope, selected.id);
      const policy = policyMap[key] || null;
      const defaultMode = app.common.compact(selected.scope) === "group" ? "blacklist" : "whitelist";
      const readMode = app.common.compact(policy && policy.read_mode) || defaultMode;
      const shareMode = app.common.compact(policy && policy.share_mode) || defaultMode;
      const captureOn = policy && policy.capture_enabled === false ? false : true;
      const recallOn = policy && policy.recall_enabled === false ? false : true;

      const outbound = rules.filter((rule) => winKey(rule.owner_scope, rule.owner_id) === key);
      const inbound = rules.filter((rule) => winKey(rule.reader_scope, rule.reader_id) === key);

      const ruleRow = (rule, direction) =>
        '<div class="row" style="cursor:default"><div class="row-main">' +
        '<div class="row-title">' + app.common.esc(app.common.shortId(direction === "out" ? rule.reader_id : rule.owner_id)) + "</div>" +
        '<div class="row-sub">' + app.common.esc(app.values.SCOPE_META[app.common.compact(direction === "out" ? rule.reader_scope : rule.owner_scope)] ? app.values.SCOPE_META[app.common.compact(direction === "out" ? rule.reader_scope : rule.owner_scope)].label : app.common.compact(direction === "out" ? rule.reader_scope : rule.owner_scope)) + "</div></div>" +
        '<div class="row-meta">' +
        app.widgets.badge(rule.effect === "deny" ? "拒绝" : "允许", rule.effect === "deny" ? "danger" : "ok") +
        (rule.enabled ? "" : app.widgets.badge("停用", "warn")) +
        '<button class="btn is-sm is-ghost" type="button" data-del-acl="' + app.common.esc(rule.id) + '">删除</button>' +
        "</div></div>";

      const otherOptions = candidates
        .filter((win) => winKey(win.scope, win.id) !== key)
        .map(
          (win) =>
            '<option value="' + app.common.esc(winKey(win.scope, win.id)) + '">' + app.common.esc(winName(win)) + "（" +
            app.common.esc(app.values.SCOPE_META[app.common.compact(win.scope)] ? app.values.SCOPE_META[app.common.compact(win.scope)].label : app.common.compact(win.scope)) + "）</option>"
        )
        .join("");

      windowCard =
        app.widgets.card(
          "选中窗口",
          policy ? "已单独设置策略" : "沿用范围默认",
          '<div style="display:flex;align-items:center;gap:9px;margin-bottom:12px">' +
            '<span style="width:8px;height:8px;border-radius:99px;background:' + winColor(selected.scope) + ';flex:none"></span>' +
            '<div style="min-width:0"><div style="font-size:14px;font-weight:620">' + app.common.esc(winName(selected)) + "</div>" +
            '<div class="mono" style="font-size:11px;color:var(--text-3)">' + app.common.esc(app.common.compact(selected.id)) + " · " + app.common.fmtInt(selected.memory_count) + " 条记忆</div></div>" +
            app.widgets.badge(app.values.SCOPE_META[app.common.compact(selected.scope)] ? app.values.SCOPE_META[app.common.compact(selected.scope)].label : app.common.compact(selected.scope), winTone(selected.scope)) +
          "</div>" +
          '<div class="section-label" style="margin:2px 0 8px"><h2 style="font-size:11px">出站授权</h2><span class="section-note">谁可以读这里的记忆 · ' + outbound.length + " 条</span></div>" +
          (outbound.length
            ? '<div class="row-list">' + outbound.map((rule) => ruleRow(rule, "out")).join("") + "</div>"
            : '<p class="section-note" style="margin-bottom:10px">还没有把这里的记忆授权给任何窗口。</p>') +
          '<div class="section-label" style="margin:14px 0 8px"><h2 style="font-size:11px">入站授权</h2><span class="section-note">这里能读到哪些记忆 · ' + inbound.length + " 条</span></div>" +
          (inbound.length
            ? '<div class="row-list">' + inbound.map((rule) => ruleRow(rule, "in")).join("") + "</div>"
            : '<p class="section-note" style="margin-bottom:10px">这里还不能读到其他窗口的记忆。</p>') +
          '<div class="section-label" style="margin:14px 0 8px"><h2 style="font-size:11px">快速授权</h2></div>' +
          '<div style="display:grid;grid-template-columns:minmax(0,1fr) 96px auto;gap:7px;align-items:center">' +
            '<select id="aclAddTarget" style="height:32px;padding:0 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px">' +
            (otherOptions || '<option value="">没有其他窗口</option>') + "</select>" +
            '<select id="aclAddEffect" style="height:32px;padding:0 9px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px">' +
              '<option value="allow">允许</option><option value="deny">拒绝</option>' +
            "</select>" +
            '<button class="btn is-sm is-primary" type="button" id="aclAddBtn">添加</button>' +
          "</div>" +
          '<p class="section-note" style="margin-top:7px">等价于在矩阵里点这一格：从「本窗口」授权给所选窗口。</p>'
        ) +
        app.widgets.card(
          "窗口策略",
          policy ? "已单独设置" : "沿用范围默认（" + app.values.ACL_MODE_LABELS[defaultMode] + "）",
          '<div class="config-form">' +
            '<div class="field is-inline"><span>读取模式</span><select id="aclReadMode">' +
              Object.keys(app.values.ACL_MODE_LABELS).map((key) => '<option value="' + key + '"' + (readMode === key ? " selected" : "") + ">" + app.values.ACL_MODE_LABELS[key] + "</option>").join("") +
            "</select></div>" +
            '<div class="field is-inline"><span>共享模式</span><select id="aclShareMode">' +
              Object.keys(app.values.ACL_MODE_LABELS).map((key) => '<option value="' + key + '"' + (shareMode === key ? " selected" : "") + ">" + app.values.ACL_MODE_LABELS[key] + "</option>").join("") +
            "</select></div>" +
            '<label class="switch"><input type="checkbox" id="aclCapture"' + (captureOn ? " checked" : "") + ' /><span class="switch-track"></span><span>捕获新记忆</span></label>' +
            '<label class="switch"><input type="checkbox" id="aclRecall"' + (recallOn ? " checked" : "") + ' /><span class="switch-track"></span><span>参与召回</span></label>' +
            '<div class="pill-row" style="justify-content:flex-end">' +
              '<button class="btn is-sm is-primary" type="button" id="aclPolicySave">保存策略</button>' +
            "</div>" +
          "</div>"
        );
    } else {
      windowCard = app.widgets.card("选中窗口", "", app.widgets.emptyState("没有可选窗口", "先在左侧矩阵里选择一个窗口。")) +
        app.widgets.card("窗口策略", "", app.widgets.emptyState("无策略", "选择窗口后可单独设置读写模式。"));
    }

    /* ---------- 范围开关 ---------- */
    const scopeGrid =
      '<div class="grid g3" style="gap:10px 14px">' +
      app.values.SCOPE_SWITCHES.map(
        (row) =>
          '<label class="switch"><input type="checkbox" data-scope-control="' + row[0] + '"' +
          (scopeControl[row[0]] ? " checked" : "") + ' /><span class="switch-track"></span><span>' + app.common.esc(row[1]) + "</span></label>"
      ).join("") +
      "</div>";

    const denyCount = rules.filter((rule) => rule.effect === "deny").length;

    return (
      '<div class="grid" style="gap:14px">' +
      '<div class="kpi-row">' +
        app.widgets.kpi("参与拓扑的窗口", app.common.fmtInt(windows.length), "矩阵展示 " + app.common.fmtInt(shown.length) + " 个", "fact") +
        app.widgets.kpi("授权规则", app.common.fmtInt(rules.length), "跨窗口读写授权", "gold") +
        app.widgets.kpi("其中拒绝", app.common.fmtInt(denyCount), "显式禁止读取", denyCount ? "relation" : "fact") +
        app.widgets.kpi("窗口策略", app.common.fmtInt(policies.length), "未设置走范围默认", "accent") +
      "</div>" +
      '<div class="grid split-2">' +
        app.widgets.card(
          "授权矩阵",
          "行＝被读方 · 列＝读取方",
          '<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:11px">' +
            '<div class="pill-row">' +
              '<button class="pill' + (app.values.aclState.onlyLinked ? "" : " is-active") + '" type="button" data-acl-filter="all">全部窗口</button>' +
              '<button class="pill' + (app.values.aclState.onlyLinked ? " is-active" : "") + '" type="button" data-acl-filter="linked">仅显示有规则</button>' +
            "</div>" +
            '<input id="aclQuery" type="search" placeholder="搜索窗口名或 ID" value="' + app.common.esc(app.values.aclState.query) +
              '" style="flex:1;min-width:150px;height:30px;padding:0 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);font-size:12px;outline:none" />' +
            (candidates.length > shown.length || app.values.aclState.expanded
              ? '<button class="pill" type="button" data-acl-filter="expand">' +
                (app.values.aclState.expanded ? "收起" : "显示全部 " + app.common.fmtInt(candidates.length) + " 个 ›") + "</button>"
              : "") +
          "</div>" +
          matrix +
          '<div class="pill-row" style="margin-top:11px;font-size:11px;color:var(--text-3)">' +
            '<span class="badge" data-tone="ok">● 允许读取</span>' +
            '<span class="badge" data-tone="danger">× 拒绝读取</span>' +
            '<span class="badge">空 未显式授权</span>' +
            '<span class="badge">自身 同窗口无需授权</span>' +
          "</div>" +
          '<p class="section-note" style="margin-top:9px">点击格子循环切换：未授权 → 允许 → 拒绝 → 未授权。灰色空格不代表禁止，由两侧窗口策略共同判定。</p>' +
          (density
            ? '<div class="section-label" style="margin:16px 0 8px"><h2 style="font-size:11px">窗口授权密度</h2><span class="section-note">出站 / 入站</span></div>' +
              '<div class="density-wrap">' + density + "</div>"
            : "")
        ) +
        '<div style="display:grid;gap:14px;align-content:start">' + windowCard + "</div>" +
      "</div>" +
      app.widgets.card(
        "范围开关",
        "关闭只停用对应能力，不删除已有记忆；参与拓扑关闭后，该范围的窗口不再出现在矩阵里",
        scopeGrid,
        '<button class="btn is-sm is-primary" type="button" id="aclScopeSave">保存范围开关</button>'
      ) +
      "</div>"
    );
  },
  mount(node) {
    const grid = app.values.$("#mxGrid", node);
    const tip = app.values.$("#mxTip", node);

    if (grid) {
      const clearHot = () => {
        app.values.$$(".is-hot", grid).forEach((item) => item.classList.remove("is-hot"));
      };
      grid.addEventListener("mouseover", (event) => {
        const cell = event.target.closest(".mx-cell");
        if (!cell || cell.dataset.state === "self") {
          clearHot();
          if (tip) tip.classList.remove("is-on");
          return;
        }
        const r = cell.dataset.r;
        const c = cell.dataset.c;
        clearHot();
        app.values.$$('.mx-cell[data-r="' + r + '"], .mx-cell[data-c="' + c + '"]', grid).forEach((item) =>
          item.classList.add("is-hot")
        );
        const rowHead = app.values.$('.mx-rh[data-r="' + r + '"]', grid);
        if (rowHead) rowHead.classList.add("is-hot");
        const colHead = app.values.$('.mx-ch[data-c="' + c + '"]', grid);
        if (colHead) colHead.classList.add("is-hot");
        cell.classList.add("is-hot");
        if (tip) {
          const state = cell.dataset.state;
          const now =
            state === "allow"
              ? "当前：允许读取"
              : state === "deny"
              ? "当前：拒绝读取"
              : "当前：未显式授权（按双方策略判定）";
          const next =
            state === "allow" ? "点击改为「拒绝」" : state === "deny" ? "点击「清除规则」" : "点击授权「允许」";
          tip.innerHTML =
            '<div class="tip-title">' + app.common.esc(cell.getAttribute("aria-label") || "") + "</div>" +
            '<div class="tip-now">' + app.common.esc(now) + "</div>" +
            '<div class="tip-hint">' + app.common.esc(next) + "</div>";
          tip.style.left = cell.offsetLeft + cell.offsetWidth / 2 + "px";
          tip.style.top = cell.offsetTop + "px";
          tip.classList.add("is-on");
        }
      });
      grid.addEventListener("mouseleave", () => {
        clearHot();
        if (tip) tip.classList.remove("is-on");
      });
    }

    const cycle = async (cell) => {
      const payload = {
        owner_scope: cell.dataset.ownerScope,
        owner_id: cell.dataset.ownerId,
        reader_scope: cell.dataset.readerScope,
        reader_id: cell.dataset.readerId,
      };
      const state = cell.dataset.state;
      const ruleId = app.common.compact(cell.dataset.rule);
      if (state === "allow") {
        await app.api.apiPost("/acl/upsert", Object.assign({ effect: "deny", enabled: true }, payload));
        app.shell.toast("已改为拒绝读取", "ok");
      } else if (state === "deny") {
        if (!ruleId) {
          app.shell.toast("找不到对应规则，请刷新后重试", "error");
          return;
        }
        await app.api.apiPost("/acl/delete", { id: ruleId });
        app.shell.toast("已清除授权规则", "ok");
      } else {
        await app.api.apiPost("/acl/upsert", Object.assign({ effect: "allow", enabled: true }, payload));
        app.shell.toast("已授权读取", "ok");
      }
      app.pool.invalidatePool();
      app.navigation.go("acl");
    };

    app.values.$$(".mx-cell[data-owner-id]", node).forEach((cell) => {
      cell.addEventListener("click", async () => {
        await app.shell.withBusy("正在更新授权…", () => cycle(cell));
      });
    });

    app.values.$$("[data-pick]", node).forEach((button) => {
      button.addEventListener("click", () => {
        app.values.aclState.selected = button.dataset.pick;
        app.navigation.go("acl");
      });
    });

    app.values.$$("[data-acl-filter]", node).forEach((button) => {
      button.addEventListener("click", () => {
        const mode = button.dataset.aclFilter;
        if (mode === "all") app.values.aclState.onlyLinked = false;
        else if (mode === "linked") app.values.aclState.onlyLinked = true;
        else if (mode === "expand") app.values.aclState.expanded = !app.values.aclState.expanded;
        app.navigation.go("acl");
      });
    });

    const queryInput = app.values.$("#aclQuery", node);
    if (queryInput) {
      const apply = () => {
        app.values.aclState.query = queryInput.value.trim();
        app.navigation.go("acl");
      };
      queryInput.addEventListener("change", apply);
      queryInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") apply();
      });
    }

    app.values.$$("[data-del-acl]", node).forEach((button) => {
      button.addEventListener("click", async () => {
        await app.shell.withBusy("正在删除…", async () => {
          await app.api.apiPost("/acl/delete", { id: button.dataset.delAcl });
          app.pool.invalidatePool();
          app.shell.toast("规则已删除", "ok");
          app.navigation.go("acl");
        });
      });
    });

    const addBtn = app.values.$("#aclAddBtn", node);
    if (addBtn) {
      addBtn.addEventListener("click", async () => {
        const target = app.values.$("#aclAddTarget", node).value;
        const effect = app.values.$("#aclAddEffect", node).value;
        const parts = target.split(":");
        if (parts.length < 2) {
          app.shell.toast("请先选择目标窗口", "error");
          return;
        }
        const current = app.common.compact(app.values.aclState.selected).split(":");
        await app.shell.withBusy("正在添加…", async () => {
          await app.api.apiPost("/acl/upsert", {
            owner_scope: current[0] || "",
            owner_id: current.slice(1).join(":") || "",
            reader_scope: parts[0] || "",
            reader_id: parts.slice(1).join(":") || "",
            effect,
            enabled: true,
          });
          app.pool.invalidatePool();
          app.shell.toast("授权已添加", "ok");
          app.navigation.go("acl");
        });
      });
    }

    const policySave = app.values.$("#aclPolicySave", node);
    if (policySave) {
      policySave.addEventListener("click", async () => {
        const current = app.common.compact(app.values.aclState.selected).split(":");
        await app.shell.withBusy("正在保存…", async () => {
          await app.api.apiPost("/acl/policy", {
            window_scope: current[0] || "",
            window_id: current.slice(1).join(":") || "",
            read_mode: app.values.$("#aclReadMode", node).value,
            share_mode: app.values.$("#aclShareMode", node).value,
            capture_enabled: app.values.$("#aclCapture", node).checked,
            recall_enabled: app.values.$("#aclRecall", node).checked,
          });
          app.pool.invalidatePool();
          app.shell.toast("窗口策略已保存", "ok");
          app.navigation.go("acl");
        });
      });
    }

    const scopeSave = app.values.$("#aclScopeSave", node);
    if (scopeSave) {
      scopeSave.addEventListener("click", async () => {
        const values = {};
        app.values.SCOPE_SWITCHES.forEach((row) => {
          const input = app.values.$('[data-scope-control="' + row[0] + '"]', node);
          if (input) values[row[0]] = input.checked;
        });
        await app.shell.withBusy("正在保存…", async () => {
          await app.api.apiPost("/config/module/update", { module: "scope_control", values });
          app.pool.invalidatePool();
          app.shell.toast("范围开关已保存", "ok");
          app.navigation.go("acl");
        });
      });
    }
  },
});
}
return { winKey, winName, winColor, winTone, initialize };
}
